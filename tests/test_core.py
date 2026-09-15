"""Core (non-GUI) behaviour: import analysis, interpreter probing, lint, debugger protocol."""
import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from viper_ide import imports, interpreters, intel  # noqa: E402
from viper_ide.paths import helper  # noqa: E402


def test_extract_imports_marks_guarded_and_skips_relative():
    src = textwrap.dedent("""
        import os, numpy as np
        from cv2 import imread
        from . import sibling
        from .pkg import thing
        from google.cloud import storage
        from google.protobuf.json_format import MessageToDict
        try:
            import ujson
        except ImportError:
            ujson = None
        if TYPE_CHECKING:
            import pandas
        def f():
            import requests
    """)
    refs = {r.module: r for r in imports.extract_imports(src)}
    assert {"os", "numpy", "cv2", "google.cloud.storage", "google.protobuf.json_format", "ujson",
            "pandas", "requests"} == set(refs)
    assert refs["ujson"].optional and refs["pandas"].optional
    assert not refs["numpy"].optional and not refs["requests"].optional


def test_extract_imports_survives_syntax_errors():
    refs = imports.extract_imports("import yaml\nfrom bs4 import X\ndef broken(:\n")
    assert {r.module for r in refs} == {"yaml", "bs4"}


@pytest.mark.parametrize("module,dist", [
    ("cv2", "opencv-python"), ("PIL", "Pillow"), ("sklearn", "scikit-learn"),
    ("google.cloud.storage", "google-cloud-storage"), ("google.protobuf.json_format", "protobuf"),
    ("requests", "requests"), ("win32api", "pywin32"), ("mysql.connector", "mysql-connector-python"),
])
def test_dist_mapping(module, dist):
    assert imports.dist_for_module(module) == dist


def test_requirements_and_pep723():
    req = "requests>=2\n# comment\n-r other.txt\nnumpy[extra]==1.0 ; python_version>'3'\ngit+https://x\nfoo @ https://y\n"
    assert imports.parse_requirements(req) == ["requests", "numpy", "foo"]
    script = "# /// script\n# dependencies = [\n#   'rich>=13',\n#   \"httpx\",\n# ]\n# ///\nprint(1)\n"
    assert imports.inline_script_dependencies(script) == ["rich", "httpx"]


def test_missing_module_from_output():
    out = 'Traceback...\nModuleNotFoundError: No module named \'google.cloud.vision.v1\'\n'
    assert imports.missing_module_from_output(out) == "google.cloud.vision"


def test_probe_and_find_missing(tmp_path):
    interp = interpreters.probe(sys.executable)
    assert interp and interp.version == sys.version.split()[0]
    (tmp_path / "localmod.py").write_text("X = 1\n")
    res = interpreters.find_missing(sys.executable, ["os", "json", "localmod", "definitely_not_a_pkg_xyz",
                                                     "google.cloud.nothing_here"],
                                    ["pyflakes", "no-such-dist-qq"], [str(tmp_path)])
    assert res["missing"] == ["definitely_not_a_pkg_xyz", "google.cloud.nothing_here"]
    assert res["dists_missing"] == ["no-such-dist-qq"]


def test_probe_flags_os_managed_python():
    assert not interpreters.probe(sys.executable).externally_managed  # the test venv
    managed = next((i for i in map(interpreters.probe, interpreters.candidate_paths()) if i and i.externally_managed),
                   None)
    if managed is None:
        pytest.skip("no PEP 668 (externally managed) Python on this machine")
    assert not managed.is_venv


def test_lint_and_outline():
    code = "import os\n\ndef f(x):\n    return y\n\nclass A:\n    def m(self): pass\nCONST = 3\n"
    items = intel.lint(code, "t.py")
    msgs = {(i["line"], i["severity"]) for i in items}
    assert (1, "warning") in msgs and (4, "error") in msgs
    tree = intel.outline(code)
    assert [(k, n) for k, n, *_ in tree] == [("function", "f"), ("class", "A"), ("variable", "CONST")]
    assert tree[1][3][0][:2] == ("method", "m")


def test_jedi_completion_against_external_interpreter(tmp_path):
    import jedi
    env = jedi.create_environment(sys.executable, safe=False)
    script = jedi.Script("import json\njson.du", path=str(tmp_path / "x.py"), environment=env)
    assert "dumps" in [c.name for c in script.complete(2, 7)]


def _debug_session(tmp_path, script_src, breakpoints, commands):
    """Drive viper_dbg.py; `commands` maps the Nth 'stopped' event to the reply cmd."""
    script = tmp_path / "prog.py"
    script.write_text(script_src)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-u", str(helper("viper_dbg.py")), "--port", str(port), "--",
                             str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    srv.settimeout(20)
    conn, _ = srv.accept()
    conn.settimeout(20)
    rfile = conn.makefile("r", encoding="utf-8")

    def send(obj):
        conn.sendall((json.dumps(obj) + "\n").encode())

    events = []
    stops = 0
    for line in rfile:
        ev = json.loads(line)
        events.append(ev)
        if ev["event"] == "hello":
            send({"cmd": "setBreakpoints", "file": str(script), "lines": [{"line": n} for n in breakpoints]})
            send({"cmd": "start"})
        elif ev["event"] == "stopped":
            for extra in commands.get(("extra", stops), []):
                send(extra)
            send({"cmd": commands.get(stops, "continue")})
            stops += 1
        elif ev["event"] == "exited":
            break
    out, err = proc.communicate(timeout=20)
    srv.close()
    expected = next(e["code"] for e in reversed(events) if e["event"] == "exited")
    assert proc.returncode == expected, (proc.returncode, err)
    assert "viper_dbg.py" not in err, err  # the debugger must never leak its own tracebacks
    return events, out, err


def test_debugger_breakpoint_variables_step(tmp_path):
    src = textwrap.dedent("""\
        def add(a, b):
            total = a + b
            return total

        data = {"k": [1, 2, 3]}
        result = add(2, 3)
        print("result", result)
    """)
    events, out, _ = _debug_session(
        tmp_path, src, [2],
        {0: "stepOut", ("extra", 0): [{"cmd": "evaluate", "expr": "a * 10", "req": 7},
                                      {"cmd": "variables", "expr": "globals()", "req": 8}],
         1: "continue"})
    stopped = [e for e in events if e["event"] == "stopped"]
    assert stopped[0]["reason"] == "breakpoint" and stopped[0]["frames"][0]["line"] == 2
    assert stopped[0]["frames"][0]["name"] == "add"
    names = {v["name"]: v["value"] for v in stopped[0]["variables"]}
    assert names["a"] == "2" and names["b"] == "3"
    evaluated = next(e for e in events if e["event"] == "evaluated")
    assert evaluated["ok"] and evaluated["value"] == "20"
    glob = next(e for e in events if e["event"] == "variables")
    assert any(v["name"] == "data" and v["children"] for v in glob["items"])
    # step out of add() lands back in the module frame
    assert stopped[1]["reason"] == "step" and stopped[1]["frames"][0]["name"] == "<module>"
    assert "result 5" in out
    assert events[-1] == {"event": "exited", "code": 0}


def test_debugger_post_mortem_on_uncaught_exception(tmp_path):
    events, _, err = _debug_session(tmp_path, "x = 1\nraise ValueError('boom')\n", [], {})
    stopped = [e for e in events if e["event"] == "stopped"]
    assert stopped and stopped[0]["reason"] == "exception"
    assert "ValueError: boom" in stopped[0]["exception"]
    assert stopped[0]["frames"][0]["line"] == 2
    assert "ValueError: boom" in err
    assert events[-1]["code"] == 1


def test_debugger_step_over_then_continue_exits_cleanly(tmp_path):
    src = "def add(a, b):\n    total = a + b\n    return total\n\nprint('total', add(4, 5))\n"
    events, out, _ = _debug_session(tmp_path, src, [2], {0: "next", 1: "continue"})
    stopped = [e for e in events if e["event"] == "stopped"]
    assert [s["frames"][0]["line"] for s in stopped] == [2, 3]
    assert "total 9" in out and events[-1] == {"event": "exited", "code": 0}


def test_debugger_runs_to_end_without_breakpoints(tmp_path):
    events, out, _ = _debug_session(tmp_path, "import json\nprint(json.dumps([1]))\n", [], {})
    assert not [e for e in events if e["event"] == "stopped"]
    assert out.strip() == "[1]"
