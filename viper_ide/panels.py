"""Side and bottom panels: project explorer, outline, problems, search results."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil

from PyQt6.QtCore import QDir, QSortFilterProxyModel, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFileSystemModel, QIcon
from PyQt6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QMenu, QMessageBox, QPushButton, QToolButton, QTreeView, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)
from PyQt6.QtCore import QFile

from .icons import icon, kind_pixmap
from .workers import run_async

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", ".tox", ".idea", ".vs", "site-packages", ".eggs", "build", "dist"}


class _ExplorerProxy(QSortFilterProxyModel):
    HIDDEN = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vs",
              ".DS_Store", "Thumbs.db", "desktop.ini"}

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        return model.fileName(model.index(row, 0, parent)) not in self.HIDDEN

    def lessThan(self, left, right):
        model = self.sourceModel()
        ld, rd = model.isDir(left), model.isDir(right)
        if ld != rd:
            return ld
        return model.fileName(left).lower() < model.fileName(right).lower()


class ExplorerPanel(QWidget):
    open_file = pyqtSignal(str)
    run_file = pyqtSignal(str)
    open_terminal = pyqtSignal(str)
    open_folder_requested = pyqtSignal()
    path_renamed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(8, 4, 4, 4)
        self.title = QLabel("No folder open")
        self.title.setObjectName("Dim")
        head.addWidget(self.title, 1)
        for name, tip, fn in (("file", "New File", self.new_file), ("folder", "New Folder", self.new_folder),
                              ("refresh", "Refresh", self.refresh)):
            b = QToolButton()
            b.setIcon(icon(name))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            head.addWidget(b)
        lay.addLayout(head)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch()
        msg = QLabel("Open a folder to browse your project.")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        el.addWidget(msg)
        btn = QPushButton("Open Folder...")
        btn.setProperty("primary", True)
        btn.clicked.connect(self.open_folder_requested)
        el.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
        el.addStretch()
        lay.addWidget(self.empty, 1)

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
        self.proxy = _ExplorerProxy(self)
        self.proxy.setSourceModel(self.model)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setHeaderHidden(True)
        for col in (1, 2, 3):
            self.tree.hideColumn(col)
        self.tree.setAnimated(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.doubleClicked.connect(self._activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        self.tree.hide()

    def set_root(self, path: str | None) -> None:
        self.root = path
        if not path:
            self.tree.hide()
            self.empty.show()
            self.title.setText("No folder open")
            return
        src = self.model.setRootPath(path)
        self.tree.setRootIndex(self.proxy.mapFromSource(src))
        self.title.setText(os.path.basename(path.rstrip("\\/")) or path)
        self.title.setToolTip(path)
        self.empty.hide()
        self.tree.show()

    def refresh(self) -> None:
        if self.root:
            self.model.setRootPath("")
            self.set_root(self.root)

    def _path(self, index) -> str:
        return self.model.filePath(self.proxy.mapToSource(index))

    def selected_path(self) -> str | None:
        idx = self.tree.currentIndex()
        return self._path(idx) if idx.isValid() else None

    def _target_dir(self) -> str | None:
        p = self.selected_path() or self.root
        if p and os.path.isfile(p):
            p = os.path.dirname(p)
        return p

    def _activated(self, index) -> None:
        path = self._path(index)
        if os.path.isfile(path):
            self.open_file.emit(path)

    def new_file(self) -> None:
        folder = self._target_dir()
        if not folder:
            return
        name, ok = QInputDialog.getText(self, "New File", "File name:", text="new_file.py")
        if ok and name.strip():
            path = os.path.join(folder, name.strip())
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                open(path, "w", encoding="utf-8").close()
            self.open_file.emit(path)

    def new_folder(self) -> None:
        folder = self._target_dir()
        if not folder:
            return
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if ok and name.strip():
            os.makedirs(os.path.join(folder, name.strip()), exist_ok=True)

    def _menu(self, point) -> None:
        idx = self.tree.indexAt(point)
        path = self._path(idx) if idx.isValid() else self.root
        if not path:
            return
        is_file = os.path.isfile(path)
        menu = QMenu(self)
        if is_file:
            menu.addAction("Open", lambda: self.open_file.emit(path))
            if path.endswith((".py", ".pyw")):
                menu.addAction(icon("run"), "Run", lambda: self.run_file.emit(path))
            menu.addSeparator()
        menu.addAction("New File...", self.new_file)
        menu.addAction("New Folder...", self.new_folder)
        if idx.isValid():
            menu.addSeparator()
            menu.addAction("Rename...", lambda: self._rename(path))
            menu.addAction("Delete", lambda: self._delete(path))
        menu.addSeparator()
        menu.addAction("Copy Path", lambda: QApplication.clipboard().setText(path))
        folder = path if not is_file else os.path.dirname(path)
        menu.addAction("Reveal in File Manager", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
        menu.addAction(icon("terminal"), "Open Terminal Here", lambda: self.open_terminal.emit(folder))
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _rename(self, path: str) -> None:
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=os.path.basename(path))
        if ok and name.strip() and name != os.path.basename(path):
            new = os.path.join(os.path.dirname(path), name.strip())
            try:
                os.rename(path, new)
            except OSError as e:
                QMessageBox.warning(self, "Rename", str(e))
                return
            self.path_renamed.emit(path, new)

    def _delete(self, path: str) -> None:
        if QMessageBox.question(self, "Delete", f"Move '{os.path.basename(path)}' to the Recycle Bin?") \
                != QMessageBox.StandardButton.Yes:
            return
        if not QFile.moveToTrash(path):
            try:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            except OSError as e:
                QMessageBox.warning(self, "Delete", str(e))


class OutlinePanel(QTreeWidget):
    goto_line = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.itemActivated.connect(self._go)
        self.itemClicked.connect(self._go)

    def _go(self, item, _col=0) -> None:
        self.goto_line.emit(item.data(0, Qt.ItemDataRole.UserRole))

    def set_outline(self, items) -> None:
        if items is None:
            return  # file doesn't parse right now: keep the last good outline
        self.clear()

        def add(parent, entries):
            for kind, name, line, children in entries:
                it = QTreeWidgetItem([name])
                it.setIcon(0, QIcon(kind_pixmap(kind)))
                it.setData(0, Qt.ItemDataRole.UserRole, line)
                it.setToolTip(0, f"{kind} {name} (line {line})")
                (parent.addChild if parent else self.addTopLevelItem)(it)
                add(it, children)

        add(None, items)
        self.expandAll()


class ProblemsPanel(QTreeWidget):
    open_location = pyqtSignal(str, int, int)
    counts_changed = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Problem", "Line"])
        self.setColumnWidth(0, 520)
        self.setRootIsDecorated(True)
        self._by_file: dict[str, tuple[str, list[dict]]] = {}
        self.itemActivated.connect(self._go)
        self.itemDoubleClicked.connect(self._go)

    def set_problems(self, key: str, name: str, items: list[dict]) -> None:
        if items:
            self._by_file[key] = (name, items)
        else:
            self._by_file.pop(key, None)
        self._rebuild()

    def remove(self, key: str) -> None:
        if self._by_file.pop(key, None):
            self._rebuild()

    def _rebuild(self) -> None:
        self.clear()
        errors = warnings = 0
        for key, (name, items) in sorted(self._by_file.items(), key=lambda kv: kv[1][0].lower()):
            top = QTreeWidgetItem([f"{name}  ({len(items)})", ""])
            top.setToolTip(0, key)
            self.addTopLevelItem(top)
            for it in sorted(items, key=lambda i: (i["line"], i.get("col", 0))):
                child = QTreeWidgetItem([it["message"], str(it["line"])])
                child.setIcon(0, QIcon(kind_pixmap(it["severity"])))
                child.setData(0, Qt.ItemDataRole.UserRole, (key, it["line"], it.get("col", 0)))
                top.addChild(child)
                if it["severity"] == "error":
                    errors += 1
                else:
                    warnings += 1
            top.setExpanded(True)
        self.counts_changed.emit(errors, warnings)

    def _go(self, item, _col=0) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.open_location.emit(*data)


def search_files(root: str, query: str, regex: bool, case: bool, word: bool, globs: str,
                 limit: int = 5000) -> list[tuple]:
    pat = query if regex else re.escape(query)
    if word:
        pat = rf"\b{pat}\b"
    rx = re.compile(pat, 0 if case else re.IGNORECASE)
    patterns = [g.strip() for g in globs.split(",") if g.strip()] or ["*"]
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not any(fnmatch.fnmatch(fn, p) for p in patterns):
                continue
            path = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(path) > 2_000_000:
                    continue
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            if b"\0" in data[:2048]:
                continue
            text = data.decode("utf-8", "replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                m = rx.search(line)
                if m:
                    results.append((path, lineno, m.start(), line.strip()[:300]))
                    if len(results) >= limit:
                        return results
    return results


class SearchPanel(QWidget):
    open_location = pyqtSignal(str, int, int)

    def __init__(self, get_root, parent=None):
        super().__init__(parent)
        self.get_root = get_root
        self._generation = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.query = QLineEdit(placeholderText="Search in project (Enter)")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.search)
        lay.addWidget(self.query)
        opts = QHBoxLayout()
        self.case = QCheckBox("Aa")
        self.case.setToolTip("Match case")
        self.word = QCheckBox("Word")
        self.regex = QCheckBox(".*")
        self.regex.setToolTip("Regular expression")
        for w in (self.case, self.word, self.regex):
            opts.addWidget(w)
        self.globs = QLineEdit("*.py, *.txt, *.md, *.toml, *.cfg, *.ini, *.json, *.yaml, *.yml")
        self.globs.setToolTip("File patterns to include (comma separated)")
        opts.addWidget(self.globs, 1)
        lay.addLayout(opts)
        self.status = QLabel()
        self.status.setObjectName("Dim")
        lay.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemActivated.connect(self._go)
        self.tree.itemClicked.connect(self._go)
        lay.addWidget(self.tree, 1)

    def focus_query(self, text: str = "") -> None:
        if text:
            self.query.setText(text)
        self.query.setFocus()
        self.query.selectAll()

    def search(self) -> None:
        root = self.get_root()
        q = self.query.text()
        if not q:
            return
        if not root:
            self.status.setText("Open a folder to search in files.")
            return
        try:
            re.compile(q if self.regex.isChecked() else re.escape(q))
        except re.error as e:
            self.status.setText(f"Invalid regular expression: {e}")
            return
        self._generation += 1
        gen = self._generation
        self.status.setText("Searching...")
        run_async(search_files, root, q, self.regex.isChecked(), self.case.isChecked(), self.word.isChecked(),
                  self.globs.text(),
                  on_done=lambda rows: gen == self._generation and self.show_results(f"'{q}'", rows),
                  on_error=lambda msg: self.status.setText(msg))

    def show_results(self, title: str, rows: list[tuple]) -> None:
        self.tree.clear()
        root = self.get_root() or ""
        groups: dict[str, QTreeWidgetItem] = {}
        for path, line, col, text in rows:
            if path not in groups:
                rel = os.path.relpath(path, root) if root and path.startswith(root) else path
                top = QTreeWidgetItem([rel])
                top.setToolTip(0, path)
                self.tree.addTopLevelItem(top)
                groups[path] = top
            child = QTreeWidgetItem([f"{line}:  {text}"])
            child.setData(0, Qt.ItemDataRole.UserRole, (path, line, col))
            groups[path].addChild(child)
        self.tree.expandAll()
        n = len(rows)
        self.status.setText(f"{title}: {n} result{'s' if n != 1 else ''} in {len(groups)} file"
                            f"{'s' if len(groups) != 1 else ''}" if n else f"{title}: no results")

    def _go(self, item, _col=0) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.open_location.emit(*data)


def list_project_files(root: str, limit: int = 20000) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith((".pyc", ".pyo", ".pyd", ".dll", ".exe", ".so")):
                continue
            out.append(os.path.join(dirpath, fn))
            if len(out) >= limit:
                return out
    return out


def pick_folder(parent, title="Open Folder", start="") -> str:
    return QFileDialog.getExistingDirectory(parent, title, start)
