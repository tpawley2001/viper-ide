"""Colour themes for the Qt chrome and the code editor."""
from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication

DARK = {
    "name": "dark",
    "bg": "#1e1f22", "panel": "#2b2d30", "panel_alt": "#232427", "border": "#393b40",
    "fg": "#dfe1e5", "fg_dim": "#8c8f96", "accent": "#3574f0", "accent_fg": "#ffffff",
    "hover": "#393b40", "select": "#214283", "caret_line": "#26282e", "caret": "#ced0d6",
    "margin_bg": "#1e1f22", "margin_fg": "#5f6268", "guide": "#35373b", "edge": "#303236",
    "brace": "#43454a", "error": "#f75464", "warning": "#e0b050", "info": "#56a8f5",
    "debug_line": "#3b3520", "breakpoint": "#e55765", "occurrence": "#373b47",
    "ok": "#5fb865",
    "syntax": {
        "default": "#bcbec4", "comment": "#7a7e85", "number": "#2aacb8",
        "string": "#6aab73", "keyword": "#cf8e6d", "class": "#e6c07b",
        "function": "#56a8f5", "operator": "#bcbec4", "identifier": "#bcbec4",
        "builtin": "#8888c6", "decorator": "#b3ae60", "unclosed": "#f75464",
        "docstring": "#5f826b",
    },
}

LIGHT = {
    "name": "light",
    "bg": "#ffffff", "panel": "#f2f3f5", "panel_alt": "#f7f8fa", "border": "#d9dbe0",
    "fg": "#1f2329", "fg_dim": "#6c707e", "accent": "#3574f0", "accent_fg": "#ffffff",
    "hover": "#e3e5e8", "select": "#c9dcfa", "caret_line": "#f5f8fe", "caret": "#000000",
    "margin_bg": "#ffffff", "margin_fg": "#aeb3c2", "guide": "#e8e9ec", "edge": "#eeeff2",
    "brace": "#d6e4f7", "error": "#d9363e", "warning": "#b8860b", "info": "#3574f0",
    "debug_line": "#fff4c2", "breakpoint": "#e55765", "occurrence": "#e8eefa",
    "ok": "#2e8b3a",
    "syntax": {
        "default": "#080808", "comment": "#8c8c8c", "number": "#1750eb",
        "string": "#067d17", "keyword": "#0033b3", "class": "#000000",
        "function": "#00627a", "operator": "#080808", "identifier": "#080808",
        "builtin": "#871094", "decorator": "#9e880d", "unclosed": "#d9363e",
        "docstring": "#8c8c8c",
    },
}

THEMES = {"dark": DARK, "light": LIGHT}

MONO_CANDIDATES = [
    "Cascadia Mono", "Consolas", "JetBrains Mono", "Fira Code", "Source Code Pro",
    "DejaVu Sans Mono", "Liberation Mono", "Menlo", "Courier New",
]


def theme(name: str) -> dict:
    return THEMES.get(name, DARK)


def mono_font(family: str = "", size: int = 11) -> QFont:
    families = set(QFontDatabase.families())
    chosen = family if family in families else next(
        (f for f in MONO_CANDIDATES if f in families), "")
    font = QFont(chosen) if chosen else QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    return font


def apply_app_theme(app: QApplication, t: dict) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    c = QColor
    roles = {
        QPalette.ColorRole.Window: t["panel"], QPalette.ColorRole.WindowText: t["fg"],
        QPalette.ColorRole.Base: t["bg"], QPalette.ColorRole.AlternateBase: t["panel_alt"],
        QPalette.ColorRole.Text: t["fg"], QPalette.ColorRole.Button: t["panel"],
        QPalette.ColorRole.ButtonText: t["fg"], QPalette.ColorRole.Highlight: t["accent"],
        QPalette.ColorRole.HighlightedText: t["accent_fg"], QPalette.ColorRole.ToolTipBase: t["panel"],
        QPalette.ColorRole.ToolTipText: t["fg"], QPalette.ColorRole.PlaceholderText: t["fg_dim"],
        QPalette.ColorRole.Link: t["info"],
    }
    for role, colour in roles.items():
        pal.setColor(role, c(colour))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, c(t["fg_dim"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, c(t["fg_dim"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(t) + _close_button_css(t))


def _close_button_css(t: dict) -> str:
    """Qt style sheets only take image files, so render the tab close glyph to disk once."""
    from .icons import close_pixmap
    from .paths import data_dir

    folder = data_dir() / "ui"
    folder.mkdir(exist_ok=True)
    path = folder / f"close-{t['name']}.png"
    if not path.is_file():
        close_pixmap(t["fg_dim"], 32).save(str(path))
    url = path.as_posix()
    return f"""
    QTabBar::close-button {{ image: url("{url}"); subcontrol-position: right; margin: 2px; border-radius: 3px; }}
    QTabBar::close-button:hover {{ background: {t['hover']}; }}
    """


def stylesheet(t: dict) -> str:
    return f"""
    QMainWindow, QDialog {{ background: {t['panel']}; }}
    QWidget {{ color: {t['fg']}; }}
    QToolTip {{ background: {t['panel']}; color: {t['fg']}; border: 1px solid {t['border']}; padding: 6px; }}
    QMenuBar {{ background: {t['panel']}; border-bottom: 1px solid {t['border']}; }}
    QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
    QMenuBar::item:selected {{ background: {t['hover']}; border-radius: 4px; }}
    QMenu {{ background: {t['panel']}; border: 1px solid {t['border']}; padding: 4px; }}
    QMenu::item {{ padding: 5px 28px 5px 20px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {t['accent']}; color: {t['accent_fg']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 8px; }}
    QToolBar {{ background: {t['panel']}; border: none; border-bottom: 1px solid {t['border']}; spacing: 2px; padding: 3px; }}
    QToolButton {{ padding: 4px 8px; border-radius: 5px; background: transparent; }}
    QToolButton:hover {{ background: {t['hover']}; }}
    QToolButton:pressed, QToolButton:checked {{ background: {t['border']}; }}
    QStatusBar {{ background: {t['panel']}; border-top: 1px solid {t['border']}; }}
    QStatusBar QToolButton, QStatusBar QLabel {{ padding: 1px 8px; }}
    QDockWidget {{ titlebar-close-icon: none; }}
    QDockWidget::title {{ background: {t['panel']}; padding: 5px 8px; border-bottom: 1px solid {t['border']}; }}
    QTabWidget::pane {{ border: none; border-top: 1px solid {t['border']}; }}
    QTabBar::tab {{ background: {t['panel']}; color: {t['fg_dim']}; padding: 6px 12px; border: none;
                    border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {t['fg']}; border-bottom: 2px solid {t['accent']}; background: {t['bg']}; }}
    QTabBar::tab:hover:!selected {{ background: {t['hover']}; }}
    QTreeView, QListView, QListWidget, QTreeWidget, QTableView, QTableWidget {{
        background: {t['panel_alt']}; border: none; outline: 0; alternate-background-color: {t['panel']}; }}
    QTreeView::item, QListView::item {{ padding: 2px 0; }}
    QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
        background: {t['select']}; color: {t['fg']}; }}
    QTreeView::item:hover:!selected, QListView::item:hover:!selected {{ background: {t['hover']}; }}
    QHeaderView::section {{ background: {t['panel']}; border: none; border-bottom: 1px solid {t['border']}; padding: 4px 6px; }}
    QPlainTextEdit, QTextEdit, QTextBrowser {{ background: {t['bg']}; border: none; selection-background-color: {t['select']}; }}
    QLineEdit, QComboBox, QSpinBox {{ background: {t['bg']}; border: 1px solid {t['border']}; border-radius: 5px; padding: 4px 6px;
                           selection-background-color: {t['accent']}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {t['accent']}; }}
    QComboBox QAbstractItemView {{ background: {t['panel']}; border: 1px solid {t['border']}; selection-background-color: {t['accent']}; }}
    QPushButton {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 5px; padding: 5px 14px; }}
    QPushButton:hover {{ background: {t['hover']}; }}
    QPushButton:default, QPushButton[primary="true"] {{ background: {t['accent']}; color: {t['accent_fg']}; border-color: {t['accent']}; }}
    QPushButton:disabled {{ color: {t['fg_dim']}; }}
    QCheckBox, QRadioButton {{ spacing: 6px; }}
    QProgressBar {{ border: 1px solid {t['border']}; border-radius: 4px; text-align: center; background: {t['bg']}; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
    QSplitter::handle {{ background: {t['border']}; }}
    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
    QScrollBar::handle {{ background: {t['border']}; border-radius: 4px; min-height: 24px; min-width: 24px; margin: 2px; }}
    QScrollBar::handle:hover {{ background: {t['fg_dim']}; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; width: 0; }}
    #InfoBar {{ background: {t['panel']}; border-bottom: 1px solid {t['border']}; }}
    #InfoBar[level="warning"] {{ border-left: 3px solid {t['warning']}; }}
    #InfoBar[level="error"] {{ border-left: 3px solid {t['error']}; }}
    #InfoBar[level="info"] {{ border-left: 3px solid {t['info']}; }}
    #FindBar {{ background: {t['panel']}; border-bottom: 1px solid {t['border']}; }}
    #Palette {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 8px; }}
    #Dim {{ color: {t['fg_dim']}; }}
    """
