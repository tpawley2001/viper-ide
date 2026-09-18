"""Main window: wires editors, panels, interpreters, smart installs, running and debugging."""
from __future__ import annotations

import html
import logging
import os
import sys
import traceback

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QFileSystemWatcher, QProcess, Qt, QTimer
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                             QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
                             QPushButton, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from . import APP_NAME, ORG_NAME, __version__, assistant, imports, interpreters, intel, packages, updater
from .assistantui import AssistantPanel
from .debugui import DebugPanel, DebugSession
from .dialogs import CommandPalette, RenamePreviewDialog, SettingsDialog
from .editor import EditorPage
from .icons import app_icon_pixmap, icon
from .intel import IntelEngine
from .packagesui import PackagesPanel, PythonDownloadDialog
from .panels import ExplorerPanel, OutlinePanel, ProblemsPanel, SearchPanel, list_project_files, pick_folder
from .paths import data_dir, is_frozen
from .updateui import UpdateDialog
from .runner import ConsolePanel, RunPanel, TerminalPanel, activated_env
from .settings import Settings
from .theme import apply_app_theme, theme
from .widgets import InfoBar
from .workers import run_async

IS_WIN = os.name == "nt"
log = logging.getLogger("viper")


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, open_paths: list[str] | None = None):
        super().__init__()
        self.settings = settings
        self.theme = theme(settings.get("theme"))
        self.project: str | None = None
        self.interpreters: list[interpreters.Interpreter] = []
        self.interp: interpreters.Interpreter | None = None
        self._routes: dict[int, tuple] = {}
        self._imports: dict[int, dict] = {}
        self._runtime_attempted: set = set()
        self._last_run = None
        self._project_files: list[str] | None = None
        self._zoom = 0
        self._shortcut_keys: set[int] = set()

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(app_icon_pixmap()))
        self.resize(1400, 880)
        self.setAcceptDrops(True)
        self.intel = IntelEngine(self)
        self.intel.result.connect(self._on_intel)
        self.pip = packages.PipRunner(self)
        self.debug = DebugSession(self)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._file_changed)

        self._build_central()
        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._wire()
        self._update_debug_actions()

        geometry = settings.get("window_geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        state = settings.get("window_state")
        if state:
            self.restoreState(QByteArray.fromBase64(state.encode()))
        self.debug_dock.hide()
        self._restore(open_paths or [])
        self._update_welcome()
        QTimer.singleShot(0, self.refresh_interpreters)
        if settings.get("auto_check_updates") and is_frozen() and IS_WIN:
            QTimer.singleShot(4000, lambda: self.check_for_updates(silent=True))

    # ================================================================== layout
    def _build_central(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.global_bar = InfoBar(central)
        lay.addWidget(self.global_bar)
        self.stack = QStackedWidget()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(lambda i: self.close_tab(self.tabs.widget(i)))
        self.tabs.currentChanged.connect(self._tab_changed)
        self.stack.addWidget(self._welcome_page())
        self.stack.addWidget(self.tabs)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

    def _welcome_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addStretch(1)
        logo = QLabel()
        logo.setPixmap(app_icon_pixmap(96))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(logo)
        title = QLabel(f"<h1 style='margin:4px'>{APP_NAME}</h1>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)
        sub = QLabel("Write Python. Missing packages are found and installed for you.")
        sub.setObjectName("Dim")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(sub)
        row = QHBoxLayout()
        row.addStretch()
        for label, fn, primary in (("New File", self.new_file, True), ("Open File...", self.open_file_dialog, False),
                                   ("Open Folder...", self.open_folder_dialog, False)):
            b = QPushButton(label)
            b.setProperty("primary", primary)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        outer.addSpacing(12)
        outer.addLayout(row)
        self.recent_list = QListWidget()
        self.recent_list.setMaximumWidth(560)
        self.recent_list.setMaximumHeight(220)
        self.recent_list.itemActivated.connect(lambda it: self.open_path(it.data(Qt.ItemDataRole.UserRole)))
        self.recent_list.itemClicked.connect(lambda it: self.open_path(it.data(Qt.ItemDataRole.UserRole)))
        wrap = QHBoxLayout()
        wrap.addStretch()
        wrap.addWidget(self.recent_list)
        wrap.addStretch()
        outer.addSpacing(16)
        outer.addLayout(wrap)
        outer.addStretch(2)
        return page

    def _dock(self, title: str, widget: QWidget, area, name: str) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                         | QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(area, dock)
        return dock

    def _build_docks(self) -> None:
        L, B, R = (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.BottomDockWidgetArea,
                   Qt.DockWidgetArea.RightDockWidgetArea)
        t = self.theme
        self.explorer = ExplorerPanel()
        self.outline = OutlinePanel()
        self.search = SearchPanel(lambda: self.project)
        self.run_panel = RunPanel(t, self.settings)
        self.console = ConsolePanel(t, lambda: self.interp)
        self.terminal = TerminalPanel(t, lambda: self.interp, lambda: self.project or self._current_dir())
        self.problems = ProblemsPanel()
        self.packages_panel = PackagesPanel(self.pip, lambda: self.interp, lambda: self.project, t)
        self.debug_panel = DebugPanel(self.debug, t)
        self.assistant = AssistantPanel(self, self.settings)

        self.explorer_dock = self._dock("Project", self.explorer, L, "dock_project")
        self.outline_dock = self._dock("Outline", self.outline, L, "dock_outline")
        self.search_dock = self._dock("Search", self.search, L, "dock_search")
        self.tabifyDockWidget(self.explorer_dock, self.outline_dock)
        self.tabifyDockWidget(self.outline_dock, self.search_dock)
        self.explorer_dock.raise_()
        self.run_dock = self._dock("Run", self.run_panel, B, "dock_run")
        self.console_dock = self._dock("Python Console", self.console, B, "dock_console")
        self.terminal_dock = self._dock("Terminal", self.terminal, B, "dock_terminal")
        self.problems_dock = self._dock("Problems", self.problems, B, "dock_problems")
        self.packages_dock = self._dock("Packages", self.packages_panel, B, "dock_packages")
        for a, b in ((self.run_dock, self.console_dock), (self.console_dock, self.terminal_dock),
                     (self.terminal_dock, self.problems_dock), (self.problems_dock, self.packages_dock)):
            self.tabifyDockWidget(a, b)
        self.run_dock.raise_()
        self.debug_dock = self._dock("Debugger", self.debug_panel, R, "dock_debug")
        self.assistant_dock = self._dock("AI Assistant", self.assistant, R, "dock_assistant")
        self.assistant_dock.hide()  # restoreState() brings it back if it was open last session
        self._assistant_models_loaded = False
        self.assistant_dock.visibilityChanged.connect(self._assistant_visible)
        self.resizeDocks([self.explorer_dock], [270], Qt.Orientation.Horizontal)
        self.resizeDocks([self.run_dock], [250], Qt.Orientation.Vertical)
        self.resizeDocks([self.debug_dock, self.assistant_dock], [380, 420], Qt.Orientation.Horizontal)

    def _act(self, text, slot, shortcut=None, icon_name=None, tip=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
        if shortcut:
            seqs = shortcut if isinstance(shortcut, list) else [shortcut]
            a.setShortcuts([QKeySequence(s) for s in seqs])
            for s in seqs:
                seq = QKeySequence(s)
                if seq.count():
                    self._shortcut_keys.add(seq[0].toCombined())
        if tip:
            a.setToolTip(tip)
        a.setCheckable(checkable)
        if checkable:
            a.toggled.connect(slot)
        else:
            a.triggered.connect(lambda _checked=False: slot())
        self.addAction(a)
        return a

    def _build_actions(self) -> None:
        A = self._act
        Std = QKeySequence.StandardKey
        ed = self._with_editor
        self.a = a = {}
        a["new"] = A("New File", self.new_file, "Ctrl+N", "file")
        a["open"] = A("Open File...", self.open_file_dialog, "Ctrl+O")
        a["open_folder"] = A("Open Folder...", self.open_folder_dialog, "Ctrl+K", "folder")
        a["save"] = A("Save", self.save, "Ctrl+S", "save")
        a["save_as"] = A("Save As...", self.save_as, "Ctrl+Shift+S")
        a["save_all"] = A("Save All", self.save_all, "Ctrl+Alt+S")
        a["close_tab"] = A("Close Tab", lambda: self.close_tab(self.page()), "Ctrl+W")
        a["close_folder"] = A("Close Folder", lambda: self.set_project(None))
        a["settings"] = A("Settings...", self.open_settings, "Ctrl+,")
        a["exit"] = A("Exit", self.close, "Alt+F4")

        a["undo"] = A("Undo", ed(lambda e: e.undo()), "Ctrl+Z")
        a["redo"] = A("Redo", ed(lambda e: e.redo()), ["Ctrl+Y", "Ctrl+Shift+Z"])
        a["cut"] = A("Cut", ed(lambda e: e.cut()), "Ctrl+X")
        a["copy"] = A("Copy", ed(lambda e: e.copy()), "Ctrl+C")
        a["paste"] = A("Paste", ed(lambda e: e.paste()), "Ctrl+V")
        a["select_all"] = A("Select All", ed(lambda e: e.selectAll()), "Ctrl+A")
        a["find"] = A("Find...", lambda: self.page() and self.page().find_bar.open(False), "Ctrl+F", "search")
        a["replace"] = A("Replace...", lambda: self.page() and self.page().find_bar.open(True), "Ctrl+H")
        a["find_files"] = A("Find in Files...", self.find_in_files, "Ctrl+Shift+F")
        a["goto_line"] = A("Go to Line...", self.goto_line_dialog, "Ctrl+G")
        a["comment"] = A("Toggle Comment", ed(lambda e: e.toggle_comment()), "Ctrl+/")
        a["duplicate"] = A("Duplicate Line", ed(lambda e: e.duplicate()), "Ctrl+D")
        a["line_up"] = A("Move Line Up", ed(lambda e: e.move_lines(True)), "Alt+Up")
        a["line_down"] = A("Move Line Down", ed(lambda e: e.move_lines(False)), "Alt+Down")
        a["next_occ"] = A("Add Next Occurrence to Selection", ed(lambda e: e.select_next_occurrence()), "Ctrl+Shift+L")
        a["indent"] = A("Indent", ed(lambda e: e.indent(e.getCursorPosition()[0]) if not e.hasSelectedText()
                                      else self._indent_selection(e, True)), "Ctrl+]")
        a["dedent"] = A("Unindent", ed(lambda e: e.unindent(e.getCursorPosition()[0]) if not e.hasSelectedText()
                                       else self._indent_selection(e, False)), "Ctrl+[")
        a["trim"] = A("Trim Trailing Whitespace", ed(lambda e: e.trim_trailing_whitespace()))
        a["format"] = A("Format Document", self.format_document, "Ctrl+Alt+L")

        a["palette"] = A("Command Palette...", self.command_palette, "Ctrl+Shift+P")
        a["quick_open"] = A("Go to File...", self.quick_open, "Ctrl+P")
        a["zoom_in"] = A("Zoom In", lambda: self._zoom_by(1), ["Ctrl+=", "Ctrl++"])
        a["zoom_out"] = A("Zoom Out", lambda: self._zoom_by(-1), "Ctrl+-")
        a["zoom_reset"] = A("Reset Zoom", lambda: self._zoom_by(None), "Ctrl+0")
        a["wrap"] = A("Word Wrap", lambda on: self._toggle_setting("word_wrap", on), "Alt+Z", checkable=True)
        a["wrap"].setChecked(bool(self.settings.get("word_wrap")))
        a["whitespace"] = A("Show Whitespace", lambda on: self._toggle_setting("show_whitespace", on), checkable=True)
        a["whitespace"].setChecked(bool(self.settings.get("show_whitespace")))
        a["theme_dark"] = A("Dark Theme", lambda: self.set_theme("dark"))
        a["theme_light"] = A("Light Theme", lambda: self.set_theme("light"))

        a["complete"] = A("Trigger Completion", ed(lambda e: e.request_completion(True)))
        a["goto_def"] = A("Go to Definition", ed(self.goto_definition), "F12")
        a["references"] = A("Find References", ed(lambda e: self.intel_request(e, "references")), "Shift+F12")
        a["rename"] = A("Rename Symbol...", ed(self.rename_symbol), "F2")
        a["docs"] = A("Show Documentation", ed(lambda e: self.intel_request(e, "hover", docs=True)), "Ctrl+Q")
        a["assistant"] = A("AI Assistant", self.show_assistant, "Ctrl+Shift+A", "assistant",
                           "AI Assistant (Ctrl+Shift+A)")
        a["ask_ai"] = A("Ask AI to Edit...", ed(lambda e: self.show_assistant(with_file=True)), "Ctrl+I")
        a["ai_providers"] = A("Manage AI Providers...", self.assistant.manage_providers)

        a["run"] = A("Run File", lambda: self.run_file(False), "F5", "run", "Run the current file (F5)")
        a["run_args"] = A("Run with Arguments...", lambda: self.run_file(False, ask_args=True), "Ctrl+Shift+F5")
        a["debug"] = A("Debug File", lambda: self.run_file(True), "F6", "debug", "Debug the current file (F6)")
        a["stop"] = A("Stop", self.stop_running, "Shift+F5", "stop", "Stop (Shift+F5)")
        a["continue"] = A("Continue", lambda: self.debug.command("continue"), "F8", "continue", "Continue (F8)")
        a["pause"] = A("Pause", self.debug.pause, None, "pause", "Pause")
        a["step_over"] = A("Step Over", lambda: self.debug.command("next"), "F10", "step_over", "Step Over (F10)")
        a["step_into"] = A("Step Into", lambda: self.debug.command("stepIn"), "F11", "step_into", "Step Into (F11)")
        a["step_out"] = A("Step Out", lambda: self.debug.command("stepOut"), "Shift+F11", "step_out",
                          "Step Out (Shift+F11)")
        a["breakpoint"] = A("Toggle Breakpoint", ed(lambda e: e.toggle_breakpoint()), "F9")
        a["clear_bps"] = A("Remove All Breakpoints", self.clear_all_breakpoints)
        a["run_selection"] = A("Run Selection in Console", ed(lambda e: self._console_run(e.selection_or_line())),
                               "Shift+Return")
        a["run_cell"] = A("Run Cell (# %%) in Console", ed(lambda e: self._console_run(e.current_cell())),
                          "Ctrl+Return")

        a["select_interp"] = A("Select Interpreter...", self.select_interpreter_menu, None, "python")
        a["create_venv"] = A("Create Virtual Environment...", self.create_venv)
        a["download_python"] = A("Download Python...", self.download_python)
        a["check_missing"] = A("Check for Missing Packages", self.check_missing_now)
        a["install_missing"] = A("Install Missing Packages", self.install_missing_current)
        a["install_reqs"] = A("Install requirements.txt", lambda: self.packages_panel.install_requirements(
            os.path.join(self.project, "requirements.txt") if self.project and
            os.path.isfile(os.path.join(self.project, "requirements.txt")) else None))
        a["packages"] = A("Manage Packages", lambda: self._show_dock(self.packages_dock), "Ctrl+Alt+P", "package")
        a["restart_console"] = A("Restart Python Console", self.console.restart)
        a["about"] = A(f"About {APP_NAME}", self.about)
        a["check_updates"] = A("Check for Updates...", lambda: self.check_for_updates(silent=False))
        a["data_folder"] = A("Open Viper Data Folder", lambda: self._reveal(str(data_dir())))

    def _build_menus(self) -> None:
        a, mb = self.a, self.menuBar()

        def menu(title, items):
            m = mb.addMenu(title)
            for it in items:
                if it is None:
                    m.addSeparator()
                elif isinstance(it, QMenu):
                    m.addMenu(it)
                else:
                    m.addAction(it)
            return m

        self.recent_files_menu = QMenu("Recent Files", self)
        self.recent_files_menu.aboutToShow.connect(lambda: self._fill_recent(self.recent_files_menu, "recent_files"))
        self.recent_folders_menu = QMenu("Recent Folders", self)
        self.recent_folders_menu.aboutToShow.connect(
            lambda: self._fill_recent(self.recent_folders_menu, "recent_folders"))
        menu("&File", [a["new"], a["open"], a["open_folder"], self.recent_files_menu, self.recent_folders_menu, None,
                       a["save"], a["save_as"], a["save_all"], None, a["close_tab"], a["close_folder"], None,
                       a["settings"], None, a["exit"]])
        menu("&Edit", [a["undo"], a["redo"], None, a["cut"], a["copy"], a["paste"], a["select_all"], None, a["find"],
                       a["replace"], a["find_files"], None, a["comment"], a["duplicate"], a["line_up"], a["line_down"],
                       a["next_occ"], a["indent"], a["dedent"], None, a["format"], a["trim"]])
        view = menu("&View", [a["palette"], a["quick_open"], a["goto_line"], None])
        for dock in (self.explorer_dock, self.outline_dock, self.search_dock, self.run_dock, self.console_dock,
                     self.terminal_dock, self.problems_dock, self.packages_dock, self.debug_dock, self.assistant_dock):
            view.addAction(dock.toggleViewAction())
        for it in (None, a["zoom_in"], a["zoom_out"], a["zoom_reset"], None, a["wrap"], a["whitespace"], None,
                   a["theme_dark"], a["theme_light"]):
            view.addSeparator() if it is None else view.addAction(it)
        self.ai_provider_menu = QMenu("AI Provider", self)
        self.ai_provider_menu.aboutToShow.connect(self._fill_ai_provider_menu)
        menu("&Code", [a["complete"], a["docs"], a["goto_def"], a["references"], a["rename"], None, a["format"], None,
                       a["assistant"], a["ask_ai"], self.ai_provider_menu])
        menu("&Run", [a["run"], a["run_args"], a["debug"], a["stop"], None, a["continue"], a["pause"],
                      a["step_over"], a["step_into"], a["step_out"], None, a["breakpoint"], a["clear_bps"], None,
                      a["run_selection"], a["run_cell"], a["restart_console"]])
        self.interp_menu = QMenu("Interpreter", self)
        self.interp_menu.aboutToShow.connect(self._fill_interp_menu)
        menu("&Python", [self.interp_menu, a["create_venv"], a["download_python"], None, a["check_missing"],
                         a["install_missing"], a["install_reqs"], a["packages"]])
        menu("&Help", [a["palette"], None, a["check_updates"], a["data_folder"], a["about"]])

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        tb.setObjectName("toolbar_main")
        tb.setMovable(False)
        a = self.a
        for it in (a["new"], a["open_folder"], a["save"], None, a["run"], a["debug"], a["stop"], None, a["continue"],
                   a["pause"], a["step_over"], a["step_into"], a["step_out"], None, a["packages"], a["assistant"]):
            tb.addSeparator() if it is None else tb.addAction(it)
        self.debug_panel.set_actions([a["continue"], a["pause"], a["step_over"], a["step_into"], a["step_out"],
                                      a["stop"]])

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.busy_label = QLabel()
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setFixedWidth(90)
        self.busy_bar.setMaximumHeight(12)
        self.busy_label.hide()
        self.busy_bar.hide()
        sb.addPermanentWidget(self.busy_label)
        sb.addPermanentWidget(self.busy_bar)
        self.problem_button = QToolButton(text="✕ 0  ⚠ 0")
        self.problem_button.setAutoRaise(True)
        self.problem_button.clicked.connect(lambda: self._show_dock(self.problems_dock))
        sb.addPermanentWidget(self.problem_button)
        self.cursor_label = QLabel("")
        sb.addPermanentWidget(self.cursor_label)
        self.eol_button = QToolButton(text="")
        self.eol_button.setAutoRaise(True)
        self.eol_button.setToolTip("Line endings (click to switch)")
        self.eol_button.clicked.connect(self._toggle_eol)
        sb.addPermanentWidget(self.eol_button)
        self.encoding_label = QLabel("")
        sb.addPermanentWidget(self.encoding_label)
        self.interp_button = QToolButton(text="No interpreter")
        self.interp_button.setIcon(icon("python"))
        self.interp_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.interp_button.setAutoRaise(True)
        self.interp_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.interp_button.setMenu(self.interp_menu)
        sb.addPermanentWidget(self.interp_button)

    def _wire(self) -> None:
        self.explorer.open_file.connect(self.open_file)
        self.explorer.run_file.connect(self._run_path)
        self.explorer.open_terminal.connect(self._terminal_at)
        self.explorer.open_folder_requested.connect(self.open_folder_dialog)
        self.explorer.path_renamed.connect(self._path_renamed)
        self.outline.goto_line.connect(lambda line: self.editor() and self.editor().goto_line(line))
        self.problems.open_location.connect(lambda p, line, col: self.open_file(p, line, col))
        self.problems.counts_changed.connect(
            lambda e, w: self.problem_button.setText(f"✕ {e}  ⚠ {w}"))
        self.search.open_location.connect(lambda p, line, col: self.open_file(p, line, col))
        for view in (self.run_panel.view, self.console.view, self.terminal.view, self.debug_panel.console):
            view.open_location.connect(lambda p, line: self.open_file(p, line))
        self.run_panel.finished.connect(self._run_finished)
        self.run_panel.started.connect(self._update_debug_actions)
        self.debug.stopped.connect(self._debug_stopped)
        self.debug.running.connect(self._clear_debug_lines)
        self.debug.state_changed.connect(self._update_debug_actions)
        self.debug_panel.frame_selected.connect(self._show_debug_location)
        self.pip.busy_changed.connect(self._pip_busy)
        self.pip.job_started.connect(lambda title: self.busy_label.setText(title))
        self.packages_panel.packages_changed.connect(self._packages_changed)

    # ================================================================ helpers
    def page(self) -> EditorPage | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, EditorPage) else None

    def editor(self):
        p = self.page()
        return p.editor if p else None

    def pages(self) -> list[EditorPage]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def _with_editor(self, fn):
        def run():
            e = self.editor()
            if e is not None:
                fn(e)
        return run

    def _find_page(self, path: str) -> EditorPage | None:
        key = norm(path)
        return next((p for p in self.pages() if p.editor.path and norm(p.editor.path) == key), None)

    def _current_dir(self) -> str | None:
        e = self.editor()
        return os.path.dirname(e.path) if e and e.path else None

    def _show_dock(self, dock: QDockWidget) -> None:
        dock.show()
        dock.raise_()

    def _reveal(self, path: str) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def is_ide_shortcut(self, combo: int) -> bool:
        return combo in self._shortcut_keys and combo not in self._editor_native_keys()

    @staticmethod
    def _editor_native_keys() -> set[int]:
        # The editor implements these itself (multi-caret aware), so don't let the window grab them.
        return {QKeySequence(s)[0].toCombined() for s in ("Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z", "Ctrl+X", "Ctrl+C",
                                                           "Ctrl+V", "Ctrl+A")}

    def editor_context_actions(self) -> list:
        a = self.a
        return [a["ask_ai"], None, a["goto_def"], a["references"], a["rename"], a["docs"], None, a["run_selection"],
                a["comment"], a["format"]]

    def _indent_selection(self, e, indent: bool) -> None:
        lf, _, lt, it = e.getSelection()
        if it == 0 and lt > lf:
            lt -= 1
        e.beginUndoAction()
        for line in range(lf, lt + 1):
            (e.indent if indent else e.unindent)(line)
        e.endUndoAction()

    def _fill_recent(self, menu: QMenu, key: str) -> None:
        menu.clear()
        items = [p for p in self.settings.get(key) if os.path.exists(p)]
        for p in items:
            menu.addAction(p, lambda p=p: self.open_path(p))
        if not items:
            menu.addAction("(empty)").setEnabled(False)

    def _update_welcome(self) -> None:
        has_tabs = self.tabs.count() > 0
        self.stack.setCurrentIndex(1 if has_tabs else 0)
        if not has_tabs:
            self.recent_list.clear()
            for key, prefix in (("recent_folders", "\U0001F4C1  "), ("recent_files", "")):
                for p in self.settings.get(key)[:6]:
                    if os.path.exists(p):
                        it = QListWidgetItem(prefix + p)
                        it.setData(Qt.ItemDataRole.UserRole, p)
                        self.recent_list.addItem(it)
            self.recent_list.setVisible(self.recent_list.count() > 0)
            self.outline.clear()
        self._update_title()

    def _update_title(self) -> None:
        parts = []
        e = self.editor()
        if e:
            parts.append(e.display_name() + (" •" if e.isModified() else ""))
        if self.project:
            parts.append(os.path.basename(self.project.rstrip("\\/")))
        parts.append(APP_NAME)
        self.setWindowTitle(" - ".join(parts))

    def _pip_busy(self, busy: bool) -> None:
        self.busy_bar.setVisible(busy)
        self.busy_label.setVisible(busy)

    # ================================================================== files
    def new_file(self) -> EditorPage:
        page = EditorPage(self, self.settings, self.theme)
        e = page.editor
        e.modificationChanged.connect(lambda _m, p=page: self._refresh_tab(p))
        e.content_idle.connect(self._editor_idle)
        e.breakpoints_changed.connect(self._breakpoints_changed)
        e.goto_requested.connect(self.goto_definition)
        e.cursorPositionChanged.connect(lambda *_: self._update_cursor_label())
        if self._zoom:
            e.zoomTo(self._zoom)
        self.tabs.addTab(page, e.display_name())
        self.tabs.setCurrentWidget(page)
        self._update_welcome()
        e.setFocus()
        return page

    def open_path(self, path: str) -> None:
        if os.path.isdir(path):
            self.set_project(path)
        elif os.path.isfile(path):
            self.open_file(path)

    def open_file_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open File", self.project or self._current_dir() or "",
                                                "Python files (*.py *.pyw);;Text files (*.txt *.md *.toml *.cfg *.ini "
                                                "*.json *.yaml *.yml);;All files (*)")
        for p in paths:
            self.open_file(p)

    def open_folder_dialog(self) -> None:
        path = pick_folder(self, "Open Folder", self.project or "")
        if path:
            self.set_project(path)

    def open_file(self, path: str, line: int | None = None, col: int = 0) -> EditorPage | None:
        path = os.path.abspath(path)
        page = self._find_page(path)
        if page is None:
            if not os.path.isfile(path):
                self.statusBar().showMessage(f"File not found: {path}", 5000)
                return None
            current = self.page()
            reuse = current if current and not current.editor.path and not current.editor.isModified() \
                and not current.editor.text() else None
            page = self.new_file()
            try:
                page.editor.load(path)
            except (OSError, UnicodeError) as e:
                self.close_tab(page, force=True)
                QMessageBox.warning(self, "Open File", f"Couldn't open {path}:\n{e}")
                return None
            if reuse:
                self.close_tab(reuse, force=True)
            saved = self.settings.get("breakpoints").get(norm(path))
            if saved:
                page.editor.set_breakpoints(saved)
            self.watcher.addPath(path)
            self.settings.add_recent("recent_files", path)
            self._refresh_tab(page)
            self._editor_idle(page.editor)
        self.tabs.setCurrentWidget(page)
        if line:
            page.editor.goto_line(line, col)
        return page

    def _refresh_tab(self, page: EditorPage) -> None:
        i = self.tabs.indexOf(page)
        if i < 0:
            return
        e = page.editor
        self.tabs.setTabText(i, e.display_name() + (" •" if e.isModified() else ""))
        self.tabs.setTabToolTip(i, e.path or "Not saved yet")
        self._update_title()

    def save(self, page: EditorPage | None = None) -> bool:
        page = page or self.page()
        if page is None:
            return False
        e = page.editor
        if not e.path:
            return self.save_as(page)
        if self.settings.get("format_on_save") and self.interp and \
                packages.tool_installed(self.settings.get("formatter"), self.interp.version):
            try:
                new = packages.run_formatter(self.settings.get("formatter"), self.interp.path, self.interp.version,
                                             e.text(), e.path)
                if new != e.text():
                    e.set_text_undoable(new)
            except Exception as ex:  # noqa: BLE001 - a formatter failure must never block saving
                self.statusBar().showMessage(f"Format on save skipped: {str(ex).splitlines()[0]}", 6000)
        try:
            e.save()
        except (OSError, UnicodeError) as ex:
            QMessageBox.warning(self, "Save", f"Couldn't save {e.path}:\n{ex}")
            return False
        if e.path not in self.watcher.files():
            self.watcher.addPath(e.path)
        page.info.clear("disk")
        self._refresh_tab(page)
        self.statusBar().showMessage(f"Saved {e.path}", 3000)
        self._editor_idle(e)
        return True

    def save_as(self, page: EditorPage | None = None) -> bool:
        page = page or self.page()
        if page is None:
            return False
        e = page.editor
        start = e.path or os.path.join(self.project or os.path.expanduser("~"), e.untitled_name)
        path, _ = QFileDialog.getSaveFileName(self, "Save As", start, "Python files (*.py *.pyw);;All files (*)")
        if not path:
            return False
        old = e.path
        try:
            e.save(path)
        except (OSError, UnicodeError) as ex:
            QMessageBox.warning(self, "Save As", f"Couldn't save {path}:\n{ex}")
            return False
        if old and old in self.watcher.files():
            self.watcher.removePath(old)
        self.watcher.addPath(e.path)
        self.settings.add_recent("recent_files", e.path)
        self._refresh_tab(page)
        self._editor_idle(e)
        return True

    def save_all(self) -> None:
        for p in self.pages():
            if p.editor.isModified():
                self.save(p)

    def close_tab(self, page: EditorPage | None, force: bool = False) -> bool:
        if page is None or sip.isdeleted(page):
            return False
        e = page.editor
        if e.isModified() and not force:
            self.tabs.setCurrentWidget(page)
            answer = QMessageBox.question(self, "Unsaved Changes", f"Save changes to {e.display_name()}?",
                                          QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                          | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if answer == QMessageBox.StandardButton.Save and not self.save(page):
                return False
        if e.path:
            self._persist_breakpoints(e)
            if e.path in self.watcher.files():
                self.watcher.removePath(e.path)
        self.problems.remove(e.path or e.untitled_name)
        self._imports.pop(id(page), None)
        self.tabs.removeTab(self.tabs.indexOf(page))
        page.deleteLater()
        self._update_welcome()
        return True

    def _path_renamed(self, old: str, new: str) -> None:
        page = self._find_page(old)
        if page:
            if old in self.watcher.files():
                self.watcher.removePath(old)
            page.editor.path = os.path.abspath(new)
            self.watcher.addPath(new)
            self._refresh_tab(page)

    def _file_changed(self, path: str) -> None:
        page = self._find_page(path)
        if page is None:
            return
        e = page.editor
        if not os.path.exists(path):
            page.info.show_message("warning", "This file was deleted or moved on disk.",
                                   [("Save Again", lambda: self.save(page), True)], tag="disk")
            return
        if path not in self.watcher.files():
            self.watcher.addPath(path)
        if e.saved_mtime == os.stat(path).st_mtime:
            return

        def reload():
            line, col = e.getCursorPosition()
            try:
                e.load(path)
            except (OSError, UnicodeError) as ex:
                page.info.show_message("warning", f"Couldn't reload this file: {html.escape(str(ex))}", tag="disk")
                return
            e.setCursorPosition(min(line, e.lines() - 1), col)
            page.info.clear("disk")
            self._editor_idle(e)

        if not e.isModified():
            reload()
        else:
            page.info.show_message("warning", "This file changed on disk, and you have unsaved edits.",
                                   [("Reload from Disk", reload, False), ("Keep My Version", lambda: page.info.clear("disk"), True)],
                                   tag="disk")

    def set_project(self, path: str | None) -> None:
        self.project = os.path.abspath(path) if path else None
        self._project_files = None
        self.explorer.set_root(self.project)
        if self.project:
            self.settings.add_recent("recent_folders", self.project)
            self._show_dock(self.explorer_dock)
        self.global_bar.clear("requirements")
        self._update_title()
        self.intel.invalidate()
        if self.interpreters or self.project:
            self.refresh_interpreters()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.open_path(url.toLocalFile())

    # ============================================================ interpreters
    def refresh_interpreters(self) -> None:
        self.interp_button.setText("Finding Python...")
        project = self.project
        run_async(interpreters.discover, project, self.settings.get("extra_interpreters"),
                  on_done=lambda found: self._interpreters_found(found, project),
                  on_error=lambda msg: self.statusBar().showMessage(f"Interpreter scan failed: {msg}", 8000))

    def _interpreters_found(self, found: list, project: str | None) -> None:
        if project != self.project:
            return
        self.interpreters = found
        preferred = self.settings.project_interpreter(self.project)
        chosen = next((i for i in found if preferred and norm(i.path) == norm(preferred)), None)
        if chosen is None and self.project:
            chosen = next((i for i in found if i.is_venv and norm(i.path).startswith(norm(self.project))), None)
        if chosen is None and self.interp:
            chosen = next((i for i in found if norm(i.path) == norm(self.interp.path)), None)
        if chosen is None:
            chosen = next((i for i in found if not i.is_venv), found[0] if found else None)
        self.set_interpreter(chosen, remember=False)
        if not found:
            self.global_bar.show_message(
                "warning", "<b>No Python interpreter was found on this computer.</b> Viper can download and "
                           "install one for you (no administrator rights needed).",
                [("Download Python", self.download_python, True), ("Browse...", self.browse_interpreter, False)],
                tag="nopython")
        else:
            self.global_bar.clear("nopython")

    def set_interpreter(self, interp, remember: bool = True) -> None:
        changed = (interp and interp.path) != (self.interp and self.interp.path)
        self.interp = interp
        if interp:
            self.interp_button.setText(interp.label())
            self.interp_button.setToolTip(interp.path)
            if remember:
                self.settings.set_project_interpreter(self.project, interp.path)
        else:
            self.interp_button.setText("No interpreter")
            self.interp_button.setToolTip("")
        if changed or remember:
            self.intel.invalidate()
            self.packages_panel.interpreter_changed()
            if self.console.running():
                self.console.restart()
            for p in self.pages():
                self.check_imports(p, force=True)
        self.check_project_requirements()

    def _fill_interp_menu(self) -> None:
        m = self.interp_menu
        m.clear()
        for i in self.interpreters:
            act = m.addAction(i.label(), lambda i=i: self.set_interpreter(i))
            act.setCheckable(True)
            act.setChecked(bool(self.interp and norm(i.path) == norm(self.interp.path)))
            act.setToolTip(i.path)
            act.setStatusTip(i.path)
        if not self.interpreters:
            m.addAction("(no interpreters found)").setEnabled(False)
        m.addSeparator()
        m.addAction("Browse for python.exe...", self.browse_interpreter)
        m.addAction("Create Virtual Environment...", self.create_venv)
        m.addAction("Download Python...", self.download_python)
        m.addAction("Rescan", self.refresh_interpreters)

    def select_interpreter_menu(self) -> None:
        self._fill_interp_menu()
        self.interp_menu.exec(self.interp_button.mapToGlobal(self.interp_button.rect().topLeft()))

    def browse_interpreter(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Python interpreter", "",
                                              "Python (python.exe pythonw.exe)" if IS_WIN else "All files (*)")
        if not path:
            return

        def done(interp):
            if not interp:
                QMessageBox.warning(self, "Interpreter", f"{path} doesn't look like a working Python interpreter.")
                return
            extra = self.settings.get("extra_interpreters")
            if interp.path not in extra:
                self.settings.set("extra_interpreters", extra + [interp.path])
            self.interpreters.insert(0, interp)
            self.global_bar.clear("nopython")
            self.set_interpreter(interp)

        run_async(interpreters.probe, path, on_done=done)

    def download_python(self) -> None:
        dlg = PythonDownloadDialog(self)
        if dlg.exec() and dlg.interpreter:
            self.interpreters.insert(0, dlg.interpreter)
            self.global_bar.clear("nopython")
            self.set_interpreter(dlg.interpreter)
            self.statusBar().showMessage(f"Installed {dlg.interpreter.label()}", 8000)

    def create_venv(self) -> None:
        base = self.interp
        if base is None:
            self.download_python()
            return
        if base.is_venv:
            parent = next((i for i in self.interpreters if not i.is_venv and i.version == base.version), None)
            base = parent or base
        folder = self.project or pick_folder(self, "Folder for the new environment")
        if not folder:
            return
        name, ok = QInputDialog.getText(self, "Create Virtual Environment",
                                        f"A new environment based on {base.label()} will be created in:\n{folder}\n\n"
                                        "Environment folder name:", text=".venv")
        if not ok or not name.strip():
            return
        target = os.path.join(folder, name.strip())
        if os.path.exists(target):
            QMessageBox.warning(self, "Create Virtual Environment", f"{target} already exists.")
            return
        self.busy_label.setText("Creating virtual environment...")
        self._pip_busy(True)

        def done(interp):
            self._pip_busy(self.pip.busy)
            if not interp:
                QMessageBox.warning(self, "Create Virtual Environment", "The environment was created but its Python "
                                                                        "doesn't run.")
                return
            self.interpreters.insert(0, interp)
            self.set_interpreter(interp)
            self.statusBar().showMessage(f"Created {interp.label()}", 8000)
            reqs = os.path.join(self.project or folder, "requirements.txt")
            if os.path.isfile(reqs) and QMessageBox.question(
                    self, "Install Requirements", "Install the packages from requirements.txt into the new "
                                                  "environment?") == QMessageBox.StandardButton.Yes:
                self.pip.install_requirements(interp.path, reqs, lambda ok, _t: self._packages_changed())

        def failed(msg):
            self._pip_busy(self.pip.busy)
            QMessageBox.warning(self, "Create Virtual Environment", msg)

        run_async(interpreters.create_venv, base, target, on_done=done, on_error=failed)

    # ========================================================= smart packages
    def check_imports(self, page: EditorPage, force: bool = False, then=None) -> None:
        if sip.isdeleted(page):
            return
        e = page.editor
        if not self.settings.get("check_imports") or not self.interp:
            page.info.clear("imports")
            if then:
                then([])
            return
        code = e.text()
        refs = imports.extract_imports(code)
        modules = sorted({r.module for r in refs if not r.optional})
        dists = imports.inline_script_dependencies(code)
        lines = {r.module: r.line for r in refs}
        key = (self.interp.path, tuple(modules), tuple(dists), e.path)
        state = self._imports.get(id(page))
        if not force and state and state["key"] == key and "packages" in state:
            if then:
                then(state["packages"])
            return
        if not modules and not dists:
            self._imports[id(page)] = {"key": key, "packages": [], "stdlib": [], "lines": {}}
            page.info.clear("imports")
            if then:
                then([])
            return
        state = {"key": key, "lines": lines}
        self._imports[id(page)] = state
        search_paths = [p for p in (os.path.dirname(e.path) if e.path else None, self.project) if p]
        interp_path = self.interp.path
        ignored = set(self.settings.get("ignored_missing"))

        def work():
            res = interpreters.find_missing(interp_path, modules, dists, search_paths)
            missing = [m for m in res["missing"] if m not in ignored]
            pkgs = imports.resolve_missing(missing)
            known = {p.dist.lower() for p in pkgs}
            for d in res.get("dists_missing", []):
                if d.lower() not in known and d not in ignored:
                    pkgs += imports.resolve_missing([d])
            return {"packages": pkgs, "stdlib": res.get("stdlib", [])}

        def done(result):
            if sip.isdeleted(page) or self._imports.get(id(page)) is not state:
                return
            state.update(result)
            self._show_missing(page, state)
            self._request_lint(page)
            if then:
                then(result["packages"])

        def failed(msg):
            self.statusBar().showMessage(f"Couldn't check imports: {msg.splitlines()[-1]}", 8000)
            if then and not sip.isdeleted(page):
                then([])

        run_async(work, on_done=done, on_error=failed)

    def _show_missing(self, page: EditorPage, state: dict) -> None:
        pkgs = state.get("packages", [])
        notes = [imports.STDLIB_HINTS[m] for m in state.get("stdlib", []) if m in imports.STDLIB_HINTS]
        installable = [p for p in pkgs if p.exists is not False]
        unknown = [p for p in pkgs if p.exists is False]
        mode = self.settings.get("auto_install")
        if (not pkgs and not notes) or mode == "never":
            page.info.clear("imports")
            return
        label = self.interp.label() if self.interp else "this interpreter"
        parts = []
        if installable:
            names = []
            for p in installable:
                n = f"<b>{html.escape(p.dist)}</b>"
                if p.dist.lower().replace("_", "-") != p.module.split(".")[-1].lower().replace("_", "-"):
                    n += f" <span style='color:{self.theme['fg_dim']}'>(for {html.escape(p.module)})</span>"
                names.append(n)
            parts.append(f"Not installed in {html.escape(label)}: " + ", ".join(names))
        if unknown:
            parts.append("Not found on PyPI: " + ", ".join(f"<b>{html.escape(p.module)}</b>" for p in unknown))
        parts += [html.escape(n) for n in notes]
        dists = [p.dist for p in installable]
        if installable and mode == "always" and not state.get("auto_attempted"):
            state["auto_attempted"] = True
            page.info.show_message("info", f"Installing {html.escape(', '.join(dists))}...", tag="imports")
            self.install_packages(dists)
            return
        actions = []
        if installable:
            actions.append(("Install" if len(dists) == 1 else f"Install {len(dists)} Packages",
                            lambda: self.install_packages(dists), True))
        actions.append(("Ignore", lambda: self._ignore_missing(page, pkgs), False))
        page.info.show_message("warning", "<br>".join(parts), actions, tag="imports")

    def _ignore_missing(self, page: EditorPage, pkgs) -> None:
        ignored = self.settings.get("ignored_missing")
        self.settings.set("ignored_missing", sorted(set(ignored) | {p.module for p in pkgs}))
        page.info.clear("imports")
        self.check_imports(page, force=True)

    def ensure_installable(self, then, what: str) -> None:
        """Call ``then(interp)`` with an interpreter pip is allowed to install into.

        OS-managed Pythons (PEP 668: Debian/Ubuntu, Homebrew, ...) refuse pip installs, so
        for those offer a virtual environment, switch to it, and continue there.
        """
        if not self.interp:
            self.download_python()
            return
        if self.interp.externally_managed:
            self._offer_venv_for(self.interp, what, then)
        else:
            then(self.interp)

    def _offer_venv_for(self, base, what: str, then) -> None:
        if self.project:
            target, scope = os.path.join(self.project, ".venv"), "this project"
        else:
            target = str(data_dir() / "venvs" / f"py{base.short_version.replace('.', '')}")
            scope = "files outside a project"
        existing = interpreters.probe(str(interpreters.venv_python(target)))
        if existing:
            self._adopt_interpreter(existing)
            then(existing)
            return
        answer = QMessageBox.question(
            self, "Create Virtual Environment",
            f"{base.label()} is managed by your operating system, so pip isn't allowed to install {what} into "
            f"it.\n\nCreate a virtual environment at\n{target}\nand install there? Viper will use it for {scope} "
            f"from now on.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.busy_label.setText("Creating virtual environment...")
        self._pip_busy(True)
        self.statusBar().showMessage(f"Creating a virtual environment at {target}...")

        def done(interp):
            self._pip_busy(self.pip.busy)
            if not interp:
                QMessageBox.warning(self, "Create Virtual Environment",
                                    f"The environment at {target} was created but its Python doesn't run.")
                return
            self._adopt_interpreter(interp)
            then(interp)

        def failed(msg):
            self._pip_busy(self.pip.busy)
            QMessageBox.warning(self, "Create Virtual Environment", f"Couldn't create the environment:\n\n{msg}")

        run_async(interpreters.create_venv, base, target, on_done=done, on_error=failed)

    def _adopt_interpreter(self, interp) -> None:
        if not any(norm(i.path) == norm(interp.path) for i in self.interpreters):
            self.interpreters.insert(0, interp)
        self.set_interpreter(interp)
        self.statusBar().showMessage(f"Now using {interp.label()}", 6000)

    def install_packages(self, dists: list[str], then=None) -> None:
        self.ensure_installable(lambda interp: self._pip_install(interp, dists, then), ", ".join(dists))

    def _pip_install(self, interp, dists: list[str], then=None) -> None:
        self.statusBar().showMessage(f"Installing {', '.join(dists)} into {interp.label()}...")

        def done(ok: bool, text: str):
            if not ok and "externally-managed-environment" in text and not interp.is_venv:
                # The probe missed the marker (unusual layout): learn it and take the venv route.
                interp.externally_managed = True
                self._offer_venv_for(interp, ", ".join(dists), lambda i: self._pip_install(i, dists, then))
                return
            if ok:
                self.statusBar().showMessage(f"Installed {', '.join(dists)}", 6000)
                self._packages_changed()
            else:
                last = next((ln for ln in reversed(text.splitlines()) if ln.strip()), "pip failed")
                page = self.page()
                if page:
                    page.info.show_message("error", f"Couldn't install {html.escape(', '.join(dists))}: "
                                                    f"{html.escape(last)}",
                                           [("Show Log", lambda: self._show_dock(self.packages_dock), True)],
                                           tag="imports")
            if then:
                then(ok)

        self.pip.install(interp.path, dists, done)

    def _packages_changed(self) -> None:
        self.intel.invalidate()
        self.packages_panel.refresh()
        for p in self.pages():
            self.check_imports(p, force=True)
        self.check_project_requirements()

    def check_missing_now(self) -> None:
        page = self.page()
        if page:
            state = self._imports.pop(id(page), None)
            if state:
                state.pop("auto_attempted", None)
            self.check_imports(page, force=True, then=lambda pkgs: self.statusBar().showMessage(
                "All imports are installed." if not pkgs else f"{len(pkgs)} missing package(s).", 5000))

    def install_missing_current(self) -> None:
        page = self.page()
        if not page:
            return

        def go(pkgs):
            dists = [p.dist for p in pkgs if p.exists is not False]
            if dists:
                self.install_packages(dists)
            else:
                self.statusBar().showMessage("Nothing to install: all imports are available.", 5000)

        self.check_imports(page, then=go)

    def check_project_requirements(self) -> None:
        if not (self.project and self.interp and self.settings.get("check_imports")):
            return
        names, source = [], None
        req = os.path.join(self.project, "requirements.txt")
        pyproject = os.path.join(self.project, "pyproject.toml")
        try:
            if os.path.isfile(req):
                with open(req, encoding="utf-8", errors="replace") as f:
                    names, source = imports.parse_requirements(f.read()), req
            elif os.path.isfile(pyproject):
                import tomllib

                with open(pyproject, "rb") as f:
                    deps = tomllib.load(f).get("project", {}).get("dependencies", [])
                names = [n for n in (imports.requirement_name(d) for d in deps) if n]
                source = pyproject
        except (OSError, ValueError):
            return
        if not names:
            self.global_bar.clear("requirements")
            return
        interp, project = self.interp, self.project

        def done(res):
            if interp is not self.interp or project != self.project:
                return
            missing = res.get("dists_missing", [])
            if not missing:
                self.global_bar.clear("requirements")
                return
            fname = os.path.basename(source)
            install = (lambda: self.ensure_installable(
                lambda i: self.pip.install_requirements(i.path, source, lambda ok, _t: self._packages_changed()),
                fname)) if source.endswith(".txt") else (lambda: self.install_packages(missing))
            self.global_bar.show_message(
                "info", f"{len(missing)} of this project's requirements ({html.escape(fname)}) aren't installed in "
                        f"{html.escape(interp.label())}: <b>{html.escape(', '.join(missing[:8]))}</b>"
                        + (" ..." if len(missing) > 8 else ""),
                [("Install", install, True), ("Dismiss", lambda: self.global_bar.clear("requirements"), False)],
                tag="requirements")

        run_async(interpreters.find_missing, interp.path, [], names, [project], on_done=done)

    # ================================================================== intel
    def intel_request(self, editor, kind: str, **kw) -> int:
        line, col = editor.getCursorPosition()
        docs = kw.pop("docs", False)
        req = {"code": editor.text(), "path": editor.path, "line": kw.pop("line", line + 1),
               "col": kw.pop("col", col), "interpreter": self.interp.path if self.interp else "",
               "project": self.project or (os.path.dirname(editor.path) if editor.path else None)}
        req.update(kw)
        rid = self.intel.request(kind, **req)
        self._routes[rid] = (editor, kind, docs)
        if len(self._routes) > 400:
            for old in sorted(self._routes)[:200]:
                self._routes.pop(old, None)
        return rid

    def _on_intel(self, res: dict) -> None:
        route = self._routes.pop(res["id"], None)
        if not route:
            return
        editor, kind, docs = route
        if sip.isdeleted(editor):
            return
        if "error" in res:
            log.debug("intel %s failed: %s", kind, res["error"])
            if kind in ("goto", "references", "rename"):
                self.statusBar().showMessage(f"{kind.title()} failed: {res['error']}", 8000)
            return
        payload, req = res.get("payload"), res["req"]
        if kind == "complete":
            editor.show_completions(payload or [], req)
        elif kind == "signature":
            editor.show_signature(payload, req)
        elif kind == "hover":
            editor.show_docs_at_cursor(payload) if docs else editor.show_hover(payload, req)
        elif kind == "goto":
            self._goto_result(payload or [])
        elif kind == "references":
            rows = [(r["path"], r["line"], r["column"], r["code"]) for r in payload or [] if r["path"]]
            self.search.show_results("References", rows)
            self._show_dock(self.search_dock)
        elif kind == "rename":
            self._apply_rename(payload)
        elif kind == "lint":
            self._apply_lint(editor, payload or [])

    def _request_lint(self, page: EditorPage) -> None:
        e = page.editor
        if self.settings.get("lint"):
            self.intel_request(e, "lint", path=e.path or e.untitled_name)
        else:
            self._apply_lint(e, [])

    def _apply_lint(self, editor, items: list[dict]) -> None:
        page = editor.parent()
        state = self._imports.get(id(page), {})
        for p in state.get("packages", []):
            line = state.get("lines", {}).get(p.module)
            if line:
                msg = (f"'{p.module}' is not installed (pip install {p.dist})" if p.exists is not False
                       else f"'{p.module}' is not installed and wasn't found on PyPI")
                items = items + [{"line": line, "col": 0, "message": msg, "severity": "warning", "whole_line": True}]
        editor.set_lint(items)
        self.problems.set_problems(editor.path or editor.untitled_name, editor.display_name(), items)

    def _editor_idle(self, editor) -> None:
        page = editor.parent()
        if not isinstance(page, EditorPage):
            return
        self._request_lint(page)
        if page is self.page():
            self.outline.set_outline(intel.outline(editor.text()))
        self.check_imports(page)

    def _tab_changed(self, _index: int) -> None:
        e = self.editor()
        if e:
            self.outline.set_outline(intel.outline(e.text()) or [])
        self._update_cursor_label()
        self._update_title()

    def _update_cursor_label(self) -> None:
        e = self.editor()
        if not e:
            self.cursor_label.setText("")
            self.eol_button.setText("")
            self.encoding_label.setText("")
            return
        line, col = e.getCursorPosition()
        sel = len(e.selectedText()) if e.hasSelectedText() else 0
        self.cursor_label.setText(f"Ln {line + 1}, Col {col + 1}" + (f" ({sel} selected)" if sel else ""))
        self.eol_button.setText(e.eol_name())
        self.encoding_label.setText(e.encoding.upper().replace("CP", "Windows-") + (" BOM" if e.bom else ""))
        if self.assistant_dock.isVisible():
            self.assistant.update_context()

    def _toggle_eol(self) -> None:
        e = self.editor()
        if e:
            e.set_eol("LF" if e.eol_name() == "CRLF" else "CRLF")
            self._update_cursor_label()

    def goto_definition(self, editor=None) -> None:
        editor = editor or self.editor()
        if editor:
            self.intel_request(editor, "goto")

    def _goto_result(self, items: list[dict]) -> None:
        located = [i for i in items if i["path"]]
        if not items:
            self.statusBar().showMessage("No definition found.", 4000)
            return
        if not located:
            self.statusBar().showMessage(f"'{items[0]['name']}' is built into Python (no source to show).", 5000)
            return
        unique = {(i["path"], i["line"]) for i in located}
        if len(unique) == 1:
            i = located[0]
            self.open_file(i["path"], i["line"] or 1, i["column"] or 0)
        else:
            self.search.show_results("Definitions", [(i["path"], i["line"] or 1, i["column"] or 0,
                                                      i["description"]) for i in located])
            self._show_dock(self.search_dock)

    def rename_symbol(self, editor) -> None:
        line, col = editor.getCursorPosition()
        current = editor.wordAtLineIndex(line, col)
        if not current:
            return
        name, ok = QInputDialog.getText(self, "Rename Symbol", f"Rename '{current}' to:", text=current)
        if ok and name.strip() and name.strip() != current:
            self.save_all()
            self.intel_request(editor, "rename", new_name=name.strip())

    def _apply_rename(self, payload: dict) -> None:
        changed = payload.get("changed", {})
        if not changed:
            self.statusBar().showMessage("Nothing to rename.", 4000)
            return
        if not RenamePreviewDialog(self, payload.get("diff", ""), len(changed)).exec():
            return
        for path, code in changed.items():
            page = self._find_page(path)
            if page:
                page.editor.set_text_undoable(code)
            else:
                try:
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(code)
                except OSError as e:
                    QMessageBox.warning(self, "Rename", f"Couldn't write {path}: {e}")
        self.statusBar().showMessage(f"Renamed in {len(changed)} file(s).", 6000)

    def format_document(self) -> None:
        e = self.editor()
        if not e:
            return
        if not self.interp:
            self.download_python()
            return
        tool, interp = self.settings.get("formatter"), self.interp
        if not packages.tool_installed(tool, interp.version):
            answer = QMessageBox.question(
                self, "Format Document",
                f"The {tool} formatter isn't downloaded yet for Python {interp.short_version}.\n\n"
                f"Download it now? It goes into Viper's own tools folder, so your projects' environments are "
                f"not changed.")
            if answer == QMessageBox.StandardButton.Yes:
                self.pip.run(interp.path, packages.install_tool_args(tool, interp.version), f"Downloading {tool}",
                             lambda ok, _t: ok and self.format_document())
            return
        code = e.text()
        path = e.path or os.path.join(self.project or os.path.expanduser("~"), e.untitled_name)

        def done(new):
            if sip.isdeleted(e) or e.text() != code:
                return
            if new != code:
                e.set_text_undoable(new)
            self.statusBar().showMessage(f"Formatted with {tool}.", 3000)

        run_async(packages.run_formatter, tool, interp.path, interp.version, code, path, on_done=done,
                  on_error=lambda msg: QMessageBox.warning(self, "Format Document", msg))

    # ============================================================ run & debug
    def _run_path(self, path: str) -> None:
        if self.open_file(path):
            self.run_file(False)

    def run_file(self, debug: bool = False, ask_args: bool = False) -> None:
        if self.debug.active and self.debug.paused and not debug:
            self.debug.command("continue")
            return
        page = self.page()
        if page is None:
            return
        e = page.editor
        if not e.path or (e.isModified() and self.settings.get("save_before_run")):
            if not self.save(page):
                return
        if not self.interp:
            self.global_bar.show_message("warning", "Choose a Python interpreter to run this file.",
                                         [("Download Python", self.download_python, True),
                                          ("Browse...", self.browse_interpreter, False)], tag="nopython")
            return
        args: list[str] = []
        if ask_args:
            session_args = self.settings.get("session").get("args", {})
            text, ok = QInputDialog.getText(self, "Run with Arguments", "Command-line arguments:",
                                            text=session_args.get(norm(e.path), ""))
            if not ok:
                return
            session = dict(self.settings.get("session"))
            session["args"] = dict(session_args, **{norm(e.path): text})
            self.settings.set("session", session)
            args = QProcess.splitCommand(text)
        if self.settings.get("check_imports") and self.settings.get("auto_install") != "never":
            self.statusBar().showMessage("Checking imports...", 2000)
            self.check_imports(page, then=lambda pkgs: self._run_after_check(page, debug, args, pkgs))
        else:
            self._launch(page, debug, args)

    def _run_after_check(self, page: EditorPage, debug: bool, args: list[str], pkgs) -> None:
        if sip.isdeleted(page):
            return
        installable = [p for p in pkgs if p.exists is not False]
        if installable:
            dists = [p.dist for p in installable]
            if self.settings.get("auto_install") == "always":
                self.install_packages(dists, then=lambda ok: self._launch(page, debug, args))
                return
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Missing Packages")
            box.setText(f"This file imports packages that aren't installed in {self.interp.label()}:")
            box.setInformativeText("\n".join(f"• {p.dist} {p.version}" + (f" - {p.summary}" if p.summary else "")
                                             for p in installable))
            install = box.addButton("Install and Run", QMessageBox.ButtonRole.AcceptRole)
            anyway = box.addButton("Run Anyway", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(install)
            box.exec()
            if box.clickedButton() is install:
                self.install_packages(dists, then=lambda ok: self._launch(page, debug, args))
                return
            if box.clickedButton() is not anyway:
                return
        self._launch(page, debug, args)

    def _launch(self, page: EditorPage, debug: bool, args: list[str]) -> None:
        if sip.isdeleted(page) or not page.editor.path or not self.interp:
            return
        path = page.editor.path
        cwd = self.project if self.settings.get("run_cwd") == "project" and self.project else os.path.dirname(path)
        env = activated_env(self.interp)
        self._last_run = (path, debug, args)
        self._show_dock(self.run_dock)
        self._clear_debug_lines()
        if debug:
            bps = self._all_breakpoints()
            if not self.debug.start(self.run_panel, self.interp.path, path, args, cwd, env, bps,
                                    bool(self.settings.get("just_my_code"))):
                QMessageBox.warning(self, "Debug", "Couldn't open a local port for the debugger.")
                return
            self.debug_dock.show()
            self.debug_panel.clear()
            self.debug_panel.status.setText("Starting...")
        else:
            self.run_panel.start(self.interp.path, ["-u", path] + args, cwd, env, f"Run {os.path.basename(path)}")
        self._update_debug_actions()

    def stop_running(self) -> None:
        if self.debug.active:
            self.debug.terminate()
        self.run_panel.stop()

    def _run_finished(self, code: int, tail: str) -> None:
        if self.debug.active:
            self.debug.finish()
            self.debug_panel.clear()
            self._clear_debug_lines()
        self._update_debug_actions()
        if code == 0 or not self.interp or self.settings.get("auto_install") == "never":
            return
        module = imports.missing_module_from_output(tail)
        if not module or not self._last_run:
            return
        key = (self.interp.path, module)
        if key in self._runtime_attempted:
            return  # installed it once already and the import still fails: don't loop
        run_async(imports.resolve_missing, [module], on_done=lambda pkgs: self._offer_runtime_install(pkgs, key))

    def _offer_runtime_install(self, pkgs, key) -> None:
        if not pkgs or not self.interp or key[0] != self.interp.path:
            return
        pkg = pkgs[0]
        if pkg.exists is False:
            self.run_panel.view.append_text(f"\n'{pkg.module}' isn't installed, and no package with that name exists "
                                            f"on PyPI. It may be a local module or have a different package name.\n",
                                            "info")
            return
        if self.settings.get("auto_install") != "always":
            answer = QMessageBox.question(
                self, "Missing Package",
                f"The program stopped because '{pkg.module}' isn't installed in {self.interp.label()}.\n\n"
                f"Install {pkg.dist} {pkg.version}" + (f" ({pkg.summary})" if pkg.summary else "") + " and run again?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._runtime_attempted.add(key)
        self.install_packages([pkg.dist], then=lambda ok: ok and self._rerun_last())

    def _rerun_last(self) -> None:
        if not self._last_run:
            return
        path, debug, args = self._last_run
        page = self.open_file(path)
        if page:
            self._launch(page, debug, args)

    def _update_debug_actions(self) -> None:
        a = self.a
        active, paused = self.debug.active, self.debug.paused
        running = self.run_panel.running() or active
        for name in ("continue", "step_over", "step_into", "step_out"):
            a[name].setEnabled(paused)
        a["pause"].setEnabled(active and not paused)
        a["stop"].setEnabled(running)

    def _debug_stopped(self, ev: dict) -> None:
        frames = ev.get("frames") or []
        if frames:
            self._show_debug_location(frames[0]["file"], frames[0]["line"])
        self._show_dock(self.debug_dock)
        self.raise_()
        self.activateWindow()

    def _show_debug_location(self, path: str, line: int) -> None:
        self._clear_debug_lines()
        page = self.open_file(path)
        if page:
            page.editor.set_debug_line(line)

    def _clear_debug_lines(self) -> None:
        for p in self.pages():
            p.editor.set_debug_line(None)

    def _breakpoints_changed(self, editor) -> None:
        if editor.path:
            self._persist_breakpoints(editor)
            self.debug.set_breakpoints(editor.path, editor.breakpoints())

    def _persist_breakpoints(self, editor) -> None:
        bps = dict(self.settings.get("breakpoints"))
        items = editor.breakpoints()
        if items:
            bps[norm(editor.path)] = items
        else:
            bps.pop(norm(editor.path), None)
        self.settings.set("breakpoints", bps)

    def _all_breakpoints(self) -> dict[str, list[dict]]:
        out = {k: v for k, v in self.settings.get("breakpoints").items() if os.path.isfile(k)}
        for p in self.pages():
            if p.editor.path:
                out[norm(p.editor.path)] = p.editor.breakpoints()
        return {k: v for k, v in out.items() if v}

    def clear_all_breakpoints(self) -> None:
        self.settings.set("breakpoints", {})
        for p in self.pages():
            p.editor.set_breakpoints([])
            if p.editor.path:
                self.debug.set_breakpoints(p.editor.path, [])

    def _console_run(self, code: str) -> None:
        self._show_dock(self.console_dock)
        self.console.run_code(code)

    def _terminal_at(self, folder: str) -> None:
        self._show_dock(self.terminal_dock)
        self.terminal.start(folder)

    # ================================================================ commands
    def find_in_files(self) -> None:
        e = self.editor()
        text = e.selectedText() if e and e.hasSelectedText() and "\n" not in e.selectedText() else ""
        self._show_dock(self.search_dock)
        self.search.focus_query(text)

    def goto_line_dialog(self) -> None:
        e = self.editor()
        if not e:
            return
        line, ok = QInputDialog.getInt(self, "Go to Line", f"Line (1 - {e.lines()}):", e.getCursorPosition()[0] + 1,
                                       1, e.lines())
        if ok:
            e.goto_line(line)

    def command_palette(self) -> None:
        entries = []
        for name, act in self.a.items():
            if act.isEnabled():
                entries.append((act.text().replace("&", ""), act.shortcut().toString(), act.trigger))
        for dock in (self.explorer_dock, self.outline_dock, self.search_dock, self.run_dock, self.console_dock,
                     self.terminal_dock, self.problems_dock, self.packages_dock, self.debug_dock, self.assistant_dock):
            entries.append((f"Show {dock.windowTitle()}", "", lambda d=dock: self._show_dock(d)))
        CommandPalette(self, sorted(entries, key=lambda e: e[0]), "Type a command").exec()

    def quick_open(self) -> None:
        if not self.project:
            self.open_file_dialog()
            return
        if self._project_files is None:
            self._project_files = list_project_files(self.project)
        root = self.project
        entries = [(os.path.relpath(p, root), "", lambda p=p: self.open_file(p)) for p in self._project_files]
        CommandPalette(self, entries, "Go to file").exec()

    def _zoom_by(self, step: int | None) -> None:
        self._zoom = 0 if step is None else max(-8, min(20, self._zoom + step))
        for p in self.pages():
            p.editor.zoomTo(self._zoom)

    def _toggle_setting(self, key: str, on: bool) -> None:
        self.settings.set(key, bool(on))
        self._apply_editor_settings()

    def _apply_editor_settings(self) -> None:
        for p in self.pages():
            p.editor.apply_settings(self.settings, self.theme)

    def set_theme(self, name: str) -> None:
        self.settings.set("theme", name)
        self.theme = theme(name)
        apply_app_theme(QApplication.instance(), self.theme)
        for view in (self.run_panel.view, self.console.view, self.terminal.view, self.debug_panel.console,
                     self.packages_panel.log):
            view.set_theme(self.theme)
        self._apply_editor_settings()

    def open_settings(self) -> None:
        old_theme = self.settings.get("theme")
        old_ai = assistant.active_provider(self.settings)
        dialog = SettingsDialog(self.settings, self)
        accepted = dialog.exec()
        self.assistant.providers_changed()  # Manage AI Providers saves even if Settings is cancelled
        if assistant.active_provider(self.settings) != old_ai:
            self.assistant.load_models()
        if accepted:
            if self.settings.get("theme") != old_theme:
                self.set_theme(self.settings.get("theme"))
            self._apply_editor_settings()
            self.a["wrap"].setChecked(bool(self.settings.get("word_wrap")))
            self.a["whitespace"].setChecked(bool(self.settings.get("show_whitespace")))
            for p in self.pages():
                self._request_lint(p)
                self.check_imports(p, force=True)

    # ============================================================== assistant
    def show_assistant(self, with_file: bool = False) -> None:
        self._show_dock(self.assistant_dock)
        self.assistant.ask(with_file=with_file)

    def _fill_ai_provider_menu(self) -> None:
        from .providersui import fill_provider_menu

        fill_provider_menu(self.ai_provider_menu, self.settings, self.assistant.switch_provider,
                           self.assistant.manage_providers)

    def _assistant_visible(self, visible: bool) -> None:
        if visible:
            self.assistant.update_context()
            if not self._assistant_models_loaded and assistant.active_provider(self.settings):
                self._assistant_models_loaded = True
                self.assistant.load_models()

    # ================================================================ updates
    def check_for_updates(self, silent: bool = False) -> None:
        if not silent:
            self.statusBar().showMessage("Checking for updates...", 5000)
        run_async(updater.check, self.settings, on_done=lambda result: self._update_checked(result, silent),
                  on_error=lambda msg: silent or QMessageBox.warning(self, "Check for Updates", msg))

    def _update_checked(self, result, silent: bool) -> None:
        release, errors = result
        if release is None:
            if not silent:
                QMessageBox.information(self, "Check for Updates", "Couldn't reach an update server:\n\n"
                                        + "\n".join(errors[-3:]))
            return
        if not release.newer_than(__version__):
            if not silent:
                QMessageBox.information(self, "Check for Updates",
                                        f"You're on the latest version ({__version__}).")
            return
        if silent and self.settings.get("skipped_update") == release.version:
            return
        if not silent:
            self._open_update(release)
            return
        if self.global_bar.isVisible() and self.global_bar.tag != "update":
            self.statusBar().showMessage(f"Viper IDE {release.version} is available: Help > Check for Updates", 20000)
            return
        self.global_bar.show_message(
            "info", f"<b>Viper IDE {html.escape(release.version)}</b> is available (you have {__version__}).",
            [("Update Now", lambda: self._open_update(release), True),
             ("Later", lambda: self.global_bar.clear("update"), False)], tag="update")

    def _open_update(self, release) -> None:
        result = UpdateDialog(release, self.settings, self).exec()
        if result in (UpdateDialog.INSTALLING, UpdateDialog.SKIPPED):
            self.global_bar.clear("update")
        if result == UpdateDialog.INSTALLING:
            self.close()  # saves the session and prompts for unsaved files; the installer relaunches us

    def about(self) -> None:
        interp = self.interp.label() if self.interp else "none selected"
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {__version__}</h3>"
                          f"<p>A Python IDE that finds and installs the packages your code needs.</p>"
                          f"<p>Interpreter: {html.escape(interp)}<br>Settings: {html.escape(str(self.settings.path))}"
                          f"</p><p>Copyright &copy; 2026 {ORG_NAME}</p>")

    # ================================================================ session
    def _restore(self, open_paths: list[str]) -> None:
        if open_paths:
            for p in open_paths:
                if os.path.isdir(p):
                    self.set_project(p)
            for p in open_paths:
                if os.path.isfile(p):
                    self.open_file(p)
            return
        session = self.settings.get("session")
        folder = session.get("folder")
        if folder and os.path.isdir(folder):
            self.set_project(folder)
        for f in session.get("files", []):
            if os.path.isfile(f.get("path", "")):
                page = self.open_file(f["path"])
                if page:
                    page.editor.goto_line(f.get("line", 1), f.get("col", 0))
        current = session.get("current")
        if current:
            page = self._find_page(current)
            if page:
                self.tabs.setCurrentWidget(page)

    def closeEvent(self, e):
        dirty = [p for p in self.pages() if p.editor.isModified()]
        if dirty:
            names = "\n".join(p.editor.display_name() for p in dirty)
            answer = QMessageBox.question(self, "Unsaved Changes", f"Save changes before closing?\n\n{names}",
                                          QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                          | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                e.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                for p in dirty:
                    if not self.save(p):
                        e.ignore()
                        return
        session = dict(self.settings.get("session"))
        session["folder"] = self.project
        session["files"] = []
        for p in self.pages():
            if p.editor.path:
                line, col = p.editor.cursor_line_col()
                session["files"].append({"path": p.editor.path, "line": line, "col": col})
                self._persist_breakpoints(p.editor)
        cur = self.editor()
        session["current"] = cur.path if cur else None
        self.settings._data["session"] = session
        self.settings._data["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.settings._data["window_state"] = bytes(self.saveState().toBase64()).decode()
        self.settings.save()
        self.stop_running()
        self.assistant.stop()
        self.console.stop()
        self.terminal.stop()
        self.pip.cancel()
        self.intel.stop()
        e.accept()


def _install_excepthook() -> None:
    log_path = data_dir() / "viper.log"
    logging.basicConfig(filename=str(log_path), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Unhandled exception:\n%s", text)
        if sys.stderr:
            sys.stderr.write(text)
        app = QApplication.instance()
        win = next((w for w in app.topLevelWidgets() if isinstance(w, MainWindow)), None) if app else None
        if win:
            win.statusBar().showMessage(f"Internal error: {exc_type.__name__}: {exc} (details in {log_path})", 15000)

    sys.excepthook = hook  # PyQt6 aborts the process on unhandled slot exceptions without a hook


def main(argv: list[str]) -> int:
    if IS_WIN:
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HillyardTech.ViperIDE")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(app_icon_pixmap()))
    _install_excepthook()
    settings = Settings()
    apply_app_theme(app, theme(settings.get("theme")))
    paths = [a for a in argv[1:] if not a.startswith("-") and os.path.exists(a)]
    win = MainWindow(settings, paths)
    win.show()
    return app.exec()
