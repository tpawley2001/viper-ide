"""Microsoft Copilot in its own window: no API, Viper types into copilot.microsoft.com and reads the reply.

The window is a small browser with its own saved profile, so you sign in to your Microsoft account
once and Viper stays signed in. Asking means: (optionally) start a new Copilot chat, put the prompt
in the message box, press send, then watch the newest Copilot reply until it stops changing,
turning its HTML back into Markdown (code blocks included) so edit blocks survive.

Copilot's page changes without notice, so every page-specific selector lives in ``SELECTORS``
and ``dump_page()`` saves the live DOM for fixing them.
"""
from __future__ import annotations

import json
import time

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from . import paths

COPILOT_URL = "https://copilot.microsoft.com/"
# Copilot renders Markdown, which would turn ``=======`` into a heading and ``>>>>>>>`` into quotes.
PROMPT_NOTE = ("\n\nThis chat renders Markdown, so put every SEARCH/REPLACE edit block and every NEW FILE block "
               "inside its own ```text code fence (one fence per block), exactly as specified above.")
# Microsoft's sign-in treats an unknown embedded browser with suspicion; present as plain Edge.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0")

SELECTORS = {
    "input": "textarea#userInput, textarea[data-testid='composer-input'], textarea",
    "send": "button[data-testid='submit-button'], button[aria-label*='Submit' i], button[aria-label*='Send' i]",
    "stop": "button[data-testid='stop-button'], button[aria-label*='Stop' i]",
    "reply": "[data-content='ai-message'], [data-testid='ai-message'], [data-author='bot']",
    "signed_out": "[data-testid='anonymous-block-page-title']",
}

POLL_MS = 400
STABLE_POLLS = 6          # reply unchanged this many polls (and no stop button) = finished
READY_TIMEOUT = 60.0
REPLY_TIMEOUT = 600.0

# Installed once per page; everything Viper does goes through window.__viper.
_JS = r"""
(() => {
  const S = %SELECTORS%;
  const q = (sel) => document.querySelector(sel);
  const visible = (el) => !!el && el.offsetParent !== null && !el.disabled;
  function md(node) {
    let out = '';
    for (const n of node.childNodes) {
      if (n.nodeType === 3) { out += n.textContent; continue; }
      if (n.nodeType !== 1) continue;
      const t = n.tagName;
      if (t === 'PRE') {
        const code = n.querySelector('code') || n;
        const lang = ((code.className || '') + '').match(/language-([\w+#.-]+)/);
        out += '\n```' + (lang ? lang[1] : '') + '\n' + code.innerText.replace(/\n$/, '') + '\n```\n';
      } else if (t === 'CODE') out += '`' + n.textContent + '`';
      else if (/^H[1-6]$/.test(t)) out += '\n' + '#'.repeat(+t[1]) + ' ' + md(n).trim() + '\n\n';
      else if (t === 'P' || t === 'BLOCKQUOTE') out += md(n).trim() + '\n\n';
      else if (t === 'BR') out += '\n';
      else if (t === 'LI') out += '- ' + md(n).trim() + '\n';
      else if (t === 'UL' || t === 'OL') out += '\n' + md(n) + '\n';
      else if (t === 'STRONG' || t === 'B') out += '**' + md(n) + '**';
      else if (t === 'EM' || t === 'I') out += '_' + md(n) + '_';
      else if (t === 'A') out += '[' + md(n) + '](' + n.href + ')';
      else if (t === 'BUTTON' || t === 'svg' || t === 'STYLE' || t === 'SCRIPT' || t === 'TEMPLATE' ||
               n.getAttribute('aria-hidden') === 'true') {}
      else out += md(n);
    }
    return out;
  }
  window.__viper = {
    state() {
      const input = q(S.input);
      return {signedOut: !!q(S.signed_out), ready: visible(input), busy: visible(q(S.stop)),
              replies: document.querySelectorAll(S.reply).length,
              maxLength: input ? input.maxLength : -1, url: location.href};
    },
    send(text) {
      const input = q(S.input);
      if (!input) return 'no message box';
      input.focus();
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(input, text);
      input.dispatchEvent(new Event('input', {bubbles: true}));
      return input.value === text ? 'ok' : 'the message box rejected the text';
    },
    submit() {
      const b = q(S.send);
      if (visible(b)) { b.click(); return 'clicked'; }
      const input = q(S.input);
      for (const type of ['keydown', 'keypress', 'keyup'])
        input.dispatchEvent(new KeyboardEvent(type, {key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                                                     bubbles: true, cancelable: true}));
      return 'enter';
    },
    stop() { const b = q(S.stop); if (visible(b)) b.click(); },
    read(before) {
      const all = document.querySelectorAll(S.reply);
      const busy = visible(q(S.stop));
      if (all.length <= before) return {text: null, busy, count: all.length};
      const last = all[all.length - 1];
      return {text: md(last).replace(/\n{3,}/g, '\n\n').trim(), busy, count: all.length};
    },
  };
  return true;
})()
"""


def prepare_app() -> None:
    """Call before the QApplication exists: Qt WebEngine can only be loaded later if this is set."""
    from PyQt6.QtCore import QCoreApplication
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def _script() -> str:
    return _JS.replace("%SELECTORS%", json.dumps(SELECTORS))


class CopilotError(Exception):
    pass


class CopilotWindow(QWidget):
    """The Copilot browser window plus the driver that asks it questions."""

    def __init__(self, url: str = COPILOT_URL, storage=None):
        try:
            from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
            from PyQt6.QtWebEngineWidgets import QWebEngineView
        except ImportError as e:  # a build without Qt WebEngine
            raise CopilotError(f"This copy of Viper can't open the Copilot window (Qt WebEngine missing: {e}).") \
                from None
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("Microsoft Copilot - Viper IDE")
        self.resize(980, 820)
        self.url = url
        store = storage or (paths.data_dir() / "copilot-web")
        self.profile = QWebEngineProfile("viper-copilot", self)
        self.profile.setPersistentStoragePath(str(store))
        self.profile.setCachePath(str(store / "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.setHttpUserAgent(USER_AGENT)
        owner = self
        self.popups: list = []

        class Page(QWebEnginePage):
            def createWindow(self, _type):  # sign-in popups (window.open) get a window of their own
                return owner._popup(QWebEnginePage, QWebEngineView)

        self.page = Page(self.profile, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)

        bar = QHBoxLayout()
        for text, tip, slot in (("New Chat", "Start a fresh Copilot conversation", self.new_chat),
                                ("Reload", "Reload the page", self.view.reload)):
            b = QToolButton(text=text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        self.status = QLabel("Sign in with your Microsoft account here once; Viper then sends your requests "
                             "to Copilot in this window and reads the replies back.")
        self.status.setObjectName("Dim")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)

        self._job: dict | None = None
        self._timer = QTimer(self, interval=POLL_MS)
        self._timer.timeout.connect(self._tick)
        self.view.load(QUrl(url))

    # ------------------------------------------------------------- helpers
    def _js(self, code: str, callback=None) -> None:
        full = f"(window.__viper || {_script()}, {code})"
        if callback is None:
            self.page.runJavaScript(full)
        else:
            self.page.runJavaScript(full, callback)

    def _popup(self, page_cls, view_cls):
        win = QWidget(None, Qt.WindowType.Window)
        win.setWindowTitle("Sign in - Microsoft Copilot")
        win.resize(560, 720)
        page = page_cls(self.profile, win)
        view = view_cls(win)
        view.setPage(page)
        lay = QVBoxLayout(win)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(view)
        entry = (win, view, page)
        self.popups.append(entry)
        page.titleChanged.connect(lambda t: win.setWindowTitle(t or "Sign in - Microsoft Copilot"))
        page.windowCloseRequested.connect(lambda: self._close_popup(entry))
        win.show()
        return page

    def _close_popup(self, entry) -> None:
        from PyQt6 import sip

        if entry in self.popups:
            self.popups.remove(entry)
            win, view, page = entry
            win.hide()
            for obj in (view, page, win):  # page before the profile, always
                if not sip.isdeleted(obj):
                    sip.delete(obj)
        self.view.reload()  # pick up the new sign-in

    def new_chat(self) -> None:
        self.view.load(QUrl(self.url))

    def bring_up(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def dump_page(self, path) -> None:
        """Save the live page HTML (for fixing SELECTORS after Copilot changes its page)."""
        self.page.runJavaScript("document.documentElement.outerHTML",
                                lambda html: open(path, "w", encoding="utf-8").write(html or ""))

    def dispose(self) -> None:
        """Tear down in the order WebEngine requires (view, then page, then profile), or Qt crashes on exit."""
        from PyQt6 import sip

        if sip.isdeleted(self) or sip.isdeleted(self.profile):
            return
        self._timer.stop()
        self._job = None
        for entry in list(self.popups):
            self.popups.remove(entry)
            for obj in entry[1:] + entry[:1]:
                if not sip.isdeleted(obj):
                    sip.delete(obj)
        self.close()
        for obj in (self.view, self.page, self.profile):
            if not sip.isdeleted(obj):
                sip.delete(obj)
        self.deleteLater()

    def _diagnose(self) -> str:
        path = paths.data_dir() / "copilot-page.html"
        self.dump_page(path)
        return f"The page was saved to {path} for troubleshooting."

    def busy(self) -> bool:
        return self._job is not None

    # ---------------------------------------------------------------- ask
    def ask(self, prompt: str, *, new_chat: bool, cancel=None, on_progress=None, on_done=None,
            on_error=None) -> None:
        """Send ``prompt``; ``on_progress(text_so_far)`` while Copilot writes, then ``on_done(reply)`` or
        ``on_error(message)``. ``cancel`` is a threading.Event; setting it presses Copilot's stop button."""
        if self._job:
            on_error and on_error("Copilot is still answering the previous request.")
            return
        if not self.isVisible():
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.show()
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self._job = {"prompt": prompt, "cancel": cancel, "progress": on_progress, "done": on_done,
                     "error": on_error, "stage": "ready", "started": time.monotonic(), "last": None,
                     "same": 0, "before": 0, "pending": False}
        if new_chat:
            self.new_chat()
        self._timer.start()

    def _finish(self, reply: str | None = None, error: str | None = None) -> None:
        job, self._job = self._job, None
        self._timer.stop()
        if not job:
            return
        if error is not None:
            job["error"] and job["error"](error)
        else:
            job["done"] and job["done"](reply or "")

    def _tick(self) -> None:
        job = self._job
        if not job or job["pending"]:
            return
        elapsed = time.monotonic() - job["started"]
        cancelled = job["cancel"] is not None and job["cancel"].is_set()
        if job["stage"] == "ready":
            if cancelled:
                return self._finish(None, "Stopped.")
            if elapsed > READY_TIMEOUT:
                return self._finish(None, "Copilot's page didn't become ready. Check the Copilot window.")
            job["pending"] = True
            self._js("window.__viper.state()", self._on_state)
        elif job["stage"] == "reply":
            if cancelled and not job.get("stopped"):
                job["stopped"] = True
                self._js("window.__viper.stop()")
            if elapsed > REPLY_TIMEOUT:
                return self._finish(job["last"] or None, None if job["last"] else "Copilot didn't answer in time.")
            job["pending"] = True
            self._js(f"window.__viper.read({job['before']})", self._on_read)

    def _on_state(self, state) -> None:
        job = self._job
        if not job:
            return
        job["pending"] = False
        if not isinstance(state, dict):
            return  # page still loading
        if state.get("signedOut"):
            self.bring_up()
            return self._finish(None, "Sign in to Copilot in the Copilot window that just opened (Sign in with "
                                      "Microsoft), then send your message again.")
        if not state.get("ready") or state.get("busy"):
            return
        limit = int(state.get("maxLength") or -1)
        if 0 < limit < len(job["prompt"]):
            return self._finish(None, f"This request is {len(job['prompt']):,} characters but Copilot accepts "
                                      f"{limit:,}. Untick \"Send the current file\" / \"Include terminal output\", "
                                      "or select less code.")
        job["before"] = int(state.get("replies") or 0)
        job["pending"] = True
        self._js(f"window.__viper.send({json.dumps(job['prompt'])})", self._on_sent)

    def _on_sent(self, result) -> None:
        job = self._job
        if not job:
            return
        if result != "ok":
            job["pending"] = False
            return self._finish(None, f"Couldn't type into Copilot ({result}). Copilot may have changed its page; "
                                      f"{self._diagnose()}")
        # React needs a moment to see the new value before the send button enables.
        QTimer.singleShot(250, lambda: self._js("window.__viper.submit()", self._on_submitted))

    def _on_submitted(self, _how) -> None:
        job = self._job
        if job:
            job["pending"] = False
            job["stage"] = "reply"
            job["started"] = time.monotonic()
            self.status.setText("Copilot is answering...")

    def _on_read(self, r) -> None:
        job = self._job
        if not job:
            return
        job["pending"] = False
        if not isinstance(r, dict):
            return
        text, busy = r.get("text"), bool(r.get("busy"))
        if text is None:
            if time.monotonic() - job["started"] > 45 and not busy:
                self._finish(None, "Copilot didn't show a reply. If it's waiting on something in its window "
                                   "(sign-in, a captcha, a full conversation), deal with it there and send again. "
                                   + self._diagnose())
            return
        if text != job["last"]:
            job["last"], job["same"] = text, 0
            job["progress"] and job["progress"](text)
            return
        job["same"] += 1
        if not busy and text and (job["same"] >= STABLE_POLLS or job.get("stopped")):
            self.status.setText("")
            self._finish(text)


_window: CopilotWindow | None = None


def window() -> CopilotWindow:
    """The one Copilot window, created on first use."""
    global _window
    if _window is None:
        _window = CopilotWindow()
    return _window


def close_window() -> None:
    global _window
    if _window is not None:
        _window.dispose()
        _window = None
