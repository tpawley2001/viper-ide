"""Package manager panel and the Python download dialog."""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSplitter,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import interpreters
from .imports import pypi_info, requirement_name
from .runner import OutputView
from .workers import run_async


class PackagesPanel(QWidget):
    packages_changed = pyqtSignal()

    def __init__(self, pip, get_interpreter, get_project, theme, parent=None):
        super().__init__(parent)
        self.pip = pip
        self.get_interpreter = get_interpreter
        self.get_project = get_project
        self._rows: list[dict] = []
        self._latest: dict[str, str] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        self.interp_label = QLabel()
        self.interp_label.setObjectName("Dim")
        lay.addWidget(self.interp_label)

        row = QHBoxLayout()
        self.spec = QLineEdit(placeholderText="Package to install, e.g.  requests   or   numpy==2.1   or   rich pandas")
        self.spec.returnPressed.connect(self.install)
        self.spec.textChanged.connect(lambda: self.info.setText(""))
        row.addWidget(self.spec, 1)
        lookup = QPushButton("Look Up")
        lookup.clicked.connect(self.lookup)
        row.addWidget(lookup)
        install = QPushButton("Install")
        install.setProperty("primary", True)
        install.clicked.connect(self.install)
        row.addWidget(install)
        lay.addLayout(row)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.info)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.filter = QLineEdit(placeholderText="Filter installed packages")
        self.filter.textChanged.connect(self._render)
        ll.addWidget(self.filter)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Package", "Installed", "Latest"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        ll.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        for label, fn in (("Refresh", self.refresh), ("Check Updates", self.check_updates),
                          ("Upgrade", self.upgrade_selected), ("Uninstall", self.uninstall_selected),
                          ("Install requirements.txt...", self.install_requirements),
                          ("Export requirements.txt...", self.export_requirements)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        buttons.addStretch()
        ll.addLayout(buttons)
        split.addWidget(left)
        self.log = OutputView(theme)
        split.addWidget(self.log)
        split.setSizes([520, 380])
        lay.addWidget(split, 1)
        pip.output.connect(lambda text: self.log.append_text(text))

    def interpreter_changed(self) -> None:
        interp = self.get_interpreter()
        self.interp_label.setText(f"Packages in {interp.label()}  -  {interp.path}" if interp
                                  else "No interpreter selected")
        self._latest.clear()
        self.refresh()

    def refresh(self) -> None:
        interp = self.get_interpreter()
        if not interp:
            self._rows = []
            self._render()
            return
        self.pip.list_installed(interp.path, self._set_rows)

    def _set_rows(self, rows: list[dict]) -> None:
        self._rows = rows
        self._render()

    def _render(self) -> None:
        needle = self.filter.text().lower()
        rows = [r for r in self._rows if needle in r["name"].lower()]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            latest = self._latest.get(r["name"].lower(), "")
            for col, value in enumerate((r["name"], r["version"], latest)):
                self.table.setItem(i, col, QTableWidgetItem(value))
        self.table.setSortingEnabled(True)

    def _selected_names(self) -> list[str]:
        return sorted({self.table.item(i.row(), 0).text() for i in self.table.selectedIndexes()})

    def _need_interp(self):
        interp = self.get_interpreter()
        if not interp:
            QMessageBox.information(self, "Packages", "Select or download a Python interpreter first.")
        return interp

    def _after_change(self, ok: bool, _text: str) -> None:
        self.refresh()
        if ok:
            self.packages_changed.emit()

    def install(self) -> None:
        interp = self._need_interp()
        specs = self.spec.text().split()
        if interp and specs:
            self.pip.install(interp.path, specs, self._after_change)
            self.spec.clear()

    def lookup(self) -> None:
        name = requirement_name(self.spec.text().split()[0]) if self.spec.text().split() else None
        if not name:
            return
        self.info.setText("Looking up on PyPI...")

        def show(info):
            if info is None:
                self.info.setText(f"<b>{name}</b> was not found on PyPI.")
            else:
                self.info.setText(f"<b>{info['name']} {info['version']}</b> &mdash; {info['summary']}"
                                  + (f"<br>Requires Python {info['requires_python']}" if info['requires_python'] else ""))

        run_async(pypi_info, name, on_done=show, on_error=lambda msg: self.info.setText(f"PyPI lookup failed: {msg}"))

    def check_updates(self) -> None:
        interp = self._need_interp()
        if not interp:
            return

        def done(rows):
            self._latest = {r["name"].lower(): r.get("latest_version", "") for r in rows}
            self._render()
            self.info.setText(f"{len(rows)} package{'s' if len(rows) != 1 else ''} can be upgraded."
                              if rows else "Everything is up to date.")

        self.pip.list_installed(interp.path, done, outdated=True)

    def upgrade_selected(self) -> None:
        interp = self._need_interp()
        names = self._selected_names()
        if interp and names:
            self.pip.install(interp.path, names, self._after_change, upgrade=True)

    def uninstall_selected(self) -> None:
        interp = self._need_interp()
        names = self._selected_names()
        if interp and names and QMessageBox.question(
                self, "Uninstall", f"Uninstall {', '.join(names)} from {interp.label()}?") == QMessageBox.StandardButton.Yes:
            self.pip.uninstall(interp.path, names, self._after_change)

    def install_requirements(self, path: str | None = None) -> None:
        interp = self._need_interp()
        if not interp:
            return
        project = self.get_project()
        default = os.path.join(project, "requirements.txt") if project else ""
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Requirements file", default,
                                                  "Requirements (*.txt);;All files (*)")
        if path:
            self.pip.install_requirements(interp.path, path, self._after_change)

    def export_requirements(self) -> None:
        interp = self._need_interp()
        if not interp:
            return
        project = self.get_project() or ""
        path, _ = QFileDialog.getSaveFileName(self, "Export requirements", os.path.join(project, "requirements.txt"),
                                              "Requirements (*.txt)")
        if not path:
            return

        def done(ok, text):
            if ok:
                lines = [ln for ln in text.splitlines() if ln and not ln.startswith(("#", "WARNING"))]
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                self.info.setText(f"Wrote {len(lines)} packages to {path}")

        self.pip.run(interp.path, ["-m", "pip", "freeze"], "Exporting requirements", done, quiet=True)


class PythonDownloadDialog(QDialog):
    """Pick a CPython release and install it per-user from python.org."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download Python")
        self.setMinimumWidth(460)
        self.interpreter = None
        lay = QVBoxLayout(self)
        intro = QLabel("Viper downloads the official installer from python.org, checks its digital "
                       "signature, and installs it for your user account only (no administrator rights, "
                       "nothing added to PATH).")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("Version:"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(160)
        row.addWidget(self.combo, 1)
        lay.addLayout(row)
        self.status = QLabel("Finding available versions...")
        self.status.setObjectName("Dim")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        lay.addWidget(self.bar)
        self.buttons = QDialogButtonBox()
        self.go = self.buttons.addButton("Download and Install", QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.setProperty("primary", True)
        self.go.setEnabled(False)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.start)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        if os.name != "nt":
            self.status.setText("Automatic Python downloads are available on Windows. On this system, install "
                                "Python with your package manager, then use Browse... to select it.")
            self.bar.hide()
            return
        run_async(interpreters.available_python_versions, on_done=self._versions, on_error=self._failed,
                  on_progress=lambda msg: self.status.setText(str(msg)))

    def _versions(self, versions: list[str]) -> None:
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        if not versions:
            self._failed("python.org didn't list any installable versions.")
            return
        for i, v in enumerate(versions):
            self.combo.addItem(f"Python {v}" + ("  (latest)" if i == 0 else ""), v)
        self.status.setText("Choose a version. The newest one is right for most people; pick an older one if a "
                            "package you need doesn't support it yet.")
        self.go.setEnabled(True)

    def _failed(self, msg: str) -> None:
        self.bar.setRange(0, 1)
        self.status.setText(f"Couldn't download Python: {msg}")
        self.go.setEnabled(bool(self.combo.count()))

    def start(self) -> None:
        version = self.combo.currentData()
        if not version:
            return
        self.go.setEnabled(False)
        self.combo.setEnabled(False)
        self.bar.setRange(0, 0)
        run_async(interpreters.install_python, version, on_done=self._done, on_error=self._failed,
                  on_progress=self._progress)

    def _progress(self, value) -> None:
        if isinstance(value, tuple):
            done, total = value
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.status.setText(f"Downloading... {done / 1e6:.1f} of {total / 1e6:.1f} MB")
        else:
            self.bar.setRange(0, 0)
            self.status.setText(str(value))

    def _done(self, interp) -> None:
        self.interpreter = interp
        self.accept()
