"""End-to-end self test, runnable from the frozen build:  ViperIDE.exe --selftest result.json

Exercises the parts that break only when bundled: helper scripts on disk, Jedi's
subprocess against a real interpreter, the debugger socket, and the GUI.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
from pathlib import Path


def _drive_debugger(interp: str, workdir: Path) -> dict:
    from .paths import child_env, helper, subprocess_flags

    script = workdir / "dbg_target.py"
    script.write_text("def add(a, b):\n    total = a + b\n    return total\n\nprint('sum', add(2, 3))\n")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(30)
    proc = subprocess.Popen([interp, "-u", str(helper("viper_dbg.py")), "--port", str(srv.getsockname()[1]), "--",
                             str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=child_env(), **subprocess_flags())
    conn, _ = srv.accept()
    conn.settimeout(30)
    events = []
    for line in conn.makefile("r", encoding="utf-8"):
        ev = json.loads(line)
        events.append(ev["event"])
        if ev["event"] == "hello":
            for cmd in ({"cmd": "setBreakpoints", "file": str(script), "lines": [{"line": 2}]}, {"cmd": "start"}):
                conn.sendall((json.dumps(cmd) + "\n").encode())
        elif ev["event"] == "stopped":
            values = {v["name"]: v["value"] for v in ev["variables"]}
            assert ev["frames"][0]["line"] == 2 and values.get("a") == "2", ev
            conn.sendall(b'{"cmd": "continue"}\n')
        elif ev["event"] == "exited":
            break
    out, err = proc.communicate(timeout=30)
    srv.close()
    assert "sum 5" in out, (out, err)
    return {"events": events}


def main(argv: list[str]) -> int:
    out_path = Path(argv[0]) if argv else Path(tempfile.gettempdir()) / "viper_selftest.json"
    work = Path(tempfile.mkdtemp(prefix="viper_selftest_"))
    os.environ["VIPER_IDE_HOME"] = str(work / "home")
    results: dict = {"frozen": bool(getattr(sys, "frozen", False)), "checks": {}}
    state: dict = {}

    def check(name, fn):
        t0 = time.monotonic()
        try:
            detail = fn()
            results["checks"][name] = {"ok": True, "detail": detail, "secs": round(time.monotonic() - t0, 2)}
        except Exception:  # noqa: BLE001 - every failure is recorded
            results["checks"][name] = {"ok": False, "detail": traceback.format_exc(),
                                       "secs": round(time.monotonic() - t0, 2)}

    from . import imports, interpreters, intel
    from .paths import helper

    def find_interp():
        found = interpreters.discover()
        assert found, "no interpreters discovered"
        state["interp"] = next((i for i in found if not i.is_venv), found[0])
        return [i.label() + " " + i.path for i in found]

    check("helpers_on_disk", lambda: [str(helper(n)) for n in ("find_missing.py", "viper_dbg.py")
                                      if helper(n).is_file() or (_ for _ in ()).throw(FileNotFoundError(n))])
    check("discover_interpreters", find_interp)

    def missing():
        res = interpreters.find_missing(state["interp"].path, ["json", "viper_surely_missing_pkg"], [], [str(work)])
        assert res["missing"] == ["viper_surely_missing_pkg"], res
        return res

    def jedi_complete():
        import jedi

        env = jedi.create_environment(state["interp"].path, safe=False)
        names = [c.name for c in jedi.Script("import json\njson.lo", path=str(work / "x.py"),
                                             environment=env).complete(2, 7)]
        assert "loads" in names, names
        return names[:5]

    check("find_missing", missing)
    check("jedi_external_env", jedi_complete)
    check("pyflakes", lambda: intel.lint("import os\nprint(undefined_name)\n", "t.py"))
    check("import_mapping", lambda: imports.dist_for_module("cv2"))
    check("debugger", lambda: _drive_debugger(state["interp"].path, work))

    def gui():
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6.QtWidgets import QApplication

        from .app import MainWindow
        from .settings import Settings
        from .theme import apply_app_theme, theme

        app = QApplication.instance() or QApplication([sys.argv[0]])
        apply_app_theme(app, theme("dark"))
        demo = work / "demo.py"
        demo.write_text(textwrap.dedent("""\
            import json
            import requests_surely_not_real_viper
            from PIL import Image


            class Greeter:
                def greet(self, name):
                    return f"Hello, {name}!"


            def main():
                data = json.dumps({"a": 1})
                print(Greeter().greet("world"), data, undefined_thing)


            if __name__ == "__main__":
                main()
            """))
        win = MainWindow(Settings(work / "settings.json"), [str(work), str(demo)])
        win.resize(1400, 860)
        win.show()

        def wait(pred, secs):
            deadline = time.monotonic() + secs
            while time.monotonic() < deadline:
                app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
                if pred():
                    return True
                time.sleep(0.02)
            return pred()

        assert wait(lambda: win.interp is not None, 60), "interpreter never selected"
        page = win.page()
        assert page and page.editor.path == str(demo)
        assert wait(lambda: page.editor.lint_items, 30), "no lint results"
        assert wait(lambda: page.info.isVisible() and "Not" in page.info.label.text(), 90), "missing-import bar never shown"
        bar_text = page.info.label.text()
        shot = out_path.with_suffix(".png")
        QTimer.singleShot(0, lambda: None)
        wait(lambda: False, 1.5)
        win.grab().save(str(shot))
        errors = [i["message"] for i in page.editor.lint_items]
        win.close()
        return {"interpreter": win.interp.label(), "infobar": bar_text, "lint": errors, "screenshot": str(shot)}

    check("gui", gui)
    results["ok"] = all(c["ok"] for c in results["checks"].values())
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return 0 if results["ok"] else 1
