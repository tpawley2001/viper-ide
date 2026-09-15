"""The code editor (QScintilla), its find bar, and the per-tab page that holds them."""
from __future__ import annotations

import builtins
import html
import os
import re
from pathlib import Path

from PyQt6.Qsci import QsciLexerPython, QsciScintilla
from PyQt6.Qsci import QsciScintillaBase as SB
from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QToolButton,
                             QToolTip, QVBoxLayout, QWidget)

from .icons import kind_pixmap
from .theme import mono_font
from .widgets import InfoBar

BUILTINS = " ".join(sorted(n for n in dir(builtins) if not n.startswith("_"))) + " self cls"

MARK_BP, MARK_BP_COND, MARK_ARROW, MARK_DEBUG_BG, MARK_ERR, MARK_WARN = 1, 2, 3, 4, 5, 6
BP_MASK = (1 << MARK_BP) | (1 << MARK_BP_COND)
IND_ERR, IND_WARN, IND_OCC = 8, 9, 10
KIND_IMAGES = {"module": 1, "class": 2, "instance": 3, "function": 4, "param": 5, "path": 6,
               "keyword": 7, "property": 8, "statement": 9}
PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}

L = QsciLexerPython
NON_CODE_STYLES = {L.Comment, L.CommentBlock, L.DoubleQuotedString, L.SingleQuotedString,
                   L.TripleSingleQuotedString, L.TripleDoubleQuotedString, L.UnclosedString,
                   L.DoubleQuotedFString, L.SingleQuotedFString, L.TripleSingleQuotedFString,
                   L.TripleDoubleQuotedFString}

SCI_AUTOCSETORDER = 2660
SCI_TARGETWHOLEDOCUMENT = 2690
SCI_SETSEARCHFLAGS = 2198
SCI_GETSELECTIONS = 2570
SCI_NEWLINE = 2329
SCI_ADDTEXT = 2001
SCI_CHARRIGHT = 2306
SCI_GETCHARAT = 2007
SCI_DELETERANGE = 2645
SCI_SETSEL = 2160
SCI_SELECTIONDUPLICATE = 2469
SCI_GETFIRSTVISIBLELINE = 2152
SCI_SETFIRSTVISIBLELINE = 2613
SCI_LINESONSCREEN = 2370
SCI_EMPTYUNDOBUFFER = 2175
SCI_INDICSETUNDER = 2510
SCI_WORDENDPOSITION = 2267


def _bgr(colour: str) -> int:
    c = QColor(colour)
    return c.red() | (c.green() << 8) | (c.blue() << 16)


class PythonLexer(QsciLexerPython):
    def keywords(self, kset):
        if kset == 2:
            return BUILTINS
        return super().keywords(kset)


class CodeEditor(QsciScintilla):
    content_idle = pyqtSignal(object)
    breakpoints_changed = pyqtSignal(object)
    goto_requested = pyqtSignal(object)
    _untitled = 0

    def __init__(self, host, settings, theme, parent=None):
        super().__init__(parent)
        self.host = host
        self.path: str | None = None
        CodeEditor._untitled += 1
        self.untitled_name = f"untitled-{CodeEditor._untitled}.py"
        self.encoding = "utf-8"
        self.bom = False
        self.saved_mtime: float | None = None
        self.lint_items: list[dict] = []
        self._bp_conditions: dict[int, str] = {}
        self._dwell = None
        self._last_hover_req = None

        self.lexer_ = PythonLexer(self)
        self.setLexer(self.lexer_)
        self.setUtf8(True)
        self.setEolMode(QsciScintilla.EolMode.EolWindows if os.name == "nt" else QsciScintilla.EolMode.EolUnix)

        self.setMarginType(0, QsciScintilla.MarginType.SymbolMargin)
        self.setMarginWidth(0, 18)
        self.setMarginSensitivity(0, True)
        self.setMarginMarkerMask(0, BP_MASK | (1 << MARK_ARROW) | (1 << MARK_ERR) | (1 << MARK_WARN))
        self.setMarginType(1, QsciScintilla.MarginType.NumberMargin)
        self.setMarginSensitivity(1, True)
        self.setMarginMarkerMask(1, 0)
        self.setFolding(QsciScintilla.FoldStyle.BoxedTreeFoldStyle, 2)

        self.markerDefine(QsciScintilla.MarkerSymbol.Circle, MARK_BP)
        self.markerDefine(QsciScintilla.MarkerSymbol.Circle, MARK_BP_COND)
        self.markerDefine(QsciScintilla.MarkerSymbol.RightArrow, MARK_ARROW)
        self.markerDefine(QsciScintilla.MarkerSymbol.Background, MARK_DEBUG_BG)
        self.markerDefine(QsciScintilla.MarkerSymbol.LeftRectangle, MARK_ERR)
        self.markerDefine(QsciScintilla.MarkerSymbol.LeftRectangle, MARK_WARN)
        self.indicatorDefine(QsciScintilla.IndicatorStyle.SquiggleIndicator, IND_ERR)
        self.indicatorDefine(QsciScintilla.IndicatorStyle.SquiggleIndicator, IND_WARN)
        self.indicatorDefine(QsciScintilla.IndicatorStyle.FullBoxIndicator, IND_OCC)
        self.SendScintilla(SB.SCI_INDICSETALPHA, IND_OCC, 110)
        self.SendScintilla(SCI_INDICSETUNDER, IND_OCC, 1)

        self.setAutoIndent(False)  # Python-aware indentation is handled in _newline
        self.setIndentationGuides(True)
        self.setBackspaceUnindents(True)
        self.setTabIndents(True)
        self.setBraceMatching(QsciScintilla.BraceMatch.SloppyBraceMatch)
        self.setCaretLineVisible(True)
        self.setCaretWidth(2)
        self.setAutoCompletionSource(QsciScintilla.AutoCompletionSource.AcsNone)
        send = self.SendScintilla
        send(SB.SCI_SETMULTIPLESELECTION, 1)
        send(SB.SCI_SETADDITIONALSELECTIONTYPING, 1)
        send(SB.SCI_SETMULTIPASTE, SB.SC_MULTIPASTE_EACH)
        send(SB.SCI_SETMOUSEDWELLTIME, 550)
        send(SB.SCI_SETSCROLLWIDTHTRACKING, 1)
        send(SB.SCI_SETSCROLLWIDTH, 1)
        send(SCI_AUTOCSETORDER, 2)  # keep Jedi's ranking
        send(SB.SCI_AUTOCSETIGNORECASE, 1)
        send(SB.SCI_AUTOCSETMAXHEIGHT, 12)
        for kind, n in KIND_IMAGES.items():
            self.registerImage(n, kind_pixmap(kind, 16))

        self.idle_timer = QTimer(self, singleShot=True, interval=600)
        self.idle_timer.timeout.connect(lambda: self.content_idle.emit(self))
        self.textChanged.connect(self.idle_timer.start)
        self.complete_timer = QTimer(self, singleShot=True, interval=80)
        self.complete_timer.timeout.connect(lambda: self.request_completion(False))
        self.occ_timer = QTimer(self, singleShot=True, interval=280)
        self.occ_timer.timeout.connect(self._highlight_occurrences)
        self.cursorPositionChanged.connect(lambda *_: self.occ_timer.start())
        self.linesChanged.connect(self._update_margin_width)
        self.marginClicked.connect(self._margin_clicked)
        self.userListActivated.connect(self._completion_chosen)
        self.SCN_DWELLSTART.connect(self._dwell_start)
        self.SCN_DWELLEND.connect(self._dwell_end)
        self.apply_settings(settings, theme)

    # ------------------------------------------------------------ appearance
    def apply_settings(self, settings, theme) -> None:
        self.settings, self.theme = settings, theme
        t, s = theme, theme["syntax"]
        font = mono_font(settings.get("font_family"), int(settings.get("font_size")))
        lx = self.lexer_
        lx.setDefaultFont(font)
        lx.setFont(font, -1)
        lx.setDefaultPaper(QColor(t["bg"]))
        lx.setPaper(QColor(t["bg"]), -1)
        lx.setDefaultColor(QColor(s["default"]))
        lx.setColor(QColor(s["default"]), -1)
        styles = {
            L.Default: "default", L.Comment: "comment", L.CommentBlock: "comment", L.Number: "number",
            L.DoubleQuotedString: "string", L.SingleQuotedString: "string",
            L.DoubleQuotedFString: "string", L.SingleQuotedFString: "string",
            L.TripleSingleQuotedString: "docstring", L.TripleDoubleQuotedString: "docstring",
            L.TripleSingleQuotedFString: "string", L.TripleDoubleQuotedFString: "string",
            L.Keyword: "keyword", L.ClassName: "class", L.FunctionMethodName: "function",
            L.Operator: "operator", L.Identifier: "identifier", L.HighlightedIdentifier: "builtin",
            L.Decorator: "decorator", L.UnclosedString: "unclosed",
        }
        for style, key in styles.items():
            lx.setColor(QColor(s[key]), style)
        italic = mono_font(settings.get("font_family"), int(settings.get("font_size")))
        italic.setItalic(True)
        for style in (L.Comment, L.CommentBlock):
            lx.setFont(italic, style)
        lx.setFoldComments(True)
        lx.setFoldQuotes(True)

        self.setMarginsFont(font)
        self.setMarginsBackgroundColor(QColor(t["margin_bg"]))
        self.setMarginsForegroundColor(QColor(t["margin_fg"]))
        self.setFoldMarginColors(QColor(t["margin_bg"]), QColor(t["margin_bg"]))
        # Fold markers are Scintilla's reserved numbers 25-31, which QScintilla's
        # colour setters silently ignore; talk to Scintilla directly.
        for n in range(25, 32):
            self.SendScintilla(SB.SCI_MARKERSETFORE, n, _bgr(t["margin_bg"]))
            self.SendScintilla(SB.SCI_MARKERSETBACK, n, _bgr(t["margin_fg"]))
        self.setCaretForegroundColor(QColor(t["caret"]))
        self.setCaretLineBackgroundColor(QColor(t["caret_line"]))
        self.setSelectionBackgroundColor(QColor(t["select"]))
        self.resetSelectionForegroundColor()
        self.setMatchedBraceBackgroundColor(QColor(t["brace"]))
        self.setMatchedBraceForegroundColor(QColor(s["default"]))
        self.setUnmatchedBraceForegroundColor(QColor(t["error"]))
        self.setUnmatchedBraceBackgroundColor(QColor(t["bg"]))
        self.setIndentationGuidesBackgroundColor(QColor(t["guide"]))
        self.setIndentationGuidesForegroundColor(QColor(t["guide"]))
        self.setCallTipsBackgroundColor(QColor(t["panel"]))
        self.setCallTipsForegroundColor(QColor(t["fg"]))
        self.setCallTipsHighlightColor(QColor(t["info"]))
        edge = int(settings.get("edge_column"))
        self.setEdgeMode(QsciScintilla.EdgeMode.EdgeLine if edge > 0 else QsciScintilla.EdgeMode.EdgeNone)
        self.setEdgeColumn(max(edge, 1))
        self.setEdgeColor(QColor(t["edge"]))
        width = int(settings.get("tab_width"))
        self.setTabWidth(width)
        self.setIndentationWidth(width)
        self.setIndentationsUseTabs(bool(settings.get("use_tabs")))
        self.setWrapMode(QsciScintilla.WrapMode.WrapWord if settings.get("word_wrap") else QsciScintilla.WrapMode.WrapNone)
        self.setWhitespaceVisibility(QsciScintilla.WhitespaceVisibility.WsVisible if settings.get("show_whitespace")
                                     else QsciScintilla.WhitespaceVisibility.WsInvisible)

        for mark, colour in ((MARK_BP, t["breakpoint"]), (MARK_BP_COND, t["warning"]), (MARK_ARROW, t["warning"]),
                             (MARK_ERR, t["error"]), (MARK_WARN, t["warning"])):
            self.setMarkerBackgroundColor(QColor(colour), mark)
            self.setMarkerForegroundColor(QColor(colour), mark)
        self.setMarkerBackgroundColor(QColor(t["debug_line"]), MARK_DEBUG_BG)
        self.setIndicatorForegroundColor(QColor(t["error"]), IND_ERR)
        self.setIndicatorForegroundColor(QColor(t["warning"]), IND_WARN)
        self.setIndicatorForegroundColor(QColor(t["occurrence"]), IND_OCC)
        self._update_margin_width()

    def _update_margin_width(self) -> None:
        self.setMarginWidth(1, "0" * (len(str(self.lines())) + 1))

    # ------------------------------------------------------------------ files
    def display_name(self) -> str:
        return os.path.basename(self.path) if self.path else self.untitled_name

    def load(self, path: str) -> None:
        data = Path(path).read_bytes()
        self.bom = data.startswith(b"\xef\xbb\xbf")
        text = None
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                text = data.decode("utf-8-sig" if enc == "utf-8" else enc)
                self.encoding = enc
                break
            except UnicodeDecodeError:
                continue
        crlf = text.count("\r\n")
        lf = text.count("\n") - crlf
        if crlf or lf:
            self.setEolMode(QsciScintilla.EolMode.EolWindows if crlf > lf else QsciScintilla.EolMode.EolUnix)
        self.setText(text)
        self.path = os.path.abspath(path)
        self.SendScintilla(SCI_EMPTYUNDOBUFFER)
        self.setModified(False)
        self.saved_mtime = os.stat(path).st_mtime

    def save(self, path: str | None = None) -> None:
        path = os.path.abspath(path or self.path)
        text = self.text()
        try:
            data = text.encode(self.encoding)
        except UnicodeEncodeError:
            self.encoding = "utf-8"
            data = text.encode("utf-8")
        if self.bom and self.encoding == "utf-8":
            data = b"\xef\xbb\xbf" + data
        Path(path).write_bytes(data)
        self.path = path
        self.setModified(False)
        self.saved_mtime = os.stat(path).st_mtime

    def eol_name(self) -> str:
        return "CRLF" if self.eolMode() == QsciScintilla.EolMode.EolWindows else "LF"

    def set_eol(self, name: str) -> None:
        mode = QsciScintilla.EolMode.EolWindows if name == "CRLF" else QsciScintilla.EolMode.EolUnix
        self.setEolMode(mode)
        self.convertEols(mode)

    # ------------------------------------------------------------ navigation
    def cursor_line_col(self) -> tuple[int, int]:
        line, col = self.getCursorPosition()
        return line + 1, col

    def goto_line(self, line: int, col: int = 0) -> None:
        line = max(0, min(line - 1, self.lines() - 1))
        self.setCursorPosition(line, max(col, 0))
        visible = self.SendScintilla(SCI_LINESONSCREEN)
        first = self.SendScintilla(SCI_GETFIRSTVISIBLELINE)
        if not first <= line < first + visible - 1:
            self.SendScintilla(SCI_SETFIRSTVISIBLELINE, max(0, line - visible // 3))
        self.setFocus()

    def set_text_undoable(self, text: str) -> None:
        line, col = self.getCursorPosition()
        first = self.SendScintilla(SCI_GETFIRSTVISIBLELINE)
        self.beginUndoAction()
        self.selectAll()
        self.replaceSelectedText(text)
        self.endUndoAction()
        self.setCursorPosition(min(line, self.lines() - 1), col)
        self.SendScintilla(SCI_SETFIRSTVISIBLELINE, first)

    # ----------------------------------------------------------- breakpoints
    def toggle_breakpoint(self, line: int | None = None) -> None:
        if line is None:
            line = self.getCursorPosition()[0]
        if self.markersAtLine(line) & BP_MASK:
            self._remove_bp(line)
        else:
            self._add_bp(line, None)
        self.breakpoints_changed.emit(self)

    def _add_bp(self, line: int, condition: str | None) -> None:
        handle = self.markerAdd(line, MARK_BP_COND if condition else MARK_BP)
        if condition:
            self._bp_conditions[handle] = condition

    def _remove_bp(self, line: int) -> None:
        for handle in [h for h in self._bp_conditions if self.markerLine(h) == line]:
            del self._bp_conditions[handle]
        self.markerDelete(line, MARK_BP)
        self.markerDelete(line, MARK_BP_COND)

    def breakpoints(self) -> list[dict]:
        out, line = [], 0
        while (line := self.markerFindNext(line, BP_MASK)) >= 0:
            cond = next((c for h, c in self._bp_conditions.items() if self.markerLine(h) == line), None)
            out.append({"line": line + 1, "condition": cond})
            line += 1
        return out

    def set_breakpoints(self, items: list[dict]) -> None:
        self.markerDeleteAll(MARK_BP)
        self.markerDeleteAll(MARK_BP_COND)
        self._bp_conditions.clear()
        for bp in items:
            if 0 < bp["line"] <= self.lines():
                self._add_bp(bp["line"] - 1, bp.get("condition"))

    def edit_breakpoint_condition(self, line: int) -> None:
        current = next((c for h, c in self._bp_conditions.items() if self.markerLine(h) == line), "")
        cond, ok = QInputDialog.getText(self, "Breakpoint Condition",
                                        f"Break at line {line + 1} only when this expression is true\n"
                                        "(leave empty for an unconditional breakpoint):", text=current)
        if not ok:
            return
        self._remove_bp(line)
        self._add_bp(line, cond.strip() or None)
        self.breakpoints_changed.emit(self)

    def set_debug_line(self, line: int | None) -> None:
        self.markerDeleteAll(MARK_ARROW)
        self.markerDeleteAll(MARK_DEBUG_BG)
        if line:
            self.markerAdd(line - 1, MARK_ARROW)
            self.markerAdd(line - 1, MARK_DEBUG_BG)
            self.goto_line(line)

    def _margin_clicked(self, margin, line, _mods) -> None:
        if margin in (0, 1):
            self.toggle_breakpoint(line)

    # ------------------------------------------------------------------ lint
    def _clear_indicator(self, ind: int) -> None:
        self.SendScintilla(SB.SCI_SETINDICATORCURRENT, ind)
        self.SendScintilla(SB.SCI_INDICATORCLEARRANGE, 0, self.SendScintilla(SB.SCI_GETLENGTH))

    def set_lint(self, items: list[dict]) -> None:
        self.lint_items = items
        self._clear_indicator(IND_ERR)
        self._clear_indicator(IND_WARN)
        self.markerDeleteAll(MARK_ERR)
        self.markerDeleteAll(MARK_WARN)
        n = self.lines()
        for it in items:
            line = min(max(it["line"] - 1, 0), n - 1)
            text = self.text(line).rstrip("\r\n")
            col = min(it.get("col", 0), max(len(text) - 1, 0))
            start = self.positionFromLineIndex(line, col)
            end = self.SendScintilla(SCI_WORDENDPOSITION, start, True)
            if end <= start:
                end = self.positionFromLineIndex(line, len(text)) if it.get("whole_line") else start + 1
            if it.get("whole_line"):
                start = self.positionFromLineIndex(line, len(text) - len(text.lstrip()))
                end = self.positionFromLineIndex(line, len(text))
            error = it["severity"] == "error"
            self.SendScintilla(SB.SCI_SETINDICATORCURRENT, IND_ERR if error else IND_WARN)
            self.SendScintilla(SB.SCI_INDICATORFILLRANGE, start, max(end - start, 1))
            self.markerAdd(line, MARK_ERR if error else MARK_WARN)

    def _highlight_occurrences(self) -> None:
        self._clear_indicator(IND_OCC)
        length = self.SendScintilla(SB.SCI_GETLENGTH)
        if length > 2_000_000:
            return
        if self.hasSelectedText():
            word = self.selectedText()
        else:
            line, col = self.getCursorPosition()
            word = self.wordAtLineIndex(line, col)
        if not word or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", word):
            return
        data = self.text().encode("utf-8")
        hits = [m.start() for m in re.finditer(rb"(?<![\w])" + re.escape(word.encode()) + rb"(?![\w])", data)]
        if len(hits) < 2 or len(hits) > 2000:
            return
        self.SendScintilla(SB.SCI_SETINDICATORCURRENT, IND_OCC)
        for start in hits:
            self.SendScintilla(SB.SCI_INDICATORFILLRANGE, start, len(word))

    # ------------------------------------------------------------- keyboard
    def event(self, e):
        # Let IDE shortcuts (F5, Shift+Enter, ...) beat QScintilla's own key handling.
        if e.type() == e.Type.ShortcutOverride:
            combo = e.modifiers().value & ~Qt.KeyboardModifier.KeypadModifier.value | e.key()
            if self.host.is_ide_shortcut(combo):
                e.ignore()
                return True
        return super().event(e)

    def keyPressEvent(self, e):
        key, text = e.key(), e.text()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Space and ctrl:
            self.request_completion(True)
            return
        if key == Qt.Key.Key_Escape and self.SendScintilla(SB.SCI_CALLTIPACTIVE):
            self.SendScintilla(SB.SCI_CALLTIPCANCEL)
            return
        if (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not self.isListActive()
                and not mods & ~Qt.KeyboardModifier.KeypadModifier and self.SendScintilla(SCI_GETSELECTIONS) == 1):
            self._newline()
            return
        if (text and not ctrl and self.settings.get("auto_close_brackets")
                and self.SendScintilla(SCI_GETSELECTIONS) == 1 and self._auto_pair(text)):
            self._after_char(text)
            return
        if key == Qt.Key.Key_Backspace and not self.hasSelectedText() and self._delete_pair():
            return
        super().keyPressEvent(e)
        if text and not ctrl:
            self._after_char(text)

    def _pos(self) -> int:
        return self.SendScintilla(SB.SCI_GETCURRENTPOS)

    def _char_at(self, pos: int) -> str:
        if pos < 0 or pos >= self.SendScintilla(SB.SCI_GETLENGTH):
            return ""
        return chr(self.SendScintilla(SCI_GETCHARAT, pos) & 0xFF)

    def in_non_code(self, pos: int | None = None) -> bool:
        pos = self._pos() if pos is None else pos
        if pos <= 0:
            return False
        return self.SendScintilla(SB.SCI_GETSTYLEAT, pos - 1) in NON_CODE_STYLES

    def _newline(self) -> None:
        line, col = self.getCursorPosition()
        before = self.text(line)[:col]
        indent = re.match(r"[ \t]*", before).group(0)
        stripped = before.strip()
        unit = "\t" if self.indentationsUseTabs() else " " * self.indentationWidth()
        code = stripped.split("#", 1)[0].rstrip() if not self.in_non_code() else ""
        if code.endswith((":", "(", "[", "{", "\\")):
            indent += unit
        elif re.match(r"(return|pass|break|continue|raise)\b", stripped) and indent:
            indent = indent[:-len(unit)] if indent.endswith(unit) else indent[:-1]
        self.beginUndoAction()
        self.SendScintilla(SCI_NEWLINE)
        if indent:
            raw = indent.encode("utf-8")
            self.SendScintilla(SCI_ADDTEXT, len(raw), raw)
        self.endUndoAction()

    def _auto_pair(self, ch: str) -> bool:
        pos = self._pos()
        nxt, prev = self._char_at(pos), self._char_at(pos - 1)
        if ch in ")]}\"'" and nxt == ch and not self.hasSelectedText():
            if ch in "\"'" or not self.in_non_code():
                self.SendScintilla(SCI_CHARRIGHT)
                return True
        if ch not in PAIRS:
            return False
        if self.hasSelectedText():
            selected = self.selectedText()
            if "\n" in selected and ch in "\"'":
                return False
            self.replaceSelectedText(ch + selected + PAIRS[ch])
            return True
        if self.in_non_code():
            return False
        if ch in "\"'":
            prefix_ok = prev in "rbfuRBFU" and not self._char_at(pos - 2).isalnum()
            if prev == ch or (prev.isalnum() and not prefix_ok):
                return False
        if nxt and (nxt.isalnum() or nxt == "_"):
            return False
        raw = (ch + PAIRS[ch]).encode()
        self.beginUndoAction()
        self.SendScintilla(SCI_ADDTEXT, len(raw), raw)
        self.SendScintilla(SB.SCI_GOTOPOS, pos + 1)
        self.endUndoAction()
        return True

    def _delete_pair(self) -> bool:
        if not self.settings.get("auto_close_brackets"):
            return False
        pos = self._pos()
        prev, nxt = self._char_at(pos - 1), self._char_at(pos)
        if prev in PAIRS and PAIRS[prev] == nxt and self.SendScintilla(SCI_GETSELECTIONS) == 1:
            self.SendScintilla(SCI_DELETERANGE, pos - 1, 2)
            return True
        return False

    def _after_char(self, ch: str) -> None:
        if self.isListActive():
            if re.match(r"\w", ch):
                self.complete_timer.start()
            else:
                self.cancelList()
        elif ch == "." and not self.in_non_code() and not self._char_at(self._pos() - 2).isdigit():
            self.complete_timer.start()
        elif re.match(r"[A-Za-z_]", ch) and len(self.word_prefix()) >= 2 and not self.in_non_code():
            self.complete_timer.start()
        if ch in "(,":
            if not self.in_non_code():
                self.host.intel_request(self, "signature")
        elif ch == ")":
            self.SendScintilla(SB.SCI_CALLTIPCANCEL)

    # ----------------------------------------------------------- completion
    def word_prefix(self) -> str:
        line, col = self.getCursorPosition()
        return re.search(r"\w*$", self.text(line)[:col]).group(0)

    def request_completion(self, explicit: bool) -> None:
        if self.in_non_code() and not explicit:
            return
        line, col = self.getCursorPosition()
        before = self.text(line)[:col]
        if not explicit and not self.word_prefix() and not before.endswith("."):
            return
        self.host.intel_request(self, "complete", explicit=explicit)

    def show_completions(self, items, req) -> None:
        line, col = self.getCursorPosition()
        if req["line"] != line + 1 or col < req["col"]:
            return
        prefix = self.word_prefix()
        before = self.text(line)[:col]
        if not req.get("explicit") and not prefix and not before.endswith("."):
            return
        lower = prefix.lower()
        names = [(n, k) for n, k in items if n.lower().startswith(lower)]
        if not names or (len(names) == 1 and names[0][0] == prefix):
            self.cancelList()
            return
        self.showUserList(1, [f"{n}?{KIND_IMAGES.get(k, 9)}" for n, k in names])

    def _completion_chosen(self, _id, text) -> None:
        name = text.split("?", 1)[0]
        pos = self._pos()
        start = self.SendScintilla(SB.SCI_WORDSTARTPOSITION, pos, True)
        self.SendScintilla(SCI_SETSEL, start, pos)
        self.replaceSelectedText(name)

    def show_signature(self, payload, req) -> None:
        line, _ = self.getCursorPosition()
        if not payload or req["line"] != line + 1:
            self.SendScintilla(SB.SCI_CALLTIPCANCEL)
            return
        text = payload["text"]
        self.SendScintilla(SB.SCI_CALLTIPSHOW, self._pos(), text.encode("utf-8"))
        if payload.get("span"):
            a, b = payload["span"]
            self.SendScintilla(SB.SCI_CALLTIPSETHLT, len(text[:a].encode()), len(text[:b].encode()))

    # ----------------------------------------------------------------- hover
    def _dwell_start(self, pos, x, y) -> None:
        if pos < 0 or self.isListActive() or self.SendScintilla(SB.SCI_CALLTIPACTIVE):
            return
        line, col = self.lineIndexFromPosition(pos)
        lint = [it for it in self.lint_items if it["line"] - 1 == line]
        self._dwell = (pos, x, y, lint)
        if self.in_non_code(pos + 1) or not self.wordAtLineIndex(line, col):
            if lint:
                self._show_tooltip(None, lint, x, y)
            return
        self._last_hover_req = self.host.intel_request(self, "hover", line=line + 1, col=col)

    def _dwell_end(self, *_):
        self._dwell = None

    def show_hover(self, payload, req) -> None:
        if not self._dwell or req.get("id") != self._last_hover_req:
            return
        _, x, y, lint = self._dwell
        if payload or lint:
            self._show_tooltip(payload, lint, x, y)

    def show_docs_at_cursor(self, payload) -> None:
        if not payload:
            QToolTip.showText(self._cursor_point(), "No documentation found", self)
            return
        self._show_tooltip(payload, [], None, None)

    def _cursor_point(self) -> QPoint:
        pos = self._pos()
        x = self.SendScintilla(SB.SCI_POINTXFROMPOSITION, 0, pos)
        y = self.SendScintilla(SB.SCI_POINTYFROMPOSITION, 0, pos)
        return self.mapToGlobal(QPoint(x, y + 18))

    def _show_tooltip(self, payload, lint, x, y) -> None:
        t = self.theme
        parts = []
        for it in lint:
            colour = t["error"] if it["severity"] == "error" else t["warning"]
            parts.append(f"<div style='color:{colour}'>&#9679; {html.escape(it['message'])}</div>")
        if payload:
            doc = payload.get("text") or ""
            lines = doc.strip().splitlines()
            if len(lines) > 30:
                lines = lines[:30] + ["..."]
            if parts:
                parts.append("<hr>")
            parts.append(f"<b>{html.escape(payload.get('title') or '')}</b> "
                         f"<span style='color:{t['fg_dim']}'>{html.escape(payload.get('type') or '')}</span>")
            if lines:
                parts.append("<pre style='white-space:pre-wrap; margin:6px 0 0 0'>"
                             + html.escape("\n".join(lines)) + "</pre>")
        if not parts:
            return
        point = self._cursor_point() if x is None else self.mapToGlobal(QPoint(x, y + 16))
        QToolTip.showText(point, "<div style='max-width:640px'>" + "".join(parts) + "</div>", self)

    # ----------------------------------------------------------------- mouse
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            pos = self.SendScintilla(SB.SCI_POSITIONFROMPOINTCLOSE, int(e.position().x()), int(e.position().y()))
            if pos >= 0:
                line, col = self.lineIndexFromPosition(pos)
                self.setCursorPosition(line, col)
                self.goto_requested.emit(self)
                return
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        margin_width = sum(self.marginWidth(i) for i in range(3))
        if e.pos().x() < margin_width:
            pos = self.SendScintilla(SB.SCI_POSITIONFROMPOINT, 0, e.pos().y())
            line = self.SendScintilla(SB.SCI_LINEFROMPOSITION, pos)
            menu = QMenu(self)
            menu.addAction("Toggle Breakpoint", lambda: self.toggle_breakpoint(line))
            menu.addAction("Edit Breakpoint Condition...", lambda: self.edit_breakpoint_condition(line))
            menu.exec(e.globalPos())
            return
        menu = self.createStandardContextMenu()
        first = menu.actions()[0] if menu.actions() else None
        for action in self.host.editor_context_actions():
            if action is None:
                menu.insertSeparator(first)
            else:
                menu.insertAction(first, action)
        menu.insertSeparator(first)
        menu.exec(e.globalPos())

    # ------------------------------------------------------------ edit ops
    def _selected_lines(self) -> tuple[int, int]:
        if self.hasSelectedText():
            lf, _, lt, it = self.getSelection()
            if it == 0 and lt > lf:
                lt -= 1
            return lf, lt
        line = self.getCursorPosition()[0]
        return line, line

    def toggle_comment(self) -> None:
        lf, lt = self._selected_lines()
        had_selection = self.hasSelectedText()
        lines = [self.text(i).rstrip("\r\n") for i in range(lf, lt + 1)]
        code = [ln for ln in lines if ln.strip()]
        if not code:
            return
        uncomment = all(ln.lstrip().startswith("#") for ln in code)
        indent = min(len(ln) - len(ln.lstrip()) for ln in code)
        self.beginUndoAction()
        for i in range(lf, lt + 1):
            t = self.text(i).rstrip("\r\n")
            if not t.strip():
                continue
            if uncomment:
                idx = t.index("#")
                width = 2 if t[idx + 1:idx + 2] == " " else 1
                self.setSelection(i, idx, i, idx + width)
                self.removeSelectedText()
            else:
                self.insertAt("# ", i, indent)
        self.endUndoAction()
        if had_selection:
            self.setSelection(lf, 0, lt, len(self.text(lt).rstrip("\r\n")))

    def duplicate(self) -> None:
        self.SendScintilla(SCI_SELECTIONDUPLICATE if self.hasSelectedText() else SB.SCI_LINEDUPLICATE)

    def move_lines(self, up: bool) -> None:
        self.SendScintilla(SB.SCI_MOVESELECTEDLINESUP if up else SB.SCI_MOVESELECTEDLINESDOWN)

    def select_next_occurrence(self) -> None:
        if not self.hasSelectedText():
            pos = self._pos()
            start = self.SendScintilla(SB.SCI_WORDSTARTPOSITION, pos, True)
            end = self.SendScintilla(SCI_WORDENDPOSITION, pos, True)
            if end > start:
                self.SendScintilla(SCI_SETSEL, start, end)
            return
        self.SendScintilla(SCI_TARGETWHOLEDOCUMENT)
        self.SendScintilla(SCI_SETSEARCHFLAGS, SB.SCFIND_MATCHCASE)
        self.SendScintilla(SB.SCI_MULTIPLESELECTADDNEXT)

    def trim_trailing_whitespace(self) -> None:
        text = self.text()
        new = re.sub(r"[ \t]+(?=\r?\n|$)", "", text)
        if new != text:
            self.set_text_undoable(new)

    def selection_or_line(self) -> str:
        if self.hasSelectedText():
            return self.selectedText()
        return self.text(self.getCursorPosition()[0])

    def current_cell(self) -> str:
        lines = self.text().splitlines()
        cur = self.getCursorPosition()[0]
        marker = re.compile(r"^\s*#\s*%%")
        start = next((i for i in range(min(cur, len(lines) - 1), -1, -1) if marker.match(lines[i])), -1) + 1
        end = next((i for i in range(cur + 1, len(lines)) if marker.match(lines[i])), len(lines))
        return "\n".join(lines[start:end])


class FindBar(QFrame):
    def __init__(self, editor: CodeEditor, parent=None):
        super().__init__(parent)
        self.setObjectName("FindBar")
        self.editor = editor
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.find_edit = QLineEdit(placeholderText="Find")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.textChanged.connect(lambda: self.find(True, incremental=True))
        self.find_edit.returnPressed.connect(lambda: self.find(True))
        row.addWidget(self.find_edit, 1)
        self.case = self._toggle("Aa", "Match case")
        self.word = self._toggle("W", "Whole word")
        self.regex = self._toggle(".*", "Regular expression")
        for b in (self.case, self.word, self.regex):
            row.addWidget(b)
        self.count = QLabel()
        self.count.setObjectName("Dim")
        self.count.setMinimumWidth(70)
        row.addWidget(self.count)
        for label, tip, fn in (("↑", "Previous (Shift+Enter)", lambda: self.find(False)),
                               ("↓", "Next (Enter)", lambda: self.find(True))):
            b = QToolButton(text=label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        self.toggle_replace = QToolButton(text="Replace")
        self.toggle_replace.setCheckable(True)
        self.toggle_replace.toggled.connect(self._show_replace)
        row.addWidget(self.toggle_replace)
        close = QToolButton(text="✕")
        close.clicked.connect(self.close_bar)
        row.addWidget(close)
        outer.addLayout(row)

        self.replace_row = QWidget()
        rrow = QHBoxLayout(self.replace_row)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.setSpacing(4)
        self.replace_edit = QLineEdit(placeholderText="Replace with")
        self.replace_edit.returnPressed.connect(self.replace_one)
        rrow.addWidget(self.replace_edit, 1)
        for label, fn in (("Replace", self.replace_one), ("Replace All", self.replace_all)):
            b = QToolButton(text=label)
            b.clicked.connect(fn)
            rrow.addWidget(b)
        outer.addWidget(self.replace_row)
        self.replace_row.hide()
        self.find_edit.installEventFilter(self)
        self.replace_edit.installEventFilter(self)
        self.hide()

    def _toggle(self, text, tip):
        b = QToolButton(text=text)
        b.setCheckable(True)
        b.setToolTip(tip)
        b.toggled.connect(lambda: self.find(True, incremental=True))
        return b

    def eventFilter(self, obj, e):
        if e.type() == e.Type.KeyPress:
            if e.key() == Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if obj is self.find_edit and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                    and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.find(False)
                return True
        return False

    def _show_replace(self, on: bool) -> None:
        self.replace_row.setVisible(on)

    def open(self, replace: bool = False) -> None:
        if self.editor.hasSelectedText() and "\n" not in self.editor.selectedText():
            self.find_edit.blockSignals(True)
            self.find_edit.setText(self.editor.selectedText())
            self.find_edit.blockSignals(False)
        self.toggle_replace.setChecked(replace)
        self.show()
        (self.replace_edit if replace and self.find_edit.text() else self.find_edit).setFocus()
        self.find_edit.selectAll()
        self._update_count()

    def close_bar(self) -> None:
        self.hide()
        self.editor.setFocus()

    def _pattern(self) -> re.Pattern | None:
        text = self.find_edit.text()
        if not text:
            return None
        pat = text if self.regex.isChecked() else re.escape(text)
        if self.word.isChecked():
            pat = rf"\b{pat}\b"
        try:
            return re.compile(pat, 0 if self.case.isChecked() else re.IGNORECASE)
        except re.error:
            return None

    def _update_count(self) -> None:
        pat = self._pattern()
        if not pat:
            self.count.setText("" if not self.find_edit.text() else "bad regex")
            return
        n = sum(1 for _ in pat.finditer(self.editor.text()))
        self.count.setText("No results" if n == 0 else f"{n} match{'es' if n != 1 else ''}")

    def find(self, forward: bool, incremental: bool = False) -> bool:
        text = self.find_edit.text()
        self._update_count()
        if not text:
            return False
        e = self.editor
        line = index = -1
        if e.hasSelectedText():
            lf, if_, lt, it = e.getSelection()
            if incremental or not forward:
                line, index = lf, if_
            else:
                line, index = lt, it
        found = e.findFirst(text, self.regex.isChecked(), self.case.isChecked(), self.word.isChecked(), True,
                            forward, line, index, True, True)
        return found

    def replace_one(self) -> None:
        e = self.editor
        pat = self._pattern()
        if pat and e.hasSelectedText() and pat.fullmatch(e.selectedText()):
            replacement = pat.sub(self.replace_edit.text(), e.selectedText()) if self.regex.isChecked() \
                else self.replace_edit.text()
            e.replaceSelectedText(replacement)
        self.find(True)

    def replace_all(self) -> None:
        pat = self._pattern()
        if not pat:
            return
        rep = self.replace_edit.text()
        new, n = pat.subn(rep if self.regex.isChecked() else (lambda _m: rep), self.editor.text())
        if n:
            self.editor.set_text_undoable(new)
        self.count.setText(f"Replaced {n}")


class EditorPage(QWidget):
    """One tab: an info strip, the find bar, and the editor."""

    def __init__(self, host, settings, theme, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.info = InfoBar(self)
        self.editor = CodeEditor(host, settings, theme, self)
        self.find_bar = FindBar(self.editor, self)
        lay.addWidget(self.info)
        lay.addWidget(self.find_bar)
        lay.addWidget(self.editor, 1)
