"""Microsoft Copilot window: drive a fake copilot.microsoft.com page end to end."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("PyQt6.QtWebEngineWidgets")

from viper_ide import assistant, copilotweb  # noqa: E402

# The reply Copilot "writes": prose, then an edit block inside a ```text fence the way PROMPT_NOTE asks.
REPLY_HTML = ("<p>Adding <strong>type hints</strong>.</p>"
              "<div class='code-block'><div class='header'>text<button>Copy</button></div>"
              "<pre><code class='language-text'>&lt;&lt;&lt;&lt;&lt;&lt;&lt; SEARCH\ndef add(a, b):\n=======\n"
              "def add(a: int, b: int) -&gt; int:\n&gt;&gt;&gt;&gt;&gt;&gt;&gt; REPLACE\n</code></pre></div>")

PAGE = """<!doctype html><html><body>
<div id=chat></div>
%BLOCK%
<textarea id=userInput maxlength=%MAX%></textarea>
<button data-testid=submit-button id=send disabled>Send</button>
<script>
const ta = document.getElementById('userInput'), send = document.getElementById('send'), chat = document.getElementById('chat');
window.sent = [];
ta.addEventListener('input', () => { send.disabled = !ta.value; });
send.addEventListener('click', () => {
  window.sent.push(ta.value);
  const u = document.createElement('div'); u.setAttribute('data-content', 'user-message'); u.textContent = ta.value;
  chat.appendChild(u); ta.value = ''; send.disabled = true;
  const stop = document.createElement('button'); stop.setAttribute('aria-label', 'Stop responding');
  document.body.appendChild(stop);
  const ai = document.createElement('div'); ai.setAttribute('data-content', 'ai-message'); chat.appendChild(ai);
  const full = %REPLY%; let n = 0;
  const t = setInterval(() => {
    n += 40;
    if (n >= full.length) { ai.innerHTML = full; clearInterval(t); stop.remove(); return; }
    ai.innerHTML = '<p>' + 'Thinking'.slice(0, 1 + n %% 8) + '</p>';
  }, 120);
  stop.addEventListener('click', () => { clearInterval(t); ai.innerHTML = '<p>Partial</p>'; stop.remove(); });
});
</script></body></html>"""


class FakeCopilot(BaseHTTPRequestHandler):
    signed_out = False
    max_length = 10240

    def log_message(self, *a):
        pass

    def do_GET(self):
        block = "<h1 data-testid=anonymous-block-page-title>Sign in to Copilot</h1>" if FakeCopilot.signed_out else ""
        body = (PAGE.replace("%BLOCK%", block).replace("%MAX%", str(FakeCopilot.max_length))
                .replace("%REPLY%", json.dumps(REPLY_HTML)).replace("%%", "%")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def copilot_win(app, tmp_path):
    from test_gui import wait
    FakeCopilot.signed_out, FakeCopilot.max_length = False, 10240
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeCopilot)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    win = copilotweb.CopilotWindow(f"http://127.0.0.1:{srv.server_address[1]}/", storage=tmp_path / "web")
    win.wait = lambda cond, secs=30: wait(app, cond, secs)
    yield win
    win.dispose()
    srv.shutdown()


def _ask(win, prompt, **kw):
    out = {"progress": []}
    win.ask(prompt, new_chat=kw.pop("new_chat", True), on_progress=out["progress"].append,
            on_done=lambda r: out.__setitem__("reply", r), on_error=lambda m: out.__setitem__("error", m), **kw)
    assert win.wait(lambda: "reply" in out or "error" in out)
    return out


def test_ask_reads_the_reply_as_markdown(copilot_win):
    out = _ask(copilot_win, "Add type hints\nplease")
    assert "error" not in out, out
    reply = out["reply"]
    assert reply.startswith("Adding **type hints**.")
    assert "Copy" not in reply  # the code block's buttons aren't part of the reply
    edits = assistant.parse_edits(reply)
    assert len(edits) == 1 and edits[0].search == "def add(a, b):"
    new, problems = assistant.apply_edits("def add(a, b):\n    return a + b\n", edits)
    assert not problems and new.startswith("def add(a: int, b: int) -> int:")
    assert out["progress"] and out["progress"][-1] == reply
    copilot_win.page.runJavaScript("window.sent", lambda v: setattr(copilot_win, "_sent", v))
    assert copilot_win.wait(lambda: getattr(copilot_win, "_sent", None) is not None)
    assert copilot_win._sent == ["Add type hints\nplease"]

    # A follow-up in the same chat reads the NEW reply, not the first one again.
    out2 = _ask(copilot_win, "again", new_chat=False)
    assert out2.get("reply") == reply
    assert not copilot_win.busy()


def test_signed_out_brings_the_window_up(copilot_win):
    FakeCopilot.signed_out = True
    out = _ask(copilot_win, "hi")
    assert "Sign in to Copilot" in out["error"] and copilot_win.isVisible()


def test_sign_in_popup_opens_and_closes(copilot_win):
    assert copilot_win.wait(lambda: copilot_win.page.url().toString().endswith("/"))
    copilot_win.page.runJavaScript("window.signin = window.open('/signin', 'signin')")
    assert copilot_win.wait(lambda: len(copilot_win.popups) == 1)
    win, view, page = copilot_win.popups[0]
    assert win.isVisible() and page.profile() is copilot_win.profile  # same sign-in cookies
    copilot_win.page.runJavaScript("window.signin.close()")
    assert copilot_win.wait(lambda: not copilot_win.popups)


def test_too_long_and_stop(copilot_win):
    FakeCopilot.max_length = 10
    out = _ask(copilot_win, "x" * 50)
    assert "accepts 10" in out["error"]
    FakeCopilot.max_length = 10240
    cancel = threading.Event()
    out = {"progress": []}
    copilot_win.ask("hello", new_chat=True, cancel=cancel, on_progress=lambda t: (out["progress"].append(t),
                    cancel.set()), on_done=lambda r: out.__setitem__("reply", r),
                    on_error=lambda m: out.__setitem__("error", m))
    assert copilot_win.wait(lambda: "reply" in out or "error" in out)
    assert out.get("reply") == "Partial"


def test_assistant_panel_uses_the_copilot_window(app, copilot_win, tmp_path, monkeypatch):
    from test_gui import wait
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(copilotweb, "_window", copilot_win)
    from viper_ide.app import MainWindow
    from viper_ide.settings import Settings

    src = tmp_path / "m.py"
    src.write_text("def add(a, b):\n    return a + b\n")
    settings = Settings(tmp_path / "settings.json")
    settings._data.update(interpreter=sys.executable, extra_interpreters=[sys.executable],
                          ai_providers=[assistant.new_ms_copilot_provider()], ai_provider="Microsoft Copilot",
                          ai_include_file=True)
    win = MainWindow(settings, [str(tmp_path)])
    win.show()
    try:
        win.open_path(str(src))
        panel = win.assistant
        win.show_assistant()
        monkeypatch.setattr(panel, "_offer_edits", lambda edits: setattr(panel, "_offered", edits))
        panel.input.setPlainText("add type hints")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 30)
        assert len(panel._offered) == 1 and panel._ms_copilot_turns == 1
        copilot_win.page.runJavaScript("window.sent", lambda v: setattr(copilot_win, "_sent", v))
        assert wait(app, lambda: getattr(copilot_win, "_sent", None) is not None)
        first = copilot_win._sent[-1]
        # First turn: instructions (with the Markdown fence note), the file, then the request.
        assert first.startswith("[Instructions]") and "```text code fence" in first
        assert "def add(a, b):" in first and first.rstrip().endswith("add type hints")

        copilot_win._sent = None
        panel.input.setPlainText("now a docstring")
        panel.send()
        assert wait(app, lambda: not panel.busy(), 30)
        copilot_win.page.runJavaScript("window.sent", lambda v: setattr(copilot_win, "_sent", v))
        assert wait(app, lambda: getattr(copilot_win, "_sent", None) is not None)
        second = copilot_win._sent[-1]
        assert "[Instructions]" not in second and second.rstrip().endswith("now a docstring")
        panel.clear()
        assert panel._ms_copilot_turns == 0
    finally:
        win.close()


def test_flatten_messages():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
    assert assistant.flatten_messages(msgs) == ("[Instructions]\nS\n\n[Earlier conversation]\nUser: a\n\n"
                                               "Assistant: b\n\n[Request]\nc")
    assert assistant.flatten_messages([{"role": "user", "content": "c"}]) == "c"
