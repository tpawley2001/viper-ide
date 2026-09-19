"""AI Assistant dock: chat with an OpenAI-compatible model and apply its edits to the editor."""
from __future__ import annotations

import html
import os
import re
import threading
from urllib.parse import quote, unquote

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                             QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from . import assistant
from .dialogs import DiffPreviewDialog
from .icons import icon
from .widgets import InfoBar
from .workers import run_async

_OPEN_BLOCK = re.compile(r"^[ \t]*<{5,9} ?SEARCH", re.MULTILINE)
_OPEN_NEW_FILE = re.compile(r"^[ \t]*<{5,9} ?NEW[ _]FILE\b:?[ \t]*([^\n]*)\n?", re.MULTILINE)


_EMPTY_FENCE = re.compile(r"^[ \t]*```[^\n]*\n\s*^[ \t]*```[ \t]*$", re.MULTILINE)


def display_markdown(reply: str, show_code: bool = True, summary: str = "") -> str:
    """Show edit blocks as diff fences (raw ``=======`` lines would render as headings). With ``show_code`` off
    the edit and new-file blocks are left out (they're seen in Review & Apply or their tab) and ``summary``, a
    line saying what they were, goes at the end instead."""
    if not show_code:
        return compact_markdown(reply, summary)
    def fence(m):
        search, replace = assistant._strip_fence(m.group(1)), assistant._strip_fence(m.group(2))
        lines = [f"- {x}" for x in search.split("\n")] if search else []
        lines += [f"+ {x}" for x in replace.split("\n")] if replace else []
        return "```diff\n" + "\n".join(lines) + "\n```"

    def new_file(m, closed=True):
        body = assistant._strip_fence(m.group(2)) if closed else ""
        return f"**New tab: {assistant.safe_file_name(m.group(1))}**\n\n```python\n{body}" + ("\n```" if closed else "")
    out = assistant._NEW_FILE.sub(new_file, reply)
    out = assistant._BLOCK.sub(fence, out)
    m = _OPEN_NEW_FILE.search(out)  # a new file still streaming in
    if m:
        return out[:m.start()] + new_file(m, closed=False) + out[m.end():] + "\n```"
    m = _OPEN_BLOCK.search(out)  # a block still streaming in
    if m:
        out = out[:m.start()] + "```\n" + out[m.start():] + "\n```"
    return out


def compact_markdown(reply: str, summary: str = "") -> str:
    out = assistant._BLOCK.sub("", assistant._NEW_FILE.sub("", reply))
    m = _OPEN_NEW_FILE.search(out)  # a new file still streaming in
    if m:
        out = out[:m.start()] + f"\n\n_Writing {assistant.safe_file_name(m.group(1))}..._"
    else:
        m = _OPEN_BLOCK.search(out)  # an edit still streaming in
        if m:
            out = out[:m.start()] + "\n\n_Writing an edit..._"
    out = _EMPTY_FENCE.sub("", out)  # fences the model wrapped around the blocks despite being told not to
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if summary:
        out = f"{out}\n\n{summary}" if out else summary
    return out


class PromptEdit(QPlainTextEdit):
    submitted = pyqtSignal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(e)


class AssistantPanel(QWidget):
    """``host`` is the main window: it supplies the current editor and settings."""

    def __init__(self, host, settings, parent=None):
        super().__init__(parent)
        self.host = host
        self.settings = settings
        self.history: list[dict] = []   # user/assistant turns, without the file context
        self._transcript: list[tuple[str, str]] = []
        self._streaming = ""
        self._cancel: threading.Event | None = None
        self._target = None              # (editor, path) the last request was about
        self._pending: tuple | None = None
        self._actions: dict[int, dict] = {}  # transcript index -> the reply's edits / new files

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        prow = QHBoxLayout()
        self.provider = QComboBox()
        self.provider.setToolTip("Which AI provider to use")
        self.provider.activated.connect(lambda _i: self.switch_provider(self.provider.currentText()))
        prow.addWidget(self.provider, 1)
        manage = QToolButton(text="Manage...")
        manage.setToolTip("Add, edit or remove AI providers")
        manage.clicked.connect(self.manage_providers)
        prow.addWidget(manage)
        lay.addLayout(prow)
        top = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setToolTip("Model name sent to the server")
        self.model.lineEdit().setPlaceholderText("model")
        self.model.lineEdit().editingFinished.connect(self._model_changed)
        self.model.activated.connect(lambda _i: self._model_changed())
        top.addWidget(self.model, 1)
        self.refresh = QToolButton(icon=icon("refresh"))
        self.refresh.setToolTip("Load the server's model list")
        self.refresh.clicked.connect(lambda: self.load_models(silent=False))
        top.addWidget(self.refresh)
        self.new_chat = QToolButton(icon=icon("clear"))
        self.new_chat.setToolTip("New conversation")
        self.new_chat.clicked.connect(self.clear)
        top.addWidget(self.new_chat)
        self.show_code = QToolButton(text="</>", checkable=True)
        self.show_code.setToolTip("Show the code of suggested edits and new files in the chat. Off: the chat shows "
                                  "a summary and the code is seen in Review && Apply or its tab")
        self.show_code.setChecked(bool(settings.get("ai_show_code")))
        self.show_code.toggled.connect(self._show_code_toggled)
        top.addWidget(self.show_code)
        lay.addLayout(top)

        self.bar = InfoBar(self)
        lay.addWidget(self.bar)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link_clicked)
        # Follow the newest text unless the user scrolls up to read; scrolling back down resumes following.
        self._follow = True
        self.view.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.view.verticalScrollBar().rangeChanged.connect(lambda _lo, _hi: self._follow and self.scroll_to_end())
        lay.addWidget(self.view, 1)

        self.include = QCheckBox("Send the current file")
        self.include.setToolTip("Include the open file (and selection) so the assistant can edit it")
        self.include.setChecked(bool(settings.get("ai_include_file")))
        self.include.toggled.connect(lambda on: self.settings.set("ai_include_file", bool(on)))
        self.context_label = QLabel()
        self.context_label.setObjectName("Dim")
        self.include_errors = QCheckBox("Include errors")
        self.include_errors.setToolTip("Also send the file's problems (pyflakes) and everything shown in red: the "
                                       "last run's error output, the console, terminal and debugger, and error "
                                       "notices")
        self.include_errors.setChecked(bool(settings.get("ai_include_errors")))
        self.include_errors.toggled.connect(self._errors_toggled)
        self.include.toggled.connect(lambda on: self.include_errors.setEnabled(on))
        self.include.toggled.connect(lambda _on: self.update_context())
        self.include_errors.setEnabled(self.include.isChecked())
        self.include_output = QCheckBox("Include terminal output")
        self.include_output.setToolTip("Send everything in the Run, Terminal, Python console and Debugger panels "
                                       "(the most recent part of each if it's very long), even without a file")
        self.include_output.setChecked(bool(settings.get("ai_include_output")))
        self.include_output.toggled.connect(self._output_toggled)
        row = QHBoxLayout()
        row.addWidget(self.include)
        row.addWidget(self.include_errors)
        row.addWidget(self.include_output)
        row.addStretch(1)
        lay.addLayout(row)
        self.context_label.setWordWrap(True)
        lay.addWidget(self.context_label)

        self.input = PromptEdit()
        self.input.setPlaceholderText("Ask a question or describe a change (Enter to send, Shift+Enter for a new line)")
        self.input.setMaximumHeight(110)
        self.input.submitted.connect(self.send)
        lay.addWidget(self.input)
        buttons = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("Dim")
        buttons.addWidget(self.status, 1)
        self.send_button = QPushButton("Send")
        self.send_button.setProperty("primary", True)
        self.send_button.clicked.connect(self.send)
        buttons.addWidget(self.send_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.hide()
        buttons.addWidget(self.stop_button)
        lay.addLayout(buttons)

        self._render_timer = QTimer(self, singleShot=True, interval=80)
        self._render_timer.timeout.connect(self._render)
        self.providers_changed()

    # ------------------------------------------------------------ settings
    def _model_changed(self) -> None:
        p = assistant.active_provider(self.settings)
        name = self.model.currentText().strip()
        if p and name != p["model"]:
            assistant.set_model(self.settings, p["name"], name)

    def providers_changed(self) -> None:
        """Refill the provider and model boxes from settings (after Manage or a switch)."""
        active = assistant.active_provider(self.settings)
        self.provider.blockSignals(True)
        self.provider.clear()
        for p in assistant.providers(self.settings):
            self.provider.addItem(p["name"])
        if active:
            self.provider.setCurrentText(active["name"])
        else:
            self.provider.addItem("No provider set up")
        self.provider.setEnabled(bool(active))
        self.provider.blockSignals(False)
        model = active["model"] if active else ""
        self.model.clear()
        if model:
            self.model.addItem(model)
        self.model.setCurrentText(model)
        self.status.setText("")
        if active:
            self.bar.clear("setup")
        self._render()

    def switch_provider(self, name: str) -> None:
        current = assistant.active_provider(self.settings)
        if current and current["name"] == name:
            return
        assistant.set_active(self.settings, name)
        self.providers_changed()
        if self._transcript:
            self._transcript.append(("note", f"Switched to {name}. The conversation continues with it."))
            self._render()
        self.load_models()

    def manage_providers(self) -> None:
        from .providersui import ProvidersDialog

        active = assistant.active_provider(self.settings)
        before = (active or {}).get("name")
        if ProvidersDialog(self.settings, self).exec():
            self.providers_changed()
            after = (assistant.active_provider(self.settings) or {}).get("name")
            if after and after != before and self._transcript:
                self._transcript.append(("note", f"Switched to {after}. The conversation continues with it."))
                self._render()
            self.load_models()

    def load_models(self, silent: bool = True) -> None:
        provider = assistant.active_provider(self.settings)
        if not provider:
            if not silent:
                self._no_server()
            return
        self.status.setText("Loading models...")

        def done(names):
            self.status.setText(f"{len(names)} model(s) available" if names else "The server listed no models")
            current = self.model.currentText().strip()
            self.model.clear()
            self.model.addItems(names)
            if current:
                if self.model.findText(current) < 0:
                    self.model.insertItem(0, current)
                self.model.setCurrentText(current)
            elif names:
                self.model.setCurrentIndex(0)
                self._model_changed()

        def failed(msg):
            self.status.setText("")
            if not silent:
                QMessageBox.warning(self, "AI Assistant", msg)

        name = provider["name"]

        def guarded(fn):
            def wrapper(value):  # drop the answer if the user switched provider meanwhile
                if (assistant.active_provider(self.settings) or {}).get("name") == name:
                    fn(value)
            return wrapper

        run_async(assistant.list_models, provider["base_url"], assistant.api_key(provider),
                  on_done=guarded(done), on_error=guarded(failed))

    def _no_server(self) -> None:
        self.bar.show_message("info", "Add an AI provider (OpenAI, OpenRouter, Gemini, Ollama, LM Studio, or any "
                              "OpenAI-compatible server) to use the assistant.",
                              [("Add Provider...", self.manage_providers, True)], tag="setup")

    # ------------------------------------------------------------- chatting
    def fix_errors(self) -> None:
        """Ask the model to fix the current file's problems and/or the last run's error."""
        e = self.host.editor()
        if e is None:
            return
        self.include.setChecked(True)
        self.include_errors.setChecked(True)
        problems, red = self._errors_for(e)
        crashed = self.host.last_run_error is not None
        if crashed and problems:
            prompt = "My program failed with the error shown, and the checker reports problems. Fix them."
        elif crashed:
            prompt = "My program failed with the error shown. Find the cause and fix it."
        elif problems and red:
            prompt = "Fix the problems the checker reports and the errors or warnings shown in red."
        elif problems:
            prompt = "Fix the problems the checker reports in this file."
        elif red:
            prompt = "Fix what's causing the errors or warnings shown in red."
        else:
            self.ask("", with_file=True)
            self.status.setText("No errors to send: no problems in the file and nothing shown in red.")
            return
        if self.busy():
            self.ask(prompt, with_file=True)
            return
        self.input.setPlainText(prompt)
        self.update_context()
        self.send()

    def ask(self, prompt: str = "", with_file: bool = False) -> None:
        """Focus the prompt box; ``with_file`` (Ask AI to Edit) makes sure the file goes along."""
        if with_file:
            self.include.setChecked(True)
        if prompt:
            self.input.setPlainText(prompt)
        self.input.setFocus()
        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input.setTextCursor(cursor)
        self.update_context()
        self.scroll_to_end()

    def _errors_toggled(self, on: bool) -> None:
        self.settings.set("ai_include_errors", bool(on))
        self.update_context()

    def _show_code_toggled(self, on: bool) -> None:
        self.settings.set("ai_show_code", bool(on))
        self._render()

    def _output_toggled(self, on: bool) -> None:
        self.settings.set("ai_include_output", bool(on))
        self.update_context()

    def _outputs(self) -> list[tuple[str, str]]:
        return self.host.panel_outputs() if self.include_output.isChecked() else []

    @staticmethod
    def _short(where: str) -> str:
        return "notice" if where.startswith("Notice") else where.split(":")[0].replace(" panel", "").lower()

    def _errors_for(self, editor) -> tuple[list[dict], list[tuple[str, str]]]:
        """Lint problems for this editor and all red output in the IDE, if errors are included."""
        if not self.include_errors.isChecked():
            return [], []
        return list(editor.lint_items), [(w, t) for w, t in self.host.red_outputs() if assistant.clean_red(t)]

    def update_context(self) -> None:
        outputs = [self._short(w) for w, _t in self._outputs()]
        output_note = "output from " + ", ".join(outputs) if outputs else ""
        e = self.host.editor()
        if not e or not self.include.isChecked():
            self.context_label.setText(output_note)
            return
        text = e.display_name()
        if e.hasSelectedText():
            lf, _, lt, it = e.getSelection()
            if it == 0 and lt > lf:
                lt -= 1
            text += f", lines {lf + 1}-{lt + 1}"
        problems, red = self._errors_for(e)
        if problems:
            text += f", {len(problems)} problem{'s' if len(problems) != 1 else ''}"
        if red:
            sources = []
            for where, _t in red:
                if self._short(where) not in sources:
                    sources.append(self._short(where))
            text += ", red text from " + ", ".join(sources)
        if output_note:
            text += ", " + output_note
        self.context_label.setText(text)

    def busy(self) -> bool:
        return self._cancel is not None

    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.busy():
            return
        provider = assistant.active_provider(self.settings)
        if not provider:
            self._no_server()
            return
        self.bar.clear()
        self._pending = None
        e = self.host.editor()
        context = []
        if self.include.isChecked() and e is not None:
            selection = None
            if e.hasSelectedText():
                lf, _, lt, it = e.getSelection()
                if it == 0 and lt > lf:
                    lt -= 1
                selection = (lf + 1, lt + 1, e.selectedText())
            problems, red = self._errors_for(e)
            context.append(assistant.context_message(e.path or e.display_name(), e.text(), selection, problems,
                                                     red))
            self._target = (e, e.path or e.display_name())
        else:
            self._target = None
        output = assistant.output_message(self._outputs())
        if output:
            context.append(output)
        content = "\n\n".join([*context, text])
        messages = [{"role": "system", "content": assistant.SYSTEM_PROMPT}, *self.history,
                    {"role": "user", "content": content}]
        self.history.append({"role": "user", "content": text})
        self._transcript.append(("user", text))
        self._follow = True
        self.input.clear()
        self._streaming = ""
        self._cancel = cancel = threading.Event()
        self._set_busy(True, "Waiting for the model...")
        self._render()
        run_async(assistant.stream_chat, provider["base_url"], assistant.api_key(provider),
                  self.model.currentText().strip(), messages, cancel=cancel,
                  on_progress=lambda t: self._progress(cancel, t),
                  on_done=lambda reply: self._finished(cancel, reply, None),
                  on_error=lambda msg: self._finished(cancel, None, msg))

    def stop(self) -> None:
        if self._cancel:
            self._cancel.set()
            self.status.setText("Stopping...")

    def clear(self) -> None:
        self.stop()
        self._cancel = None
        self._set_busy(False, "")
        self.history.clear()
        self._transcript.clear()
        self._streaming = ""
        self._pending = None
        self._actions.clear()
        self.bar.clear()
        self._render()

    def _set_busy(self, busy: bool, status: str) -> None:
        self.send_button.setVisible(not busy)
        self.stop_button.setVisible(busy)
        self.status.setText(status)

    def _progress(self, token, text) -> None:
        if token is not self._cancel:
            return
        if text is None:
            self.status.setText("Thinking...")
            return
        self.status.setText("Writing...")
        self._streaming = text
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _finished(self, token, reply: str | None, error: str | None) -> None:
        if token is not self._cancel:
            return  # a conversation cleared while this request was running
        stopped = token.is_set()
        self._cancel = None
        self._streaming = ""
        self._set_busy(False, "Stopped." if stopped else "")
        if error:
            self.history.pop()
            self._transcript.append(("error", error))
            self._render()
            return
        reply = reply or ""
        if not reply.strip():
            reply = "_(The model returned an empty reply. Reasoning models may need their thinking turned off, " \
                    "or a larger output limit on the server.)_"
        self.history.append({"role": "assistant", "content": reply})
        self._transcript.append(("assistant", reply))
        new_files = assistant.parse_new_files(reply)
        edits = assistant.parse_edits(reply)
        action = {}
        if new_files:
            action["files"] = new_files
        if edits and self._target:
            action["edits"] = (*self._target, edits)
        if action:
            self._actions[len(self._transcript) - 1] = action
        self._render()
        if new_files:
            self.open_new_files(new_files)
        if edits and self._target:
            self._offer_edits(edits)
        elif edits:
            self.status.setText("The reply has edits, but no file was sent, so there is nothing to apply them to.")

    # ----------------------------------------------------------- new files
    def open_new_files(self, files) -> list:
        """Open each NEW FILE block in its own tab. A name that's already open unsaved gets its contents replaced
        (one undo step), so a revised version doesn't pile up duplicate tabs. Returns the editors."""
        editors = []
        for f in files:
            text = f.content if not f.content or f.content.endswith("\n") else f.content + "\n"
            page = next((p for p in self.host.pages() if not p.editor.path and p.editor.untitled_name == f.name),
                        None)
            if page is not None:
                page.editor.set_text_undoable(text)
                self.host.tabs.setCurrentWidget(page)
            else:
                page = self.host.new_file()
                page.editor.untitled_name = f.name  # also what Save As suggests
                page.editor.setText(text)
                page.editor.setModified(True)
            self.host._refresh_tab(page)
            self.host._editor_idle(page.editor)
            editors.append(page.editor)
        names = ", ".join(f.name for f in files)
        self.status.setText(f"Opened {names} in {'a new tab' if len(files) == 1 else 'new tabs'} (not saved yet).")
        return editors

    # --------------------------------------------------------------- edits
    def _offer_edits(self, edits) -> None:
        editor, name = self._target
        action = self._actions.get(len(self._transcript) - 1, {})
        self._pending = action.get("edits") or (editor, name, edits)
        base = os.path.basename(name)
        self.bar.show_message("info", f"The assistant suggested <b>{len(edits)}</b> edit{'s' if len(edits) != 1 else ''}"
                              f" to <b>{html.escape(base)}</b>.",
                              [("Review && Apply...", self.review_edits, True),
                               ("Dismiss", lambda: self.bar.clear("edits"), False)], tag="edits")

    def review_edits(self) -> bool:
        if not self._pending:
            return False
        editor, name, edits = self._pending
        if sip.isdeleted(editor):
            self.bar.show_message("warning", f"{html.escape(os.path.basename(name))} was closed.", tag="edits")
            return False
        old = editor.text()
        new, problems = assistant.apply_edits(old, edits)
        base = os.path.basename(name)
        if new == old:
            detail = "<br>".join(html.escape(p) for p in problems) or "The edits don't change anything."
            self.bar.show_message("warning", f"None of the edits could be applied to {html.escape(base)}:<br>{detail}",
                                  [("Ask to Fix", lambda: self._ask_fix(problems), True)], tag="edits")
            return False
        heading = f"Apply the assistant's changes to {base}?"
        if problems:
            heading += "\n\nSome edits couldn't be matched and are left out:\n" + "\n".join(problems)
        if not DiffPreviewDialog(self, "AI Assistant - Review Changes", heading,
                                 assistant.unified_diff(old, new, base)).exec():
            return False
        if sip.isdeleted(editor):
            return False
        editor.set_text_undoable(new)  # a single undo step reverts the whole change
        for action in self._actions.values():
            if action.get("edits") is self._pending:
                action["applied"] = True
        self._pending = None
        self._render()
        self.bar.clear("edits")
        self.status.setText(f"Applied to {base} (Ctrl+Z to undo).")
        page = self.host._find_page(editor.path) if editor.path else None
        if page is not None:
            self.host.tabs.setCurrentWidget(page)
        return True

    def _ask_fix(self, problems) -> None:
        self.bar.clear("edits")
        self.input.setPlainText("Your edit blocks didn't apply:\n" + "\n".join(problems) +
                                "\nPlease resend them with SEARCH text copied exactly from the current file.")
        self.include.setChecked(True)
        self.send()

    # ------------------------------------------------------- chat links
    def _link_clicked(self, url) -> None:
        if url.scheme() != "viper":
            QDesktopServices.openUrl(url)
            return
        kind, _, rest = unquote(url.path(QUrl.ComponentFormattingOption.FullyEncoded)).partition("/")
        index, _, name = rest.partition("/")
        action = self._actions.get(int(index)) if index.isdigit() else None
        if not action:
            return
        if kind == "review" and action.get("edits"):
            self._pending = action["edits"]
            self.review_edits()
        elif kind == "file":
            self.show_new_file(next((f for f in action.get("files", []) if f.name == name), None))

    def show_new_file(self, f) -> None:
        """Switch to the tab a NEW FILE block opened, or open it again if that tab was saved or closed."""
        if f is None:
            return
        page = next((p for p in self.host.pages() if not p.editor.path and p.editor.untitled_name == f.name), None)
        if page is not None:
            self.host.tabs.setCurrentWidget(page)
        else:
            self.open_new_files([f])

    def _summary(self, index: int) -> str:
        action = self._actions.get(index)
        if not action:
            return ""
        lines = []
        for f in action.get("files", []):
            count = len(f.content.splitlines())
            lines.append(f"**New tab: {f.name}** ({count} line{'s' if count != 1 else ''}) - "
                         f"[Show]({self._link('file', index, f.name)})")
        if action.get("edits"):
            _editor, name, edits = action["edits"]
            what = f"**{len(edits)} edit{'s' if len(edits) != 1 else ''} to {os.path.basename(name)}**"
            if action.get("applied"):
                lines.append(f"{what} - applied - [Review again]({self._link('review', index)})")
            else:
                lines.append(f"{what} - [Review & Apply]({self._link('review', index)})")
        return "\n\n".join(lines)

    @staticmethod
    def _link(kind: str, index: int, name: str = "") -> str:
        return f"viper:{kind}/{index}" + (f"/{quote(name)}" if name else "")

    # -------------------------------------------------------------- render
    _rendering = False

    def _scrolled(self, value: int) -> None:
        if not self._rendering:
            bar = self.view.verticalScrollBar()
            self._follow = value >= bar.maximum() - 4

    def scroll_to_end(self) -> None:
        if sip.isdeleted(self):
            return
        bar = self.view.verticalScrollBar()
        self._rendering = True
        bar.setValue(bar.maximum())
        self._rendering = False
        self._follow = True

    def _render(self) -> None:
        if not self._transcript and not self._streaming:
            if assistant.active_provider(self.settings):
                intro = ("Ask about the open file, or describe a change, e.g. *\"add type hints\"* or "
                         "*\"handle a missing file in load()\"*. Select code first to focus on it. Suggested "
                         "edits are shown as a diff before anything changes. Ask for a *\"new file\"* or *\"new tab\"* "
                         "and the code opens in a tab of its own.")
            else:
                intro = ("**No AI provider set up.** Press **Manage...** above and add one: OpenAI, OpenRouter, "
                         "Gemini, Groq, Mistral or DeepSeek (with an API key), a local Ollama, LM Studio or "
                         "llama.cpp server, or any other OpenAI-compatible URL. Add several and switch between "
                         "them from the drop-down.")
            self.view.setMarkdown(intro)
            return
        parts = []
        show_code = self.show_code.isChecked()
        for i, (role, text) in enumerate(self._transcript):
            if role == "user":
                parts.append("**You**\n\n" + text)
            elif role == "assistant":
                # Edits with no file to apply them to have nowhere else to be seen, so they stay in the chat.
                keep = show_code or (assistant.parse_edits(text) and "edits" not in self._actions.get(i, {}))
                parts.append("**Assistant**\n\n" + display_markdown(text, bool(keep), self._summary(i)))
            elif role == "note":
                parts.append(f"_{text}_")
            else:
                parts.append("**Error:** " + text)
        if self._streaming:
            parts.append("**Assistant**\n\n" + display_markdown(self._streaming, show_code))
        follow = self._follow
        self._rendering = True
        self.view.setMarkdown("\n\n---\n\n".join(parts))  # resets the scroll position to the top
        self._rendering = False
        self._follow = follow
        if follow:
            self.scroll_to_end()
            QTimer.singleShot(0, self.scroll_to_end)  # again once the new text has been laid out
