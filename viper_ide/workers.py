"""Run blocking work off the GUI thread and deliver the result back on it."""
from __future__ import annotations

import threading
import traceback

from PyQt6.QtCore import QObject, pyqtSignal

_live: set = set()


class Call(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(object)


def run_async(fn, *args, on_done=None, on_error=None, on_progress=None, **kwargs) -> Call:
    """Call ``fn(*args, **kwargs)`` in a thread.

    Signals are emitted from the worker thread but ``Call`` lives on the GUI
    thread, so Qt queues the slots onto the event loop. When ``on_progress`` is
    given, ``fn`` receives a ``progress`` callable keyword.
    """
    call = Call()
    _live.add(call)
    if on_done:
        call.done.connect(on_done)
    if on_error:
        call.failed.connect(on_error)
    if on_progress:
        call.progress.connect(on_progress)
        kwargs["progress"] = call.progress.emit

    def release(*_):
        _live.discard(call)

    call.done.connect(release)
    call.failed.connect(release)

    def body():
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            signal, value = call.failed, detail
        else:
            signal, value = call.done, result
        try:
            signal.emit(value)
        except RuntimeError:  # Qt already tore the object down (the app is quitting); nobody is listening
            _live.discard(call)

    threading.Thread(target=body, daemon=True).start()
    return call
