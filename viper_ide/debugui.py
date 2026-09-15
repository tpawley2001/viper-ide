"""Debugger front end: the socket session with viper_dbg.py and the debug panel."""
from __future__ import annotations

import json
import os

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtNetwork import QHostAddress, QTcpServer
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSplitter,
                             QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .paths import helper
from .runner import OutputView
from .theme import mono_font

PLACEHOLDER = "…"


class DebugSession(QObject):
    stopped = pyqtSignal(dict)
    running = pyqtSignal()
    exited = pyqtSignal(int)
    variables = pyqtSignal(dict)
    evaluated = pyqtSignal(dict)
    state_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server: QTcpServer | None = None
        self.sock = None
        self._buf = b""
        self._req = 0
        self._pending_bps: dict[str, list[dict]] = {}
        self.active = False
        self.paused = False
        self.frames: list[dict] = []

    def start(self, run_panel, interpreter: str, script: str, args: list[str], cwd: str, env: dict,
              breakpoints: dict[str, list[dict]], just_my_code: bool) -> bool:
        self.finish()
        server = QTcpServer(self)
        if not server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0):
            return False
        server.newConnection.connect(self._on_connection)
        self.server = server
        self._pending_bps = breakpoints
        argv = ["-u", str(helper("viper_dbg.py")), "--port", str(server.serverPort())]
        if not just_my_code:
            argv.append("--no-jmc")
        argv += ["--", script] + list(args)
        self.active, self.paused = True, False
        self.state_changed.emit()
        run_panel.start(interpreter, argv, cwd, env, f"Debug {os.path.basename(script)}")
        return True

    def _on_connection(self) -> None:
        if self.server is None:
            return
        sock = self.server.nextPendingConnection()
        if self.sock is not None:
            sock.close()
            return
        self.sock = sock
        sock.readyRead.connect(self._read)
        self.server.close()

    def _read(self) -> None:
        if not self.sock:
            return
        self._buf += bytes(self.sock.readAll())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                self._handle(json.loads(line))
            except ValueError:
                continue

    def _handle(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "hello":
            for path, lines in self._pending_bps.items():
                self.send({"cmd": "setBreakpoints", "file": path, "lines": lines})
            self.send({"cmd": "start"})
        elif kind == "stopped":
            self.paused = True
            self.frames = ev.get("frames", [])
            self.state_changed.emit()
            self.stopped.emit(ev)
        elif kind == "running":
            self.paused = False
            self.state_changed.emit()
            self.running.emit()
        elif kind == "variables":
            self.variables.emit(ev)
        elif kind == "evaluated":
            self.evaluated.emit(ev)
        elif kind == "exited":
            self.exited.emit(int(ev.get("code", 0)))

    def send(self, obj: dict) -> None:
        if self.sock is not None and self.sock.isOpen():
            self.sock.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.sock.flush()

    def command(self, name: str) -> None:
        if self.paused:
            self.paused = False
            self.state_changed.emit()
            self.send({"cmd": name})

    def pause(self) -> None:
        if self.active and not self.paused:
            self.send({"cmd": "pause"})

    def set_breakpoints(self, path: str, lines: list[dict]) -> None:
        if self.active:
            self._pending_bps[path] = lines
            self.send({"cmd": "setBreakpoints", "file": path, "lines": lines})

    def request_variables(self, frame: int, expr: str | None) -> int:
        self._req += 1
        self.send({"cmd": "variables", "frame": frame, "expr": expr, "req": self._req})
        return self._req

    def evaluate(self, frame: int, expr: str) -> int:
        self._req += 1
        self.send({"cmd": "evaluate", "frame": frame, "expr": expr, "req": self._req})
        return self._req

    def terminate(self) -> None:
        self.send({"cmd": "terminate"})

    def finish(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock.deleteLater()
        if self.server is not None:
            self.server.close()
            self.server.deleteLater()
        self.sock = self.server = None
        self._buf = b""
        was_active = self.active
        self.active = self.paused = False
        self.frames = []
        if was_active:
            self.state_changed.emit()


class DebugPanel(QWidget):
    frame_selected = pyqtSignal(str, int)

    def __init__(self, session: DebugSession, theme, parent=None):
        super().__init__(parent)
        self.session = session
        self.frame = 0
        self._pending: dict[int, QTreeWidgetItem | None] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.toolbar = QHBoxLayout()
        self.toolbar.setContentsMargins(4, 2, 4, 2)
        self.status = QLabel("Not debugging")
        self.status.setObjectName("Dim")
        lay.addLayout(self.toolbar)

        split = QSplitter(Qt.Orientation.Vertical)
        stack_box = QWidget()
        sl = QVBoxLayout(stack_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self._heading("CALL STACK"))
        self.stack = QListWidget()
        self.stack.currentRowChanged.connect(self._frame_changed)
        sl.addWidget(self.stack)
        split.addWidget(stack_box)

        var_box = QWidget()
        vl = QVBoxLayout(var_box)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self._heading("VARIABLES"))
        self.vars = QTreeWidget()
        self.vars.setHeaderLabels(["Name", "Value", "Type"])
        self.vars.setColumnWidth(0, 150)
        self.vars.setColumnWidth(1, 260)
        self.vars.itemExpanded.connect(self._expand)
        vl.addWidget(self.vars)
        split.addWidget(var_box)

        con_box = QWidget()
        cl = QVBoxLayout(con_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self._heading("DEBUG CONSOLE"))
        self.console = OutputView(theme)
        cl.addWidget(self.console, 1)
        self.eval_input = QLineEdit(placeholderText="Evaluate in the selected frame (Enter)")
        self.eval_input.setFont(mono_font("", 10))
        self.eval_input.returnPressed.connect(self._evaluate)
        cl.addWidget(self.eval_input)
        split.addWidget(con_box)
        split.setSizes([140, 320, 180])
        lay.addWidget(self.status)
        lay.addWidget(split, 1)

        session.stopped.connect(self._stopped)
        session.running.connect(self._running)
        session.variables.connect(self._variables)
        session.evaluated.connect(self._evaluated)
        self.set_enabled(False)

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Dim")
        label.setContentsMargins(8, 6, 8, 2)
        return label

    def set_actions(self, actions) -> None:
        for action in actions:
            b = QToolButton()
            b.setDefaultAction(action)
            b.setAutoRaise(True)
            self.toolbar.addWidget(b)
        self.toolbar.addStretch()

    def set_enabled(self, on: bool) -> None:
        self.vars.setEnabled(on)
        self.eval_input.setEnabled(on)

    def clear(self) -> None:
        self.stack.clear()
        self.vars.clear()
        self.status.setText("Not debugging")
        self.set_enabled(False)

    def _stopped(self, ev: dict) -> None:
        reason = ev.get("reason", "")
        top = ev["frames"][0] if ev.get("frames") else None
        where = f" at {os.path.basename(top['file'])}:{top['line']}" if top else ""
        self.status.setText(f"Paused ({reason}){where}")
        if ev.get("exception"):
            self.console.append_text(f"Uncaught exception: {ev['exception']}\n", "err")
        self.stack.blockSignals(True)
        self.stack.clear()
        for f in ev.get("frames", []):
            item = QListWidgetItem(f"{f['name']}   {os.path.basename(f['file'])}:{f['line']}")
            item.setToolTip(f"{f['file']}:{f['line']}")
            self.stack.addItem(item)
        self.stack.setCurrentRow(0)
        self.stack.blockSignals(False)
        self.frame = 0
        self._pending.clear()
        self._fill(None, ev.get("variables", []))
        self.set_enabled(True)

    def _running(self) -> None:
        self.status.setText("Running...")
        self.set_enabled(False)

    def _frame_changed(self, row: int) -> None:
        if row < 0 or row >= len(self.session.frames):
            return
        self.frame = row
        f = self.session.frames[row]
        self._pending[self.session.request_variables(row, None)] = None
        self.frame_selected.emit(f["file"], f["line"])

    def _fill(self, parent: QTreeWidgetItem | None, items: list[dict]) -> None:
        if parent is None:
            self.vars.clear()
        else:
            parent.takeChildren()
        for v in items:
            it = QTreeWidgetItem([v["name"], v["value"], v["type"]])
            it.setToolTip(1, v["value"])
            it.setData(0, Qt.ItemDataRole.UserRole, v.get("expr"))
            if v.get("children") and v.get("expr"):
                it.addChild(QTreeWidgetItem([PLACEHOLDER]))
            if parent is None:
                self.vars.addTopLevelItem(it)
            else:
                parent.addChild(it)

    def _expand(self, item: QTreeWidgetItem) -> None:
        if item.childCount() == 1 and item.child(0).text(0) == PLACEHOLDER:
            expr = item.data(0, Qt.ItemDataRole.UserRole)
            self._pending[self.session.request_variables(self.frame, expr)] = item

    def _variables(self, ev: dict) -> None:
        req = ev.get("req")
        if req not in self._pending:
            return
        self._fill(self._pending.pop(req), ev.get("items", []))

    def _evaluate(self) -> None:
        expr = self.eval_input.text().strip()
        if not expr or not self.session.paused:
            return
        self.eval_input.clear()
        self.console.append_text(f"> {expr}\n", "input")
        self.session.evaluate(self.frame, expr)

    def _evaluated(self, ev: dict) -> None:
        if ev.get("value"):
            self.console.append_text(ev["value"] + "\n", "out" if ev.get("ok") else "err")
        self._pending[self.session.request_variables(self.frame, None)] = None
