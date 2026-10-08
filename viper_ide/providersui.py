"""Manage AI Providers: named OpenAI-compatible servers, each with its own URL, key and model,
plus GitHub Copilot (signs in with a GitHub account instead of an API key) and Microsoft Copilot
(driven in its own browser window, see copilotweb.py)."""
from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont, QGuiApplication
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from . import assistant, copilot
from .workers import run_async


class CopilotSignInDialog(QDialog):
    """GitHub device-flow sign-in: show the code, open github.com/login/device, wait for approval."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in to GitHub Copilot")
        self.token = ""
        self._cancel = threading.Event()
        lay = QVBoxLayout(self)
        self.info = QLabel("Asking GitHub for a sign-in code...")
        self.info.setWordWrap(True)
        self.code = QLabel("")
        font = QFont(self.code.font())
        font.setPointSize(font.pointSize() * 2)
        font.setBold(True)
        self.code.setFont(font)
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.open_btn = QPushButton("Copy Code && Open GitHub")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open)
        self.status = QLabel("")
        self.status.setObjectName("Dim")
        self.status.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        for w in (self.info, self.code, self.open_btn, self.status, buttons):
            lay.addWidget(w)
        self.setMinimumWidth(460)
        self._flow: dict = {}
        run_async(copilot.start_sign_in, on_done=self._started, on_error=self._failed)

    def _started(self, flow: dict) -> None:
        if self._cancel.is_set():
            return
        self._flow = flow
        self.info.setText(f"Enter this code at {flow.get('verification_uri', 'https://github.com/login/device')} "
                          "and approve access. Any GitHub account with Copilot works, including the free plan.")
        self.code.setText(flow.get("user_code", ""))
        self.open_btn.setEnabled(True)
        self.status.setText("Waiting for you to approve on GitHub...")
        self.adjustSize()
        self._open()
        run_async(copilot.finish_sign_in, flow, self._cancel, on_done=self._signed_in, on_error=self._failed)

    def _open(self) -> None:
        QGuiApplication.clipboard().setText(self._flow.get("user_code", ""))
        QDesktopServices.openUrl(QUrl(self._flow.get("verification_uri") or "https://github.com/login/device"))

    def _signed_in(self, token: str) -> None:
        if self._cancel.is_set():
            return
        self.token = token
        self.accept()

    def _failed(self, msg: str) -> None:
        if not self._cancel.is_set():
            self.status.setText(msg.split(": ", 1)[-1] if "Error: " in msg else msg)

    def done(self, result: int) -> None:
        self._cancel.set()
        super().done(result)


class ProvidersDialog(QDialog):
    def __init__(self, settings, parent=None, select: str | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AI Providers")
        self.resize(720, 400)
        self.items = [dict(p) for p in assistant.providers(settings)]
        self.active = settings.get("ai_provider") or (self.items[0]["name"] if self.items else "")
        self._loading = False

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add")
        menu = QMenu(add)
        menu.addAction(assistant.COPILOT_PRESET, self._add_copilot)
        menu.addAction(assistant.MS_COPILOT_PRESET, self._add_ms_copilot)
        menu.addSeparator()
        for name, url, env in assistant.PRESETS:
            menu.addAction(name, lambda n=name, u=url, e=env: self._add(n, u, e))
        menu.addSeparator()
        menu.addAction("Custom server...", lambda: self._add("Custom", "", ""))
        add.setMenu(menu)
        row.addWidget(add)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove)
        row.addWidget(self.remove)
        left.addLayout(row)

        self.form_box = QWidget()
        form = QFormLayout(self.form_box)
        self.name = QLineEdit()
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://.../v1 (the part before /chat/completions)")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.env = QLineEdit()
        self.env.setPlaceholderText("optional, e.g. OPENAI_API_KEY")
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.lineEdit().setPlaceholderText("model name")
        self.test_btn = test = QPushButton("Test && Load Models")
        test.clicked.connect(self._test)
        self.test_result = QLabel()
        self.test_result.setObjectName("Dim")
        self.test_result.setWordWrap(True)
        self.sign_in = QPushButton("Sign in with GitHub")
        self.sign_in.clicked.connect(self._sign_in)
        self.account = QLabel()
        self.account.setWordWrap(True)
        self.use = QPushButton("Use This Provider")
        self.use.clicked.connect(self._make_active)
        self._rows = {}
        for label, w in (("Name", self.name), ("GitHub", self.account), ("", self.sign_in),
                         ("Server URL", self.url), ("API key", self.key), ("Key from env var", self.env),
                         ("Model", self.model), ("", test), ("", self.test_result), ("", self.use)):
            form.addRow(label, w)
            self._rows[w] = form
        self.form = form
        for w in (self.name, self.url, self.key, self.env):
            w.textEdited.connect(self._store)
        self.model.currentTextChanged.connect(self._store)

        body = QHBoxLayout()
        body.addLayout(left, 2)
        body.addWidget(self.form_box, 3)
        lay = QVBoxLayout(self)
        note = QLabel("Keys are saved in Viper's settings file in plain text. Leave the key blank for local "
                      "servers, or name an environment variable to read it from instead. GitHub Copilot needs "
                      "no key: sign in with your GitHub account and it uses your Copilot plan. Microsoft Copilot "
                      "runs in its own window, signed in with your Microsoft account.")
        note.setObjectName("Dim")
        note.setWordWrap(True)
        lay.addLayout(body, 1)
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self._refill(select or self.active)

    # ------------------------------------------------------------------ list
    def _refill(self, select: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.items:
            self.list.addItem(p["name"] + ("   (in use)" if p["name"] == self.active else ""))
        self.list.blockSignals(False)
        names = [p["name"] for p in self.items]
        self.list.setCurrentRow(names.index(select) if select in names else (0 if names else -1))
        self._show(self.list.currentRow())

    def _current(self) -> dict | None:
        row = self.list.currentRow()
        return self.items[row] if 0 <= row < len(self.items) else None

    def _unique(self, name: str) -> str:
        names = {p["name"] for p in self.items}
        out, n = name, 2
        while out in names:
            out, n = f"{name} {n}", n + 1
        return out

    def _add(self, name: str, url: str, env: str) -> None:
        p = assistant.new_provider(self._unique(name), url, env)
        self.items.append(p)
        if not self.active:
            self.active = p["name"]
        self._refill(p["name"])
        (self.url if not url else self.key if env else self.model).setFocus()

    def _add_copilot(self) -> None:
        p = assistant.new_copilot_provider(self._unique("GitHub Copilot"))
        self.items.append(p)
        if not self.active:
            self.active = p["name"]
        self._refill(p["name"])
        self._sign_in()

    def _add_ms_copilot(self) -> None:
        p = assistant.new_ms_copilot_provider(self._unique("Microsoft Copilot"))
        self.items.append(p)
        if not self.active:
            self.active = p["name"]
        self._refill(p["name"])
        self._sign_in()

    def _sign_in(self) -> None:
        p = self._current()
        if assistant.is_ms_copilot(p):
            from . import copilotweb
            try:
                copilotweb.window().bring_up()
            except copilotweb.CopilotError as e:
                QMessageBox.warning(self, "Microsoft Copilot", str(e))
            return
        if not assistant.is_copilot(p):
            return
        dlg = CopilotSignInDialog(self)
        if not dlg.exec() or not dlg.token:
            return
        copilot.forget(p.get("api_key") or "")
        p["api_key"] = dlg.token
        p.pop("account", None)
        self._show(self.list.currentRow())
        self._test()

    def _remove(self) -> None:
        p = self._current()
        if not p:
            return
        self.items.remove(p)
        if p["name"] == self.active:
            self.active = self.items[0]["name"] if self.items else ""
        self._refill(self.active)

    def _make_active(self) -> None:
        p = self._current()
        if p:
            self.active = p["name"]
            self._refill(p["name"])

    # ------------------------------------------------------------------ form
    def _show(self, row: int) -> None:
        p = self._current()
        self.form_box.setEnabled(p is not None)
        self.remove.setEnabled(p is not None)
        self._loading = True
        self.name.setText(p["name"] if p else "")
        self.url.setText(p["base_url"] if p else "")
        self.key.setText(p["api_key"] if p else "")
        self.env.setText(p["env_key"] if p else "")
        self.model.clear()
        if p and p["model"]:
            self.model.addItem(p["model"])
        self.model.setCurrentText(p["model"] if p else "")
        self.test_result.setText("")
        cop, ms = assistant.is_copilot(p), assistant.is_ms_copilot(p)
        for w in (self.url, self.key, self.env):
            self.form.setRowVisible(w, not (cop or ms))
        for w in (self.account, self.sign_in):
            self.form.setRowVisible(w, cop or ms)
        for w in (self.model, self.test_btn, self.test_result):
            self.form.setRowVisible(w, not ms)
        self.form.labelForField(self.account).setText("Microsoft" if ms else "GitHub")
        if ms:
            self.account.setText("Copilot runs in its own window. Sign in there once with your Microsoft "
                                 "account; Viper types your requests into it and reads the replies. Pick "
                                 "Copilot's mode (Quick, Think Deeper...) in that window.")
            self.sign_in.setText("Open Copilot Window")
        elif cop:
            signed = bool(p.get("api_key"))
            self.account.setText((f"Signed in as {p['account']}" if p.get("account") else "Signed in")
                                 if signed else "Not signed in")
            self.sign_in.setText("Sign in Again..." if signed else "Sign in with GitHub")
        self.use.setEnabled(bool(p) and p["name"] != self.active)
        self._loading = False

    def _store(self, *_):
        p = self._current()
        if not p or self._loading:
            return
        old = p["name"]
        new = self.name.text().strip()
        if new and new != old and new not in {q["name"] for q in self.items}:
            p["name"] = new
            if self.active == old:
                self.active = new
            item = self.list.currentItem()
            item.setText(new + ("   (in use)" if new == self.active else ""))
        if not (assistant.is_copilot(p) or assistant.is_ms_copilot(p)):
            p["base_url"] = self.url.text().strip()
            p["api_key"] = self.key.text().strip()
            p["env_key"] = self.env.text().strip()
        p["model"] = self.model.currentText().strip()

    def _test(self) -> None:
        p = self._current()
        if not p:
            return
        self._store()
        self.test_result.setText("Connecting...")
        name = p["name"]

        def done(models):
            cur = self._current()
            if not cur or cur["name"] != name:
                return
            self.test_result.setText(f"Connected: {len(models)} model(s)." if models else
                                     "Connected, but the server listed no models. Type the model name.")
            keep = self.model.currentText()
            self.model.blockSignals(True)
            self.model.clear()
            self.model.addItems(models)
            self.model.blockSignals(False)
            self.model.setCurrentText(keep or (models[0] if models else ""))
            self._store()

        def failed(msg):
            cur = self._current()
            if cur and cur["name"] == name:
                self.test_result.setText(msg)

        run_async(assistant.provider_models, dict(p), on_done=done, on_error=failed)
        if assistant.is_copilot(p) and p.get("api_key") and not p.get("account"):
            def named(login, token=p["api_key"]):
                if login and p.get("api_key") == token:
                    p["account"] = login
                    if self._current() is p:
                        self.account.setText(f"Signed in as {login}")
            run_async(copilot.account_name, p["api_key"], on_done=named)

    def accept(self) -> None:
        self._store()
        blank = [p["name"] for p in self.items if not p["name"].strip()
                 or not (p["base_url"].strip() or assistant.is_copilot(p))]
        if blank:
            QMessageBox.warning(self, "AI Providers", "Every provider needs a name and a server URL:\n\n"
                                + "\n".join(n or "(unnamed)" for n in blank))
            return
        assistant.save_providers(self.settings, self.items, self.active)
        super().accept()


def fill_provider_menu(menu: QMenu, settings, on_switch, on_manage) -> None:
    """A menu of providers with the active one checked, plus Manage."""
    menu.clear()
    active = assistant.active_provider(settings)
    for p in assistant.providers(settings):
        a = menu.addAction(p["name"] + (f"  ({p['model']})" if p["model"] else ""))
        a.setCheckable(True)
        a.setChecked(bool(active) and p["name"] == active["name"])
        a.triggered.connect(lambda _c=False, n=p["name"]: on_switch(n))
    if not menu.isEmpty():
        menu.addSeparator()
    menu.addAction("Manage Providers...", on_manage)

