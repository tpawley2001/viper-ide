"""JSON-backed settings (a plain file is easier to inspect than the registry)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .paths import config_dir

DEFAULTS: dict = {
    "theme": "dark",
    "font_family": "",
    "font_size": 11,
    "tab_width": 4,
    "use_tabs": False,
    "edge_column": 88,
    "word_wrap": False,
    "show_whitespace": False,
    "auto_close_brackets": True,
    "formatter": "black",          # black | ruff
    "format_on_save": False,
    "lint": True,
    "check_imports": True,
    "auto_install": "ask",         # ask | always | never
    "just_my_code": True,
    "run_cwd": "file",             # file | project
    "clear_output_on_run": True,
    "save_before_run": True,
    "interpreter": "",
    "project_interpreters": {},
    "extra_interpreters": [],
    "recent_files": [],
    "recent_folders": [],
    "session": {},
    "breakpoints": {},
    "window_geometry": "",
    "window_state": "",
    "ignored_missing": [],
}


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_dir() / "settings.json"
        self._data = copy.deepcopy(DEFAULTS)
        try:
            loaded = json.loads(self.path.read_text("utf-8"))
            if isinstance(loaded, dict):
                self._data.update(loaded)
        except (OSError, ValueError):
            pass

    def get(self, key: str, default=None):
        if key in self._data:
            return self._data[key]
        return copy.deepcopy(DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._data, indent=2), "utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def add_recent(self, key: str, value: str, limit: int = 15) -> None:
        items = [v for v in self.get(key) if os.path.normcase(v) != os.path.normcase(value)]
        items.insert(0, value)
        self.set(key, items[:limit])

    @staticmethod
    def _folder_key(folder: str) -> str:
        return os.path.normcase(os.path.abspath(folder))

    def project_interpreter(self, folder: str | None) -> str:
        if folder:
            chosen = self.get("project_interpreters").get(self._folder_key(folder))
            if chosen:
                return chosen
        return self.get("interpreter")

    def set_project_interpreter(self, folder: str | None, path: str) -> None:
        if folder:
            mapping = dict(self.get("project_interpreters"))
            mapping[self._folder_key(folder)] = path
            self.set("project_interpreters", mapping)
        else:
            self.set("interpreter", path)
