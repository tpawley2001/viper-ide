"""Code intelligence: Jedi (completion, hover, goto, rename) and pyflakes lint.

One worker thread; a newer request of the same kind replaces a queued older one,
so fast typing never builds a backlog.
"""
from __future__ import annotations

import ast
import os
import threading

from PyQt6.QtCore import QObject, pyqtSignal

PRIORITY = ("rename", "references", "goto", "complete", "signature", "hover", "lint")
ERROR_FLAKES = {"UndefinedName", "UndefinedLocal", "UndefinedExport", "ReturnOutsideFunction",
                "YieldOutsideFunction", "ContinueOutsideLoop", "BreakOutsideLoop",
                "DuplicateArgument", "TooManyExpressionsInStarredAssignment"}


class IntelEngine(QObject):
    result = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: dict[str, dict] = {}
        self._cv = threading.Condition()
        self._envs: dict[tuple, tuple] = {}
        self._next_id = 0
        self._stop = False
        threading.Thread(target=self._loop, name="viper-intel", daemon=True).start()

    def request(self, kind: str, **kw) -> int:
        with self._cv:
            self._next_id += 1
            kw.update(kind=kind, id=self._next_id)
            key = f"lint:{kw.get('path')}" if kind == "lint" else kind
            self._pending[key] = kw
            self._cv.notify()
            return self._next_id

    def invalidate(self) -> None:
        """Interpreter changed or packages installed: rebuild Jedi environments."""
        with self._cv:
            self._envs.clear()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()

    def _take(self) -> dict | None:
        with self._cv:
            while not self._pending and not self._stop:
                self._cv.wait()
            if self._stop:
                return None
            for kind in PRIORITY:
                for key in list(self._pending):
                    if key.split(":", 1)[0] == kind:
                        return self._pending.pop(key)
            return self._pending.pop(next(iter(self._pending)))

    def _loop(self) -> None:
        while (req := self._take()) is not None:
            out = {"kind": req["kind"], "id": req["id"], "path": req.get("path"), "req": req}
            try:
                out["payload"] = getattr(self, "_do_" + req["kind"])(req)
            except Exception as e:  # noqa: BLE001 - intel failures must never kill the worker
                out["error"] = f"{type(e).__name__}: {e}"
            self.result.emit(out)

    # ----------------------------------------------------------------- jedi
    def _script(self, req: dict):
        import jedi

        path = req.get("path") or None
        root = req.get("project") or (os.path.dirname(path) if path else os.getcwd())
        interp = req.get("interpreter") or ""
        key = (root, interp)
        with self._cv:
            cached = self._envs.get(key)
        if cached is None:
            env = None
            if interp:
                try:
                    env = jedi.create_environment(interp, safe=False)
                except Exception:  # noqa: BLE001
                    env = None
            if env is None:
                env = jedi.InterpreterEnvironment()
            cached = (jedi.Project(root), env)
            with self._cv:
                self._envs[key] = cached
        project, env = cached
        return jedi.Script(req["code"], path=path, project=project, environment=env)

    def _do_complete(self, req):
        script = self._script(req)
        comps = script.complete(req["line"], req["col"])
        return [(c.name, c.type) for c in comps[:400]]

    def _do_signature(self, req):
        sigs = self._script(req).get_signatures(req["line"], req["col"])
        if not sigs:
            return None
        sig = sigs[0]
        text = sig.to_string()
        span = None
        if sig.index is not None and 0 <= sig.index < len(sig.params):
            ptxt = sig.params[sig.index].to_string()
            start = text.find(ptxt, text.find("("))
            if start >= 0:
                span = (start, start + len(ptxt))
        return {"text": text, "span": span}

    def _do_hover(self, req):
        script = self._script(req)
        names = script.help(req["line"], req["col"]) or script.infer(req["line"], req["col"])
        for n in names[:1]:
            doc = n.docstring() or ""
            head = n.full_name or n.name
            if not doc.strip():
                doc = n.description
            return {"title": head, "type": n.type, "text": doc[:4000]}
        return None

    def _do_goto(self, req):
        script = self._script(req)
        names = script.goto(req["line"], req["col"], follow_imports=True)
        if not any(n.module_path for n in names):
            names = script.infer(req["line"], req["col"]) or names
        return [{"path": str(n.module_path) if n.module_path else None, "line": n.line, "column": n.column,
                 "name": n.name, "description": n.description} for n in names]

    def _do_references(self, req):
        refs = self._script(req).get_references(req["line"], req["col"], include_builtins=False)
        return [{"path": str(r.module_path) if r.module_path else req.get("path"), "line": r.line,
                 "column": r.column, "code": (r.get_line_code() or "").strip()} for r in refs]

    def _do_rename(self, req):
        refactoring = self._script(req).rename(req["line"], req["col"], new_name=req["new_name"])
        changed = {str(p): cf.get_new_code() for p, cf in refactoring.get_changed_files().items()}
        return {"changed": changed, "diff": refactoring.get_diff()}

    # ----------------------------------------------------------------- lint
    def _do_lint(self, req):
        return lint(req["code"], req.get("path") or "<untitled>")


def lint(code: str, filename: str) -> list[dict]:
    from pyflakes import api

    items: list[dict] = []

    class Reporter:
        def unexpectedError(self, _filename, msg):
            items.append({"line": 1, "col": 0, "message": str(msg), "severity": "error"})

        def syntaxError(self, _filename, msg, lineno, offset, _text):
            items.append({"line": lineno or 1, "col": max((offset or 1) - 1, 0), "message": msg,
                          "severity": "error"})

        def flake(self, message):
            sev = "error" if type(message).__name__ in ERROR_FLAKES else "warning"
            items.append({"line": message.lineno, "col": getattr(message, "col", 0) or 0,
                          "message": message.message % message.message_args, "severity": sev})

    api.check(code, filename, Reporter())
    lines = code.splitlines()
    kept = []
    for it in items:
        src = lines[it["line"] - 1] if 0 < it["line"] <= len(lines) else ""
        if "# noqa" not in src:
            kept.append(it)
    return kept


def outline(code: str) -> list[tuple] | None:
    """[(kind, name, line, children)] for classes, functions and module-level names."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None

    def walk(body, in_class=False, top=False):
        out = []
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(("method" if in_class else "function", node.name, node.lineno, walk(node.body)))
            elif isinstance(node, ast.ClassDef):
                out.append(("class", node.name, node.lineno, walk(node.body, in_class=True)))
            elif top and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        out.append(("variable", t.id, node.lineno, []))
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for attr in ("body", "orelse", "finalbody"):
                    out += walk(getattr(node, attr, []) or [], in_class)
                for h in getattr(node, "handlers", []) or []:
                    out += walk(h.body, in_class)
        return out

    return walk(tree.body, top=True)
