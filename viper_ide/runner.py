"""Run output, the interactive Python console, and the terminal."""
from __future__ import annotations

import ast
import codecs
import os
import re
import shutil
import subprocess
import time

from PyQt6.QtCore import QProcess, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QToolButton, QVBoxLayout,
                             QWidget)

from .icons import icon
from .packages import qt_env
from .paths import child_env
from .theme import mono_font

IS_WIN = os.name == "nt"
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
LOCATION = re.compile(r'File "([^"]+)", line (\d+)|^([A-Za-z]:[\\/][^:]+|/[^:]+):(\d+):')


def activated_env(interp) -> dict:
    """Environment with the interpreter (and its Scripts/bin) first on PATH, like an activated venv."""
    bindir = os.path.dirname(interp.path)
    extra = [bindir]
    if IS_WIN and not interp.is_venv:
        extra.append(os.path.join(bindir, "Scripts"))
    env = child_env()
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    if interp.is_venv:
        env["VIRTUAL_ENV"] = interp.prefix
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


class OutputView(QPlainTextEdit):
    open_location = pyqtSignal(str, int)

    def __init__(self, theme, font_size=10, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setMaximumBlockCount(20000)
        self.setFont(mono_font("", font_size))
        self.setMouseTracking(True)
        self.set_theme(theme)

    def set_theme(self, theme) -> None:
        self.colors = {"out": theme["fg"], "err": theme["error"], "info": theme["fg_dim"], "ok": theme["ok"],
                       "input": theme["info"]}

    def append_text(self, text: str, kind: str = "out") -> None:
        text = ANSI.sub("", text).replace("\r\n", "\n")
        text = re.sub(r"[^\n]*\r(?!\n)", "", text)  # progress bars redraw with a bare CR
        if not text:
            return
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.colors.get(kind, self.colors["out"])))
        cursor.insertText(text, fmt)
        if at_bottom:
            bar.setValue(bar.maximum())

    def _location_at(self, pos):
        line = self.cursorForPosition(pos).block().text()
        m = LOCATION.search(line)
        if not m:
            return None
        path, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return (path, int(num)) if os.path.isfile(path) else None

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        loc = self._location_at(e.position().toPoint())
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if loc else Qt.CursorShape.IBeamCursor)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if e.button() == Qt.MouseButton.LeftButton and not self.textCursor().hasSelection():
            loc = self._location_at(e.position().toPoint())
            if loc:
                self.open_location.emit(*loc)


class _ProcessPanel(QWidget):
    """Shared plumbing: a QProcess wired to an OutputView with UTF-8-safe decoding."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self.view = OutputView(theme)
        self._decoders = {}

    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def _spawn(self, program: str, args: list[str], cwd: str | None, env: dict) -> QProcess:
        proc = QProcess(self)
        proc.setProcessEnvironment(qt_env(env))
        if cwd:
            proc.setWorkingDirectory(cwd)
        self._decoders = {k: codecs.getincrementaldecoder("utf-8")("replace") for k in ("out", "err")}
        proc.readyReadStandardOutput.connect(lambda p=proc: self._read(p, "out"))
        proc.readyReadStandardError.connect(lambda p=proc: self._read(p, "err"))
        proc.errorOccurred.connect(lambda err, p=proc: self._error(p, err))
        self.proc = proc
        proc.start(program, args)
        return proc

    def _read(self, proc: QProcess, kind: str) -> None:
        raw = proc.readAllStandardOutput() if kind == "out" else proc.readAllStandardError()
        text = self._decoders[kind].decode(bytes(raw))
        self.on_output(text, kind)

    def on_output(self, text: str, kind: str) -> None:
        self.view.append_text(text, kind)

    def _error(self, proc: QProcess, err) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self.view.append_text(f"Could not start {proc.program()}: {proc.errorString()}\n", "err")

    def write_line(self, text: str) -> None:
        if self.running():
            self.proc.write((text + ("\r\n" if IS_WIN and isinstance(self, TerminalPanel) else "\n")).encode("utf-8"))

    def stop(self) -> None:
        if self.running():
            self.proc.kill()
            self.proc.waitForFinished(2000)

    @staticmethod
    def _tool(name: str, tip: str, fn) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon(name))
        b.setToolTip(tip)
        b.clicked.connect(fn)
        return b


class RunPanel(_ProcessPanel):
    started = pyqtSignal()
    finished = pyqtSignal(int, str)

    def __init__(self, theme, settings, parent=None):
        super().__init__(theme, parent)
        self.settings = settings
        self._tail = ""
        self._t0 = 0.0
        self._last = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 6, 2)
        self.title = QLabel("Nothing running")
        self.title.setObjectName("Dim")
        bar.addWidget(self.title, 1)
        self.rerun_btn = self._tool("restart", "Run again", self.rerun)
        self.stop_btn = self._tool("stop", "Stop (Shift+F5)", self.stop)
        bar.addWidget(self.rerun_btn)
        bar.addWidget(self.stop_btn)
        bar.addWidget(self._tool("clear", "Clear output", self.view.clear))
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)
        self.stdin = QLineEdit(placeholderText="Program input: type here and press Enter")
        self.stdin.returnPressed.connect(self._send_stdin)
        self.stdin.setEnabled(False)
        lay.addWidget(self.stdin)
        self.stop_btn.setEnabled(False)

    def start(self, program: str, args: list[str], cwd: str, env: dict, title: str) -> None:
        self.stop()
        if self.settings.get("clear_output_on_run"):
            self.view.clear()
        self._tail = ""
        self._last = (program, list(args), cwd, env, title)
        self.title.setText(title)
        self.view.append_text(f"{program} {' '.join(args)}\n", "info")
        proc = self._spawn(program, args, cwd, env)
        proc.finished.connect(lambda code, status, p=proc: self._finished(p, code, status))
        self._t0 = time.monotonic()
        self.stop_btn.setEnabled(True)
        self.stdin.setEnabled(True)
        self.stdin.setFocus()
        self.started.emit()

    def rerun(self) -> None:
        if self._last:
            self.start(*self._last)

    def on_output(self, text: str, kind: str) -> None:
        self._tail = (self._tail + text)[-65536:]
        self.view.append_text(text, kind)

    def _error(self, proc, err) -> None:
        super()._error(proc, err)
        if err == QProcess.ProcessError.FailedToStart:
            self._finished(proc, -1, QProcess.ExitStatus.CrashExit)

    def _finished(self, proc, code, status) -> None:
        if proc is not self.proc:
            return
        self.proc = None
        elapsed = time.monotonic() - self._t0
        crashed = status == QProcess.ExitStatus.CrashExit and code != -1
        msg = "terminated" if crashed else f"exited with code {code}"
        self.view.append_text(f"\nProcess {msg} ({elapsed:.1f}s)\n", "ok" if code == 0 and not crashed else "err")
        self.stop_btn.setEnabled(False)
        self.stdin.setEnabled(False)
        self.title.setText(f"{self._last[4] if self._last else ''} - finished")
        self.finished.emit(code if not crashed else -1, self._tail)

    def _send_stdin(self) -> None:
        text = self.stdin.text()
        self.stdin.clear()
        if self.running():
            self.view.append_text(text + "\n", "input")
            self.proc.write((text + "\n").encode("utf-8"))


class ConsolePanel(_ProcessPanel):
    """A persistent `python -i` session for trying code and running selections."""

    def __init__(self, theme, get_interpreter, parent=None):
        super().__init__(theme, parent)
        self.get_interpreter = get_interpreter
        self.history: list[str] = []
        self._hist_pos = 0
        self._interp_path = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 6, 2)
        self.title = QLabel("Python console")
        self.title.setObjectName("Dim")
        bar.addWidget(self.title, 1)
        bar.addWidget(self._tool("restart", "Restart console", self.restart))
        bar.addWidget(self._tool("clear", "Clear", self.view.clear))
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.setContentsMargins(6, 2, 6, 4)
        prompt = QLabel(">>>")
        prompt.setFont(mono_font("", 10))
        row.addWidget(prompt)
        self.input = QLineEdit()
        self.input.setFont(mono_font("", 10))
        self.input.setPlaceholderText("Python expression or statement")
        self.input.returnPressed.connect(self._submit)
        self.input.installEventFilter(self)
        row.addWidget(self.input, 1)
        lay.addLayout(row)

    def ensure_started(self) -> bool:
        interp = self.get_interpreter()
        if interp is None:
            self.view.append_text("No Python interpreter selected.\n", "err")
            return False
        if self.running() and self._interp_path == interp.path:
            return True
        self.stop()
        self._interp_path = interp.path
        self.title.setText(f"Python console - {interp.label()}")
        self.view.append_text(f"Python {interp.version} ({interp.path})\n", "info")
        proc = self._spawn(interp.path, ["-i", "-q", "-u"], os.getcwd(), activated_env(interp))
        proc.finished.connect(lambda *_: self.view.append_text("\n[console exited]\n", "info"))
        return True

    def restart(self) -> None:
        self.stop()
        self.view.clear()
        self.ensure_started()

    def on_output(self, text: str, kind: str) -> None:
        if kind == "err" and text.strip() in (">>>", "...", ">>> >>>"):
            kind = "info"
        self.view.append_text(text, kind)

    def run_code(self, code: str) -> None:
        if not code.strip() or not self.ensure_started():
            return
        import textwrap

        code = textwrap.dedent(code).strip("\n")
        self.view.append_text(code + "\n", "input")
        single_line = "\n" not in code
        if single_line:
            self.proc.write((code + "\n").encode("utf-8"))
            return
        try:
            ast.parse(code, mode="eval")
            self.proc.write((" ".join(code.split()) + "\n").encode("utf-8"))
        except SyntaxError:
            self.proc.write(f"exec(compile({code!r}, '<selection>', 'exec'))\n".encode("utf-8"))

    def _submit(self) -> None:
        text = self.input.text()
        self.input.clear()
        if text.strip():
            self.history.append(text)
        self._hist_pos = len(self.history)
        if not self.ensure_started():
            return
        self.view.append_text(text + "\n", "input")
        self.proc.write((text + "\n").encode("utf-8"))

    def eventFilter(self, obj, e):
        if obj is self.input and e.type() == e.Type.KeyPress and self.history:
            if e.key() == Qt.Key.Key_Up:
                self._hist_pos = max(0, self._hist_pos - 1)
                self.input.setText(self.history[self._hist_pos])
                return True
            if e.key() == Qt.Key.Key_Down:
                self._hist_pos = min(len(self.history), self._hist_pos + 1)
                self.input.setText(self.history[self._hist_pos] if self._hist_pos < len(self.history) else "")
                return True
        return False


class TerminalPanel(_ProcessPanel):
    """A line-based shell with the selected interpreter activated."""

    def __init__(self, theme, get_interpreter, get_cwd, parent=None):
        super().__init__(theme, parent)
        self.get_interpreter = get_interpreter
        self.get_cwd = get_cwd
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 6, 2)
        self.title = QLabel("Terminal")
        self.title.setObjectName("Dim")
        bar.addWidget(self.title, 1)
        ext = QToolButton(text="Open External Terminal")
        ext.clicked.connect(lambda: self.open_external(self.get_cwd()))
        bar.addWidget(ext)
        bar.addWidget(self._tool("restart", "Restart shell", lambda: self.start(self.get_cwd())))
        bar.addWidget(self._tool("clear", "Clear", self.view.clear))
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)
        self.input = QLineEdit(placeholderText="Command (e.g. pip list) and press Enter")
        self.input.setFont(mono_font("", 10))
        self.input.returnPressed.connect(self._submit)
        lay.addWidget(self.input)

    def _env(self) -> dict:
        interp = self.get_interpreter()
        return activated_env(interp) if interp else child_env()

    def start(self, cwd: str | None = None) -> None:
        self.stop()
        cwd = cwd or self.get_cwd() or os.path.expanduser("~")
        interp = self.get_interpreter()
        self.title.setText(f"Terminal - {cwd}" + (f"  ({interp.label()})" if interp else ""))
        if IS_WIN:
            self._spawn(os.environ.get("COMSPEC", "cmd.exe"), ["/Q", "/K", "chcp 65001>nul"], cwd, self._env())
        else:
            self._spawn(shutil.which("bash") or "/bin/sh", ["--norc", "-i"] if shutil.which("bash") else ["-i"],
                        cwd, self._env())

    def _submit(self) -> None:
        text = self.input.text()
        self.input.clear()
        if not self.running():
            self.start()
        self.view.append_text(text + "\n", "input")
        self.write_line(text)

    def open_external(self, cwd: str | None) -> None:
        cwd = cwd or os.path.expanduser("~")
        env = self._env()
        try:
            if IS_WIN:
                subprocess.Popen(["cmd.exe", "/K"], cwd=cwd, env=env,
                                 creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10))
                return
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"):
                if shutil.which(term):
                    subprocess.Popen([term], cwd=cwd, env=env)
                    return
            self.view.append_text("No terminal emulator found.\n", "err")
        except OSError as e:
            self.view.append_text(f"Could not open a terminal: {e}\n", "err")
