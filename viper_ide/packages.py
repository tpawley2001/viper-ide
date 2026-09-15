"""pip operations (queued QProcess jobs) and on-demand developer tools."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

from .paths import child_env, subprocess_flags, tools_dir


def qt_env(values: dict) -> QProcessEnvironment:
    env = QProcessEnvironment()
    for k, v in values.items():
        env.insert(k, v)
    return env


def pip_env(extra: dict | None = None) -> QProcessEnvironment:
    return qt_env(child_env({"PYTHONIOENCODING": "utf-8", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                             "PIP_NO_INPUT": "1", "PYTHONUNBUFFERED": "1", **(extra or {})}))


@dataclass
class PipJob:
    interpreter: str
    args: list[str]
    title: str
    quiet: bool = False
    callback: Callable[[bool, str], None] | None = None
    retried: bool = False
    output: list[str] = field(default_factory=list)


class PipRunner(QObject):
    """Serialises pip invocations; installing twice at once corrupts site-packages."""

    output = pyqtSignal(str)
    job_started = pyqtSignal(str)
    job_finished = pyqtSignal(str, bool)
    busy_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[PipJob] = []
        self._current: PipJob | None = None
        self._proc: QProcess | None = None

    @property
    def busy(self) -> bool:
        return self._current is not None

    def run(self, interpreter: str, args: list[str], title: str, callback=None, quiet=False) -> None:
        self._queue.append(PipJob(interpreter, list(args), title, quiet, callback))
        if not self._current:
            self._next()

    def install(self, interpreter: str, packages: list[str], callback=None, upgrade=False) -> None:
        args = ["install"] + (["--upgrade"] if upgrade else []) + packages
        self.run(interpreter, ["-m", "pip"] + args, f"Installing {', '.join(packages)}", callback)

    def uninstall(self, interpreter: str, packages: list[str], callback=None) -> None:
        self.run(interpreter, ["-m", "pip", "uninstall", "-y"] + packages,
                 f"Uninstalling {', '.join(packages)}", callback)

    def install_requirements(self, interpreter: str, path: str, callback=None) -> None:
        self.run(interpreter, ["-m", "pip", "install", "-r", path],
                 f"Installing {os.path.basename(path)}", callback)

    def list_installed(self, interpreter: str, callback: Callable[[list[dict]], None], outdated=False) -> None:
        args = ["-m", "pip", "list", "--format=json"] + (["--outdated"] if outdated else [])

        def done(ok: bool, text: str):
            rows = []
            if ok:
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith("["):
                        try:
                            rows = json.loads(line)
                        except ValueError:
                            pass
            callback(rows)

        self.run(interpreter, args, "Checking for updates" if outdated else "Listing packages", done,
                 quiet=True)

    def cancel(self) -> None:
        self._queue.clear()
        if self._proc and self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.kill()
            self._proc.waitForFinished(3000)

    def _next(self) -> None:
        if not self._queue:
            self._current = None
            self.busy_changed.emit(False)
            return
        job = self._current = self._queue.pop(0)
        self.busy_changed.emit(True)
        self.job_started.emit(job.title)
        if not job.quiet:
            self.output.emit(f"> {os.path.basename(job.interpreter)} {' '.join(job.args)}\n")
        proc = self._proc = QProcess(self)
        proc.setProcessEnvironment(pip_env())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._error)
        proc.start(job.interpreter, job.args)

    def _read(self) -> None:
        if not self._proc or not self._current:
            return
        text = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._current.output.append(text)
        if not self._current.quiet:
            self.output.emit(text)

    def _error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart and self._current:
            self._current.output.append(f"Could not start {self._current.interpreter}\n")
            self._finished(-1, None)

    def _finished(self, code, _status) -> None:
        job = self._current
        if job is None:
            return
        self._current = None
        if self._proc:
            self._proc.deleteLater()
            self._proc = None
        text = "".join(job.output)
        ok = code == 0
        if not ok and "No module named pip" in text and not job.retried:
            self.output.emit("pip is missing from this interpreter - bootstrapping it with ensurepip...\n")
            job.retried, job.output = True, []
            self._queue[:0] = [PipJob(job.interpreter, ["-m", "ensurepip", "--upgrade"], "Installing pip"), job]
        else:
            if not ok and "externally-managed-environment" in text:
                self.output.emit("\nThis Python is managed by the operating system, so pip won't install into "
                                 "it. Viper will offer a virtual environment instead.\n")
            if not job.quiet:
                self.output.emit(f"{'Done' if ok else 'Failed'}: {job.title}\n\n")
            self.job_finished.emit(job.title, ok)
            if job.callback:
                job.callback(ok, text)
        self._next()


# ------------------------------------------------------------------ dev tools
TOOLS = {"black": "black", "ruff": "ruff"}


def tool_dir(version: str) -> Path:
    major_minor = "".join(version.split(".")[:2])
    return tools_dir() / f"py{major_minor}"


def tool_installed(tool: str, version: str) -> bool:
    return (tool_dir(version) / TOOLS[tool]).is_dir()


def install_tool_args(tool: str, version: str) -> list[str]:
    return ["-m", "pip", "install", "--upgrade", "--target", str(tool_dir(version)), TOOLS[tool]]


def run_formatter(tool: str, interpreter: str, version: str, source: str, filename: str) -> str:
    """Format ``source`` with black/ruff living in Viper's tools folder, not the user's env."""
    env = child_env({"PYTHONPATH": str(tool_dir(version)), "PYTHONIOENCODING": "utf-8"})
    if tool == "ruff":
        cmd = [interpreter, "-m", "ruff", "format", "--stdin-filename", filename, "-"]
    else:
        cmd = [interpreter, "-m", "black", "-q", "--stdin-filename", filename, "-"]
    proc = subprocess.run(cmd, input=source.encode("utf-8"), capture_output=True, env=env, timeout=60,
                          cwd=os.path.dirname(filename) or None, **subprocess_flags())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or f"{tool} failed")
    return proc.stdout.decode("utf-8")
