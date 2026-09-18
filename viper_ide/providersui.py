"""Manage AI Providers: named OpenAI-compatible servers, each with its own URL, key and model."""
from __future__ import annotations

from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from . import assistant
from .workers import run_async


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
        test = QPushButton("Test && Load Models")
        test.clicked.connect(self._test)
        self.test_result = QLabel()
        self.test_result.setObjectName("Dim")
        self.test_result.setWordWrap(True)
        self.use = QPushButton("Use This Provider")
        self.use.clicked.connect(self._make_active)
        for label, w in (("Name", self.name), ("Server URL", self.url), ("API key", self.key),
                         ("Key from env var", self.env), ("Model", self.model), ("", test),
                         ("", self.test_result), ("", self.use)):
            form.addRow(label, w)
        for w in (self.name, self.url, self.key, self.env):
            w.textEdited.connect(self._store)
        self.model.currentTextChanged.connect(self._store)

        body = QHBoxLayout()
        body.addLayout(left, 2)
        body.addWidget(self.form_box, 3)
        lay = QVBoxLayout(self)
        note = QLabel("Keys are saved in Viper's settings file in plain text. Leave the key blank for local "
                      "servers, or name an environment variable to read it from instead.")
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

        run_async(assistant.list_models, p["base_url"], assistant.api_key(p), on_done=done, on_error=failed)

    def accept(self) -> None:
        self._store()
        blank = [p["name"] for p in self.items if not p["name"].strip() or not p["base_url"].strip()]
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

