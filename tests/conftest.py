"""One QApplication for the whole session.

A QApplication made inside a test dies with that test's locals and takes every live
widget, timer and pending cross-thread signal with it, which crashed later tests
intermittently (a core dump in a Qt timer callback, "wrapped C/C++ object ... has been deleted").
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def app():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


_windows = []


@pytest.fixture(autouse=True)
def _keep_windows_alive(monkeypatch):
    """Keep every MainWindow alive until the session ends.

    Tests close their windows but drop the last reference, so Python's GC freed them at
    arbitrary moments while Qt still had timers and queued callbacks aimed at them: a
    segfault inside a QTimer timeout slot a test or two later. The real app never frees
    its window, so tests shouldn't either.
    """
    from viper_ide import app as app_module

    original = app_module.MainWindow.__init__

    def init(self, *a, **kw):
        _windows.append(self)
        original(self, *a, **kw)

    monkeypatch.setattr(app_module.MainWindow, "__init__", init)
    yield
