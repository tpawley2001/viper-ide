"""Viper IDE debugger backend. Runs inside the *target* interpreter (keep it Python 3.7+).

    python viper_dbg.py --port N [--no-jmc] [--stop-on-entry] -- script.py [args...]

Talks newline-delimited JSON over a localhost socket. The script's own stdout
and stderr stay on the process pipes, so the IDE shows program output as usual.
"""
import bdb
import builtins
import json
import os
import queue
import reprlib
import socket
import sys
import sysconfig
import threading
import traceback

_repr = reprlib.Repr()
_repr.maxstring = 240
_repr.maxother = 240
_repr.maxlist = _repr.maxtuple = _repr.maxset = _repr.maxdict = 30
_repr.maxlevel = 3

HERE = os.path.normcase(os.path.abspath(__file__))
BDB_FILE = os.path.normcase(os.path.abspath(bdb.__file__))
SIMPLE_KEYS = (str, int, float, bool, type(None), bytes)
HIDDEN = {"__builtins__", "__loader__", "__spec__", "__cached__", "__package__", "__annotations__"}


def _library_roots():
    roots = set()
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        if paths.get(key):
            roots.add(os.path.normcase(os.path.abspath(paths[key])))
    try:
        import site
        roots.add(os.path.normcase(os.path.abspath(site.getusersitepackages())))
    except Exception:
        pass
    return tuple(roots)


class Channel:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port))
        self.rfile = self.sock.makefile("r", encoding="utf-8")
        self.lock = threading.Lock()

    def send(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self.lock:
            try:
                self.sock.sendall(data)
            except OSError:
                pass


class Debugger(bdb.Bdb):
    def __init__(self, chan, main_path, just_my_code=True, stop_on_entry=False):
        bdb.Bdb.__init__(self)
        self.chan = chan
        self.main = self.canonic(main_path)
        self.jmc = just_my_code
        self.stop_on_entry = stop_on_entry
        self.lib_roots = _library_roots()
        self.commands = queue.Queue()
        self.started = False
        self.last_cmd = "continue"
        self.shown = []
        threading.Thread(target=self._reader, name="viper-dbg-reader", daemon=True).start()

    # ---------------------------------------------------------------- transport
    def _reader(self):
        try:
            for line in self.chan.rfile:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                cmd = msg.get("cmd")
                if cmd == "setBreakpoints":
                    self._set_breakpoints(msg)
                elif cmd == "pause":
                    self._pause()
                elif cmd == "terminate":
                    os._exit(1)
                else:
                    self.commands.put(msg)
        except OSError:
            pass
        os._exit(0)  # IDE went away: don't leave an orphaned, traced process behind

    def _set_breakpoints(self, msg):
        filename = self.canonic(msg["file"])
        self.clear_all_file_breaks(filename)
        verified = []
        for bp in msg.get("lines", []):
            err = self.set_break(filename, int(bp["line"]), cond=bp.get("condition") or None)
            verified.append({"line": bp["line"], "ok": not err})
        self.chan.send({"event": "breakpoints", "file": msg["file"], "items": verified})

    def _pause(self):
        self.last_cmd = "pause"
        self.set_step()
        main_ident = threading.main_thread().ident
        frame = sys._current_frames().get(main_ident)
        while frame is not None:
            frame.f_trace = self.trace_dispatch
            frame = frame.f_back

    # -------------------------------------------------------------- bdb hooks
    def set_continue(self):
        # Unlike bdb, keep the trace function installed even with no breakpoints,
        # so breakpoints added while running and "pause" still work.
        self._set_stopinfo(self.botframe, None, -1)

    def is_internal(self, frame):
        fn = frame.f_code.co_filename
        if fn.startswith("<"):
            return True
        canon = self.canonic(fn)
        return canon in (HERE, BDB_FILE)

    def is_user_frame(self, frame):
        if self.is_internal(frame):
            return False
        if not self.jmc:
            return True
        canon = self.canonic(frame.f_code.co_filename)
        if canon == self.main:
            return True
        if "site-packages" in canon or "dist-packages" in canon:
            return False
        return not any(canon.startswith(root) for root in self.lib_roots)

    def stop_here(self, frame):
        return bdb.Bdb.stop_here(self, frame) and self.is_user_frame(frame)

    def user_line(self, frame):
        if not self.started:
            if self.canonic(frame.f_code.co_filename) != self.main:
                return
            self.started = True
            if not self.stop_on_entry and not self._has_break(frame):
                self.set_continue()
                return
            self.interaction(frame, None, "entry" if self.stop_on_entry else "breakpoint")
            return
        if self.last_cmd in ("next", "stepIn", "stepOut"):
            reason = "step"
        elif self.last_cmd == "pause":
            reason = "pause"
        else:
            reason = "breakpoint"
        self.interaction(frame, None, reason)

    def _has_break(self, frame):
        return bool(self.get_breaks(self.canonic(frame.f_code.co_filename), frame.f_lineno))

    # ------------------------------------------------------------- inspection
    def interaction(self, frame, tb, reason, exception=None):
        stack, _ = self.get_stack(frame, tb)
        self.shown = [(f, lineno) for f, lineno in reversed(stack) if not self.is_internal(f)]
        if not self.shown:
            return
        frames = [{"id": i, "name": f.f_code.co_name, "file": os.path.abspath(f.f_code.co_filename),
                   "line": lineno} for i, (f, lineno) in enumerate(self.shown)]
        self.chan.send({"event": "stopped", "reason": reason, "frames": frames,
                        "exception": exception, "variables": self.variables(0, None)})
        top = self.shown[0][0]
        while True:
            msg = self.commands.get()
            cmd = msg.get("cmd")
            if cmd in ("continue", "next", "stepIn", "stepOut"):
                self.last_cmd = cmd
                if tb is not None:
                    break  # post-mortem: nothing left to run
                if cmd == "continue":
                    self.set_continue()
                elif cmd == "next":
                    self.set_next(top)
                elif cmd == "stepIn":
                    self.set_step()
                else:
                    self.set_return(top)
                break
            if cmd == "variables":
                self.chan.send({"event": "variables", "req": msg.get("req"), "frame": msg.get("frame", 0),
                                "expr": msg.get("expr"),
                                "items": self.variables(msg.get("frame", 0), msg.get("expr"))})
            elif cmd == "evaluate":
                self.chan.send(dict(self.evaluate(msg.get("frame", 0), msg.get("expr", "")),
                                    event="evaluated", req=msg.get("req"), expr=msg.get("expr", "")))
        self.chan.send({"event": "running"})

    def _frame(self, index):
        index = max(0, min(int(index or 0), len(self.shown) - 1))
        return self.shown[index][0]

    @staticmethod
    def _entry(name, value, expr):
        expandable = isinstance(value, (dict, list, tuple, set, frozenset)) and len(value) > 0
        if not expandable and not isinstance(value, (type, type(os), type(len))):
            try:
                expandable = bool(vars(value))
            except TypeError:
                expandable = False
        try:
            text = _repr.repr(value)
        except Exception as e:
            text = "<repr failed: %s>" % e
        return {"name": name, "type": type(value).__name__, "value": text, "expr": expr,
                "children": expandable}

    def variables(self, frame_index, expr):
        frame = self._frame(frame_index)
        items = []
        if expr is None:
            scope = frame.f_locals
            for name in sorted(scope, key=lambda n: (n.startswith("_"), n.lower())):
                if name in HIDDEN:
                    continue
                items.append(self._entry(name, scope[name], name))
            if frame.f_locals is not frame.f_globals:
                items.append({"name": "(globals)", "type": "dict", "value": "module globals",
                              "expr": "globals()", "children": True})
            return items
        try:
            value = eval(expr, frame.f_globals, frame.f_locals)
        except Exception as e:
            return [{"name": "error", "type": type(e).__name__, "value": str(e), "expr": None,
                     "children": False}]
        if isinstance(value, dict):
            for i, (k, v) in enumerate(value.items()):
                if i >= 500:
                    break
                if expr == "globals()" and k in HIDDEN:
                    continue
                child = "(%s)[%r]" % (expr, k) if isinstance(k, SIMPLE_KEYS) else None
                items.append(self._entry(_repr.repr(k) if expr != "globals()" else k, v, child))
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value[:500]):
                items.append(self._entry("[%d]" % i, v, "(%s)[%d]" % (expr, i)))
        elif isinstance(value, (set, frozenset)):
            for i, v in enumerate(list(value)[:500]):
                items.append(self._entry("{%d}" % i, v, "list(%s)[%d]" % (expr, i)))
        else:
            try:
                attrs = vars(value)
            except TypeError:
                attrs = {}
            for k in sorted(attrs):
                items.append(self._entry(k, attrs[k], "(%s).%s" % (expr, k)))
        return items

    def evaluate(self, frame_index, expr):
        frame = self._frame(frame_index)
        try:
            try:
                code = compile(expr, "<debug console>", "eval")
            except SyntaxError:
                exec(compile(expr, "<debug console>", "exec"), frame.f_globals, frame.f_locals)
                return {"ok": True, "value": ""}
            value = eval(code, frame.f_globals, frame.f_locals)
            return {"ok": True, "value": _repr.repr(value), "type": type(value).__name__}
        except Exception as e:
            return {"ok": False, "value": "".join(traceback.format_exception_only(type(e), e)).strip()}


def main():
    argv = sys.argv[1:]
    if "--" not in argv:
        sys.stderr.write("usage: viper_dbg.py --port N [--no-jmc] [--stop-on-entry] -- script [args]\n")
        return 2
    split = argv.index("--")
    opts, rest = argv[:split], argv[split + 1:]
    port = int(opts[opts.index("--port") + 1])
    main_path = os.path.abspath(rest[0])

    chan = Channel(port)
    dbg = Debugger(chan, main_path, just_my_code="--no-jmc" not in opts,
                   stop_on_entry="--stop-on-entry" in opts)
    chan.send({"event": "hello", "pid": os.getpid(), "python": sys.version.split()[0]})
    while dbg.commands.get().get("cmd") != "start":  # breakpoints arrive first
        pass

    sys.argv = [main_path] + rest[1:]
    sys.path[0] = os.path.dirname(main_path)
    import __main__
    __main__.__dict__.clear()
    __main__.__dict__.update({"__name__": "__main__", "__file__": main_path,
                              "__builtins__": builtins, "__package__": None})
    exit_code = 0
    try:
        with open(main_path, "rb") as f:
            code = compile(f.read(), main_path, "exec")
        dbg.run(code, __main__.__dict__)
    except SystemExit as e:
        if e.code is None:
            exit_code = 0
        elif isinstance(e.code, int):
            exit_code = e.code
        else:
            sys.stderr.write("%s\n" % e.code)
            exit_code = 1
    except BaseException as e:
        exit_code = 1
        user_tb = sys.exc_info()[2]
        while user_tb is not None and dbg.is_internal(user_tb.tb_frame):
            user_tb = user_tb.tb_next  # start the printed traceback at the user's code
        traceback.print_exception(type(e), e, user_tb)
        sys.stderr.flush()
        text = "".join(traceback.format_exception_only(type(e), e)).strip()
        dbg.reset()
        dbg.interaction(None, sys.exc_info()[2], "exception", exception=text)
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
    chan.send({"event": "exited", "code": exit_code})
    return exit_code


def entry():
    code = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(code)


if __name__ == "__main__":
    # Re-import under our module name so clearing __main__ for the script
    # doesn't wipe the debugger's own globals (the same trick pdb uses). Nothing
    # after this call may touch this file's __main__ globals: main() empties them.
    import viper_dbg

    viper_dbg.entry()
