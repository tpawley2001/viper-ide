"""Viper IDE helper: runs inside the *target* interpreter (keep it Python 3.6+).

stdin:  {"modules": [...], "dists": [...], "paths": [...]}
stdout: {"missing": [...], "stdlib": [...], "dists_missing": [...], "version": "3.x.y"}
"""
import importlib.util
import io
import json
import sys


def _dist_installed(metadata, name):
    for candidate in (name, name.replace("-", "_"), name.replace("_", "-"), name.lower()):
        try:
            metadata.distribution(candidate)
            return True
        except metadata.PackageNotFoundError:
            continue
        except Exception:
            return True
    return False


def main():
    request = json.loads(sys.stdin.read() or "{}")
    for p in reversed(request.get("paths", [])):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    real_stdout = sys.stdout
    sys.stdout = io.StringIO()  # probing a dotted name imports its parent, which may print
    result = {"missing": [], "stdlib": [], "dists_missing": [], "version": sys.version.split()[0]}
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    try:
        for mod in request.get("modules", []):
            try:
                found = importlib.util.find_spec(mod) is not None
            except ModuleNotFoundError:
                found = False
            except Exception:
                found = True  # importable parent that is broken: not a missing-package problem
            if not found:
                bucket = "stdlib" if mod.split(".")[0] in stdlib else "missing"
                result[bucket].append(mod)

        dists = request.get("dists", [])
        if dists:
            try:
                from importlib import metadata
            except ImportError:
                metadata = None
            if metadata is not None:
                result["dists_missing"] = [d for d in dists if not _dist_installed(metadata, d)]
    finally:
        sys.stdout = real_stdout
    print(json.dumps(result))


if __name__ == "__main__":
    main()
