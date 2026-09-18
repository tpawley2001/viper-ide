"""Toolbar and completion icons drawn with QPainter (no image assets to ship or lose)."""
from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

COLORS = {
    "run": "#4caf50", "debug": "#e0a030", "stop": "#e55765", "continue": "#4caf50", "pause": "#e0a030",
    "step_over": "#56a8f5", "step_into": "#56a8f5", "step_out": "#56a8f5", "restart": "#4caf50",
    "save": "#8c8f96", "folder": "#d9a343", "file": "#8c8f96", "package": "#b07fd6", "clear": "#8c8f96",
    "search": "#8c8f96", "python": "#3574f0", "refresh": "#8c8f96", "terminal": "#8c8f96",
    "assistant": "#b07fd6",
}

KIND_COLORS = {
    "module": ("#7e57c2", "M"), "class": ("#e6a23c", "C"), "instance": ("#26a69a", "v"),
    "function": ("#3574f0", "f"), "method": ("#3574f0", "m"), "param": ("#8d6e63", "p"),
    "path": ("#78909c", "/"), "keyword": ("#cf8e6d", "k"), "property": ("#26a69a", "p"),
    "statement": ("#26a69a", "v"), "variable": ("#26a69a", "v"),
    "error": ("#f75464", "!"), "warning": ("#e0b050", "!"),
}


def _canvas(size: int = 32):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pm, p


def _arrow_head(p: QPainter, tip: QPointF, direction: str, size: float = 5.5) -> None:
    x, y = tip.x(), tip.y()
    pts = {
        "down": [QPointF(x - size, y - size), QPointF(x + size, y - size), QPointF(x, y + 1)],
        "up": [QPointF(x - size, y + size), QPointF(x + size, y + size), QPointF(x, y - 1)],
        "right": [QPointF(x - size, y - size), QPointF(x - size, y + size), QPointF(x + 1, y)],
    }[direction]
    p.drawPolygon(QPolygonF(pts))


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    pm, p = _canvas()
    c = QColor(COLORS.get(name, "#8c8f96"))
    pen = QPen(c, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(c)
    if name == "run":
        p.drawPolygon(QPolygonF([QPointF(9, 6), QPointF(26, 16), QPointF(9, 26)]))
    elif name == "debug":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(10, 10, 12, 16))
        for y in (14, 19, 24):
            p.drawLine(QPointF(5, y), QPointF(10, y))
            p.drawLine(QPointF(22, y), QPointF(27, y))
        p.drawLine(QPointF(12, 8), QPointF(10, 4))
        p.drawLine(QPointF(20, 8), QPointF(22, 4))
        p.drawLine(QPointF(16, 12), QPointF(16, 24))
    elif name == "stop":
        p.drawRoundedRect(QRectF(8, 8, 16, 16), 2, 2)
    elif name == "continue":
        p.drawRect(QRectF(7, 7, 3, 18))
        p.drawPolygon(QPolygonF([QPointF(14, 7), QPointF(27, 16), QPointF(14, 25)]))
    elif name == "pause":
        p.drawRect(QRectF(9, 7, 4, 18))
        p.drawRect(QRectF(19, 7, 4, 18))
    elif name in ("step_over", "step_into", "step_out"):
        p.drawEllipse(QPointF(16, 26), 2.5, 2.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if name == "step_over":
            path = QPainterPath(QPointF(6, 18))
            path.cubicTo(QPointF(7, 4), QPointF(25, 4), QPointF(26, 15))
            p.drawPath(path)
            p.setBrush(c)
            _arrow_head(p, QPointF(26, 19), "down")
        elif name == "step_into":
            p.drawLine(QPointF(16, 4), QPointF(16, 16))
            p.setBrush(c)
            _arrow_head(p, QPointF(16, 20), "down")
        else:
            p.drawLine(QPointF(16, 20), QPointF(16, 9))
            p.setBrush(c)
            _arrow_head(p, QPointF(16, 4), "up")
    elif name == "restart":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(7, 7, 18, 18), 60 * 16, 290 * 16)
        p.setBrush(c)
        _arrow_head(p, QPointF(24, 9), "up", 4.5)
    elif name == "save":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(6, 6, 20, 20), 3, 3)
        p.drawRect(QRectF(11, 6, 10, 6))
        p.drawRect(QRectF(10, 17, 12, 9))
    elif name == "folder":
        path = QPainterPath(QPointF(4, 9))
        for pt in ((12, 9), (15, 12), (28, 12), (28, 25), (4, 25)):
            path.lineTo(QPointF(*pt))
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)
    elif name == "file":
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(QPointF(8, 4))
        for pt in ((19, 4), (25, 10), (25, 28), (8, 28)):
            path.lineTo(QPointF(*pt))
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12, 17), QPointF(21, 17))
        p.drawLine(QPointF(12, 22), QPointF(21, 22))
    elif name == "package":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(16, 4), QPointF(27, 10), QPointF(27, 22), QPointF(16, 28),
                                 QPointF(5, 22), QPointF(5, 10)]))
        p.drawLine(QPointF(5, 10), QPointF(16, 16))
        p.drawLine(QPointF(27, 10), QPointF(16, 16))
        p.drawLine(QPointF(16, 16), QPointF(16, 28))
    elif name == "clear":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(7, 7, 18, 18))
        p.drawLine(QPointF(10, 22), QPointF(22, 10))
    elif name == "search":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(6, 6, 14, 14))
        p.drawLine(QPointF(18, 18), QPointF(26, 26))
    elif name == "refresh":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(7, 7, 18, 18), 30 * 16, 300 * 16)
        p.setBrush(c)
        _arrow_head(p, QPointF(25, 12), "down", 4.5)
    elif name == "terminal":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(4, 6, 24, 20), 3, 3)
        p.drawPolyline(QPolygonF([QPointF(9, 12), QPointF(13, 16), QPointF(9, 20)]))
        p.drawLine(QPointF(15, 21), QPointF(22, 21))
    elif name == "assistant":
        p.setPen(Qt.PenStyle.NoPen)
        for cx, cy, r in ((13, 17, 10), (24, 8, 5)):
            p.drawPolygon(QPolygonF([QPointF(cx, cy - r), QPointF(cx + r * 0.28, cy - r * 0.28), QPointF(cx + r, cy),
                                     QPointF(cx + r * 0.28, cy + r * 0.28), QPointF(cx, cy + r),
                                     QPointF(cx - r * 0.28, cy + r * 0.28), QPointF(cx - r, cy),
                                     QPointF(cx - r * 0.28, cy - r * 0.28)]))
    elif name == "python":
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(5, 4, 14, 14), 4, 4)
        p.setBrush(QColor("#f5c518"))
        p.drawRoundedRect(QRectF(13, 14, 14, 14), 4, 4)
    p.end()
    return QIcon(pm)


def close_pixmap(colour: str, size: int = 16) -> QPixmap:
    pm, p = _canvas(size)
    p.setPen(QPen(QColor(colour), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    m = size * 0.3
    p.drawLine(QPointF(m, m), QPointF(size - m, size - m))
    p.drawLine(QPointF(size - m, m), QPointF(m, size - m))
    p.end()
    return pm


@lru_cache(maxsize=None)
def kind_pixmap(kind: str, size: int = 16) -> QPixmap:
    colour, letter = KIND_COLORS.get(kind, ("#78909c", "?"))
    pm, p = _canvas(size)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(colour))
    p.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 4, 4)
    font = QFont()
    font.setPixelSize(int(size * 0.62))
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letter)
    p.end()
    return pm


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """The Viper logo: a green tile with a white V whose tail curls into a snake head."""
    pm, p = _canvas(size)
    s = size / 256
    p.setPen(Qt.PenStyle.NoPen)
    from PyQt6.QtGui import QLinearGradient

    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#2fb36b"))
    grad.setColorAt(1, QColor("#16734a"))
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 52 * s, 52 * s)
    pen = QPen(QColor("#ffffff"), 30 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(62 * s, 66 * s))
    path.lineTo(QPointF(128 * s, 196 * s))
    path.lineTo(QPointF(178 * s, 96 * s))
    path.cubicTo(QPointF(188 * s, 74 * s), QPointF(214 * s, 70 * s), QPointF(206 * s, 96 * s))
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QPointF(204 * s, 88 * s), 20 * s, 16 * s)
    p.setBrush(QColor("#16734a"))
    p.drawEllipse(QPointF(210 * s, 84 * s), 4.5 * s, 4.5 * s)
    p.end()
    return pm
