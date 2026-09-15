"""Settings, command palette, and refactoring preview dialogs."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFontComboBox, QFormLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSpinBox,
                             QVBoxLayout)

from .theme import mono_font


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        s = settings

        def combo(options, current):
            c = QComboBox()
            for label, value in options:
                c.addItem(label, value)
            c.setCurrentIndex(max(0, [v for _, v in options].index(current) if current in [v for _, v in options] else 0))
            return c

        def check(key, label):
            c = QCheckBox(label)
            c.setChecked(bool(s.get(key)))
            return c

        def spin(key, lo, hi):
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setValue(int(s.get(key)))
            return sp

        self.theme = combo([("Dark", "dark"), ("Light", "light")], s.get("theme"))
        self.font = QFontComboBox()
        self.font.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        self.font.setCurrentFont(QFont(s.get("font_family")) if s.get("font_family") else mono_font())
        self.font_size = spin("font_size", 6, 40)
        self.tab_width = spin("tab_width", 1, 12)
        self.edge = spin("edge_column", 0, 400)
        self.use_tabs = check("use_tabs", "Indent with tabs")
        self.wrap = check("word_wrap", "Wrap long lines")
        self.whitespace = check("show_whitespace", "Show whitespace")
        self.brackets = check("auto_close_brackets", "Auto-close brackets and quotes")
        self.lint = check("lint", "Check code for errors as you type (pyflakes)")
        self.check_imports = check("check_imports", "Detect imports that aren't installed")
        self.auto_install = combo([("Ask before installing", "ask"), ("Install automatically", "always"),
                                   ("Never offer", "never")], s.get("auto_install"))
        self.formatter = combo([("Black", "black"), ("Ruff", "ruff")], s.get("formatter"))
        self.format_on_save = check("format_on_save", "Format on save (once the formatter is downloaded)")
        self.jmc = check("just_my_code", "Just My Code (don't step into libraries)")
        self.run_cwd = combo([("The file's folder", "file"), ("The project folder", "project")], s.get("run_cwd"))
        self.clear_output = check("clear_output_on_run", "Clear output before each run")
        self.save_before_run = check("save_before_run", "Save the file before running")

        for label, widget in (("Theme", self.theme), ("Editor font", self.font), ("Font size", self.font_size),
                              ("Tab width", self.tab_width), ("Ruler column (0 = off)", self.edge),
                              ("", self.use_tabs), ("", self.wrap), ("", self.whitespace), ("", self.brackets),
                              ("", self.lint), ("Missing packages", self.check_imports),
                              ("When packages are missing", self.auto_install), ("Formatter", self.formatter),
                              ("", self.format_on_save), ("Debugger", self.jmc), ("Run programs from", self.run_cwd),
                              ("", self.clear_output), ("", self.save_before_run)):
            form.addRow(label, widget)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def accept(self) -> None:
        s = self.settings
        values = {
            "theme": self.theme.currentData(), "font_family": self.font.currentFont().family(),
            "font_size": self.font_size.value(), "tab_width": self.tab_width.value(),
            "edge_column": self.edge.value(), "use_tabs": self.use_tabs.isChecked(),
            "word_wrap": self.wrap.isChecked(), "show_whitespace": self.whitespace.isChecked(),
            "auto_close_brackets": self.brackets.isChecked(), "lint": self.lint.isChecked(),
            "check_imports": self.check_imports.isChecked(), "auto_install": self.auto_install.currentData(),
            "formatter": self.formatter.currentData(), "format_on_save": self.format_on_save.isChecked(),
            "just_my_code": self.jmc.isChecked(), "run_cwd": self.run_cwd.currentData(),
            "clear_output_on_run": self.clear_output.isChecked(), "save_before_run": self.save_before_run.isChecked(),
        }
        for k, v in values.items():
            s._data[k] = v
        s.save()
        super().accept()


def fuzzy_score(query: str, text: str) -> int | None:
    """Subsequence match; higher is better. None means no match."""
    if not query:
        return 0
    q, t = query.lower(), text.lower()
    if q in t:
        return 1000 - t.index(q) * 2 - len(t)
    pos, score, streak = 0, 0, 0
    for ch in q:
        found = t.find(ch, pos)
        if found < 0:
            return None
        streak = streak + 1 if found == pos else 0
        score += 5 + streak * 3 - min(found - pos, 10)
        pos = found + 1
    return score - len(t) // 4


class CommandPalette(QDialog):
    """Ctrl+Shift+P for commands, Ctrl+P for files: type to filter, Enter to run."""

    def __init__(self, parent, entries, placeholder: str):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("Palette")
        self.entries = entries  # [(label, detail, callback)]
        self.setFixedWidth(min(680, max(420, parent.width() // 2)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.edit = QLineEdit(placeholderText=placeholder)
        lay.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setMinimumHeight(360)
        lay.addWidget(self.list)
        self.edit.textChanged.connect(self._filter)
        self.edit.installEventFilter(self)
        self.list.itemActivated.connect(self._run)
        self._filter("")
        top = parent.mapToGlobal(parent.rect().topLeft())
        self.move(top.x() + (parent.width() - self.width()) // 2, top.y() + 60)

    def _filter(self, text: str) -> None:
        self.list.clear()
        scored = []
        for i, (label, detail, cb) in enumerate(self.entries):
            sc = fuzzy_score(text, label)
            if sc is not None:
                scored.append((-sc, i, label, detail, cb))
        scored.sort()
        for _, _, label, detail, cb in scored[:300]:
            item = QListWidgetItem(f"{label}    {detail}" if detail else label)
            item.setData(Qt.ItemDataRole.UserRole, cb)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, e):
        if e.type() == e.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self.list.keyPressEvent(e)
                return True
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.list.currentItem():
                    self._run(self.list.currentItem())
                return True
        return False

    def _run(self, item) -> None:
        cb = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if cb:
            cb()


class RenamePreviewDialog(QDialog):
    def __init__(self, parent, diff: str, files: int):
        super().__init__(parent)
        self.setWindowTitle("Rename Symbol - Preview")
        self.resize(820, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"This rename changes {files} file{'s' if files != 1 else ''}:"))
        view = QPlainTextEdit(diff)
        view.setReadOnly(True)
        view.setFont(mono_font("", 10))
        lay.addWidget(view, 1)
        buttons = QDialogButtonBox()
        apply = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        apply.setProperty("primary", True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
