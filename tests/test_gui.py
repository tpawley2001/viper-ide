"""GUI behaviour driven headlessly (QT_QPA_PLATFORM=offscreen)."""
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QEventLoop, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, pred, secs=20):
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("VIPER_IDE_HOME", str(tmp_path / "home"))
    from viper_ide.app import MainWindow
    from viper_ide.settings import Settings

    settings = Settings(tmp_path / "settings.json")
    settings._data["extra_interpreters"] = [sys.executable]
    settings._data["interpreter"] = sys.executable
    win = MainWindow(settings, [str(tmp_path)])
    win.show()
    assert wait(app, lambda: win.interp is not None, 30)
    yield win
    for p in win.pages():
        p.editor.setModified(False)
    win.close()


def type_text(app, editor, text):
    for ch in text:
        key = Qt.Key.Key_Return if ch == "\n" else 0
        editor.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier,
                                       "\r" if ch == "\n" else ch))
    app.processEvents()


def test_indent_pairs_and_comment(window, app):
    e = window.new_file().editor
    type_text(app, e, "def f(x):\nreturn [x")
    assert e.text().replace("\r\n", "\n") == "def f(x):\n    return [x]"
    type_text(app, e, "]")
    assert e.text().endswith("[x]") and e.getCursorPosition() == (1, len("    return [x]"))
    e.setCursorPosition(1, 0)
    e.toggle_comment()
    assert e.text(1).startswith("    # return")
    e.toggle_comment()
    assert e.text(1).startswith("    return")


def test_completion_list_appears(window, app):
    e = window.new_file().editor
    e.setText("import json\njson.du")
    e.setCursorPosition(1, 7)
    e.request_completion(True)
    assert wait(app, e.isListActive, 20)


def test_lint_markers_and_problems(window, app, tmp_path):
    path = tmp_path / "bad.py"
    path.write_text("import os\nprint(nope)\n")
    page = window.open_file(str(path))
    assert wait(app, lambda: len(page.editor.lint_items) == 2, 20)
    assert window.problems.topLevelItemCount() == 1


def test_run_file_shows_output(window, app, tmp_path):
    path = tmp_path / "hello.py"
    path.write_text("print('hello from viper')\n")
    window.open_file(str(path))
    window.run_file(False)
    assert wait(app, lambda: "exited with code 0" in window.run_panel.view.toPlainText(), 30)
    assert "hello from viper" in window.run_panel.view.toPlainText()


def test_debug_session_through_gui(window, app, tmp_path):
    path = tmp_path / "dbg.py"
    path.write_text(textwrap.dedent("""\
        def add(a, b):
            total = a + b
            return total

        print("total", add(4, 5))
    """))
    page = window.open_file(str(path))
    page.editor.toggle_breakpoint(1)
    window.run_file(True)
    assert wait(app, lambda: window.debug.paused, 30)
    items = {window.debug_panel.vars.topLevelItem(i).text(0): window.debug_panel.vars.topLevelItem(i).text(1)
             for i in range(window.debug_panel.vars.topLevelItemCount())}
    assert items.get("a") == "4" and items.get("b") == "5"
    assert page.editor.markersAtLine(1) & (1 << 3)  # debug arrow on the breakpoint line
    window.debug.command("next")
    assert wait(app, lambda: window.debug.paused and page.editor.markersAtLine(2) & (1 << 3), 20)
    window.debug.command("continue")
    assert wait(app, lambda: "exited with code 0" in window.run_panel.view.toPlainText(), 30)
    assert "total 9" in window.run_panel.view.toPlainText()
    assert not window.debug.active


def test_missing_import_bar_offers_pypi_package(window, app, tmp_path):
    path = tmp_path / "needs.py"
    path.write_text("import cv2\nimport yaml_surely_missing_viper_xyz\n")
    page = window.open_file(str(path))
    assert wait(app, lambda: page.info.isVisible() and "opencv-python" in page.info.label.text(), 60)
    assert "Not found on PyPI" in page.info.label.text()


def test_runtime_module_not_found_offers_install(window, app, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    window.settings._data["check_imports"] = False
    path = tmp_path / "dyn.py"
    path.write_text("import importlib\nimportlib.import_module('yaml' + '')\n")
    window.open_file(str(path))
    try:
        import yaml  # noqa: F401
        pytest.skip("PyYAML is installed in the test interpreter")
    except ImportError:
        pass
    window.run_file(False)
    assert wait(app, lambda: asked, 60)
    assert "PyYAML" in asked[0]
