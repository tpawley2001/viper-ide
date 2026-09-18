"""AI Assistant dock: chat with an OpenAI-compatible model and apply its edits to the editor."""
from __future__ import annotations

import html
import os
import re
import threading

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                             QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from . import assistant
from .dialogs import DiffPreviewDialog
from .icons import icon
from .widgets import InfoBar
from .workers import run_async

_OPEN_BLOCK = re.compile(r"^[ \t]*<{5,9} ?SEARCH", re.MULTILINE)


def display_markdown(reply: str) -> str:
    """Show edit blocks as diff fences (raw ``=======`` lines would render as headings)."""
    def fence(m):
        search, replace = assistant._strip_fence(m.group(1)), assistant._strip_fence(m.group(2))
        lines = [f"- {x}" for x in search.split("\n")] if search else []
        lines += [f"+ {x}" for x in replace.split("\n")] if replace else []
        return "```diff\n" + "\n".join(lines) + "\n```"
    out = assistant._BLOCK.sub(fence, reply)
    m = _OPEN_BLOCK.search(out)  # a block still streaming in
    if m:
        out = out[:m.start()] + "```\n" + out[m.start():] + "\n```"
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

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setToolTip("Model name sent to the server")
        self.model.lineEdit().setPlaceholderText("model")
        if settings.get("ai_model"):
            self.model.addItem(settings.get("ai_model"))
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
        lay.addLayout(top)

        self.bar = InfoBar(self)
        lay.addWidget(self.bar)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        lay.addWidget(self.view, 1)

        self.include = QCheckBox("Send the current file")
        self.include.setToolTip("Include the open file (and selection) so the assistant can edit it")
        self.include.setChecked(bool(settings.get("ai_include_file")))
        self.include.toggled.connect(lambda on: self.settings.set("ai_include_file", bool(on)))
        self.context_label = QLabel()
        self.context_label.setObjectName("Dim")
        row = QHBoxLayout()
        row.addWidget(self.include)
        row.addWidget(self.context_label, 1)
        lay.addLayout(row)

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
        self._render()

    # ------------------------------------------------------------ settings
    def _model_changed(self) -> None:
        name = self.model.currentText().strip()
        if name != self.settings.get("ai_model"):
            self.settings.set("ai_model", name)

    def settings_changed(self) -> None:
        name = self.settings.get("ai_model")
        if name and self.model.currentText() != name:
            if self.model.findText(name) < 0:
                self.model.addItem(name)
            self.model.setCurrentText(name)
        self._render()

    def load_models(self, silent: bool = True) -> None:
        if not self.settings.get("ai_base_url"):
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

        run_async(assistant.list_models, self.settings.get("ai_base_url"), assistant.api_key(self.settings),
                  on_done=done, on_error=failed)

    def _no_server(self) -> None:
        self.bar.show_message("info", "Connect an OpenAI-compatible server (OpenAI, llama.cpp, Ollama, LM Studio...) "
                              "to use the assistant.",
                              [("Open Settings", self.host.open_settings, True)], tag="setup")

    # ------------------------------------------------------------- chatting
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

    def update_context(self) -> None:
        e = self.host.editor()
        if not e:
            self.context_label.setText("")
            return
        text = e.display_name()
        if e.hasSelectedText():
            lf, _, lt, it = e.getSelection()
            if it == 0 and lt > lf:
                lt -= 1
            text += f", lines {lf + 1}-{lt + 1}"
        self.context_label.setText(text)

    def busy(self) -> bool:
        return self._cancel is not None

    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.busy():
            return
        if not self.settings.get("ai_base_url"):
            self._no_server()
            return
        self.bar.clear()
        self._pending = None
        e = self.host.editor()
        content = text
        if self.include.isChecked() and e is not None:
            selection = None
            if e.hasSelectedText():
                lf, _, lt, it = e.getSelection()
                if it == 0 and lt > lf:
                    lt -= 1
                selection = (lf + 1, lt + 1, e.selectedText())
            content = assistant.context_message(e.path or e.display_name(), e.text(), selection) + "\n\n" + text
            self._target = (e, e.path or e.display_name())
        else:
            self._target = None
        messages = [{"role": "system", "content": assistant.SYSTEM_PROMPT}, *self.history,
                    {"role": "user", "content": content}]
        self.history.append({"role": "user", "content": text})
        self._transcript.append(("user", text))
        self.input.clear()
        self._streaming = ""
        self._cancel = cancel = threading.Event()
        self._set_busy(True, "Waiting for the model...")
        self._render()
        run_async(assistant.stream_chat, self.settings.get("ai_base_url"), assistant.api_key(self.settings),
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
        self._render()
        edits = assistant.parse_edits(reply)
        if edits and self._target:
            self._offer_edits(edits)
        elif edits:
            self.status.setText("The reply has edits, but no file was sent, so there is nothing to apply them to.")

    # --------------------------------------------------------------- edits
    def _offer_edits(self, edits) -> None:
        editor, name = self._target
        self._pending = (editor, name, edits)
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
        self._pending = None
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

    # -------------------------------------------------------------- render
    def _render(self) -> None:
        if not self._transcript and not self._streaming:
            if self.settings.get("ai_base_url"):
                intro = ("Ask about the open file, or describe a change, e.g. *\"add type hints\"* or "
                         "*\"handle a missing file in load()\"*. Select code first to focus on it. Suggested "
                         "edits are shown as a diff before anything changes.")
            else:
                intro = ("**No assistant server set.** Open **Settings > AI server URL** and enter any "
                         "OpenAI-compatible endpoint: `https://api.openai.com/v1` (with an API key), or a local "
                         "llama.cpp, Ollama (`http://localhost:11434/v1`) or LM Studio server.")
            self.view.setMarkdown(intro)
            return
        parts = []
        for role, text in self._transcript:
            if role == "user":
                parts.append("**You**\n\n" + text)
            elif role == "assistant":
                parts.append("**Assistant**\n\n" + display_markdown(text))
            else:
                parts.append("**Error:** " + text)
        if self._streaming:
            parts.append("**Assistant**\n\n" + display_markdown(self._streaming))
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.view.setMarkdown("\n\n---\n\n".join(parts))
        if at_end or self._streaming:
            bar.setValue(bar.maximum())
