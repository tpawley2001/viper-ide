"""The "update available" dialog: release notes, verified download, hand-off to the installer."""
from __future__ import annotations

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QProgressBar,
                             QVBoxLayout)

from . import __version__, updater
from .workers import run_async


class UpdateDialog(QDialog):
    INSTALLING = 2  # exec() result: the installer is running and the IDE should close
    SKIPPED = 3

    def __init__(self, release: updater.Release, settings, parent=None):
        super().__init__(parent)
        self.release = release
        self.settings = settings
        self.setWindowTitle("Update Viper IDE")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        size = f" ({release.size / 1e6:.0f} MB)" if release.size else ""
        head = QLabel(f"<h3>Viper IDE {release.version} is available</h3>"
                      f"You have {__version__}. The update downloads{size}, is checked against its published "
                      f"checksum, and installs in the background. Viper closes and reopens when it's done; "
                      f"you'll be asked to save any unsaved files first.")
        head.setWordWrap(True)
        lay.addWidget(head)
        if release.notes:
            notes = QPlainTextEdit(release.notes)
            notes.setReadOnly(True)
            notes.setMaximumHeight(160)
            lay.addWidget(notes)
        self.status = QLabel()
        self.status.setObjectName("Dim")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.hide()
        lay.addWidget(self.bar)
        self.buttons = QDialogButtonBox()
        self.go = self.buttons.addButton("Update Now", QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.setProperty("primary", True)
        self.skip = self.buttons.addButton("Skip This Version", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.later = self.buttons.addButton("Later", QDialogButtonBox.ButtonRole.RejectRole)
        self.go.clicked.connect(self.start)
        self.skip.clicked.connect(self._skip)
        self.later.clicked.connect(self.reject)
        lay.addWidget(self.buttons)

    def _skip(self) -> None:
        self.settings.set("skipped_update", self.release.version)
        self.done(self.SKIPPED)

    def start(self) -> None:
        for b in (self.go, self.skip):
            b.setEnabled(False)
        self.bar.show()
        self.bar.setRange(0, 0)
        self.status.setText(f"Downloading from {self.release.base} ...")
        run_async(updater.download, self.release, on_progress=self._progress, on_done=self._downloaded,
                  on_error=self._failed)

    def _progress(self, value) -> None:
        done, total = value
        self.bar.setRange(0, total)
        self.bar.setValue(done)
        self.status.setText(f"Downloading... {done / 1e6:.1f} of {total / 1e6:.1f} MB")

    def _downloaded(self, path) -> None:
        self.bar.setRange(0, 0)
        self.status.setText("Starting the installer. Viper will close and reopen on the new version.")
        try:
            updater.install(path)
        except (updater.UpdateError, OSError) as e:
            self._failed(str(e))
            return
        self.done(self.INSTALLING)

    def _failed(self, msg: str) -> None:
        self.bar.hide()
        self.go.setEnabled(True)
        self.skip.setEnabled(True)
        self.status.setText("")
        QMessageBox.warning(self, "Update Viper IDE", f"The update didn't install:\n\n{msg}")
