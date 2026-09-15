"""Small shared widgets."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton


class InfoBar(QFrame):
    """A dismissible strip with a message and action buttons.

    Messages carry a tag so one feature (say, missing imports) can clear its
    own message without wiping another feature's.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoBar")
        self.tag: str | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 6, 5)
        lay.setSpacing(6)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(6)
        lay.addLayout(self._buttons)
        close = QToolButton(text="✕")
        close.setToolTip("Dismiss")
        close.setAutoRaise(True)
        close.clicked.connect(lambda: self.clear())
        lay.addWidget(close)
        self.hide()

    def show_message(self, level: str, html: str, actions=(), tag: str | None = None) -> None:
        self.tag = tag
        self.setProperty("level", level)
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setText(html)
        while self._buttons.count():
            w = self._buttons.takeAt(0).widget()
            if w:
                w.deleteLater()
        for label, callback, primary in actions:
            b = QPushButton(label)
            b.setProperty("primary", bool(primary))
            b.clicked.connect(callback)
            self._buttons.addWidget(b)
        self.show()

    def clear(self, tag: str | None = None) -> None:
        if tag is None or tag == self.tag:
            self.tag = None
            self.hide()
