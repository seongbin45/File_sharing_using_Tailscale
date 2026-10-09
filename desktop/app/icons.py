"""The 나루 logo and tray icons, drawn in code.

Design: 나루 디자인 시스템 §03 로고와 아이콘. Two dots are the two computers,
the white line across them the crossing (나루터): blue is this side, teal
the far side, and the circles touch so "connected" reads as the default.
At 16px and below the white line is dropped and only the dots remain.

Tray states (§03): 정상, 연결 중, 주의 (amber badge), 실패 (red badge),
멈춤 (grey). Drawing rather than shipping .png/.ico keeps the --onefile
build free of data files that could go missing at runtime.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from .theme import qcolor

SIZES = (16, 20, 24, 32, 48, 64, 256)

# The engine-facing names the rest of the app already uses, mapped onto the
# design's five tray states.
STATE_ALIASES = {"idle": "ok", "running": "link", "paused": "paused", "error": "err",
                 "warn": "warn", "ok": "ok", "link": "link", "err": "err"}


def draw_logo(p: QPainter, rect: QRectF, *, line: bool = True,
              left: QColor | None = None, right: QColor | None = None,
              alpha: float = 1.0) -> None:
    """Two touching circles side by side inside `rect` (2:1), and the
    crossing line when there is room for it."""
    left = QColor(left or qcolor("pr"))
    right = QColor(right or qcolor("tl"))
    left.setAlphaF(alpha)
    right.setAlphaF(alpha)
    d = min(rect.width() / 2, rect.height())
    cy = rect.center().y()
    x0 = rect.center().x() - d
    p.setPen(Qt.NoPen)
    p.setBrush(left)
    p.drawEllipse(QRectF(x0, cy - d / 2, d, d))
    p.setBrush(right)
    p.drawEllipse(QRectF(x0 + d, cy - d / 2, d, d))
    if line:
        h = d * 0.24
        bar = QRectF(x0 + d * 0.5, cy - h / 2, d, h)
        # alpha fades the dots only: the empty state's faded logo (§09) keeps
        # its crossing line solid white.
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(bar, h / 2, h / 2)


def logo_pixmap(width: int, *, line: bool = True, alpha: float = 1.0, dpr: float = 2.0) -> QPixmap:
    pix = QPixmap(int(width * dpr), int(width / 2 * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    draw_logo(p, QRectF(0, 0, width, width / 2), line=line, alpha=alpha)
    p.end()
    return pix


def _tray_pixmap(state: str, size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    line = size > 16
    pad = size * 0.06
    rect = QRectF(pad, size * 0.22, size - 2 * pad, size * 0.56)
    if state == "paused":
        grey = QColor("#9aa3ad")
        draw_logo(p, rect, line=line, left=grey, right=grey)
    elif state == "link":
        draw_logo(p, rect, line=line, left=qcolor("tl"), right=qcolor("tl"))
    else:
        draw_logo(p, rect, line=line)
    if state in ("warn", "err"):
        r = size * 0.36
        badge = QRectF(size - r - size * 0.02, size * 0.04, r, r)
        # A thin ring in the window colour keeps the badge readable over the
        # dot it overlaps.
        p.setBrush(qcolor("sf"))
        p.drawEllipse(badge.adjusted(-size * 0.04, -size * 0.04, size * 0.04, size * 0.04))
        p.setBrush(qcolor("wn" if state == "warn" else "er"))
        p.drawEllipse(badge)
    p.end()
    return pix


def app_icon(state: str = "ok") -> QIcon:
    """Window and tray icon for one of the design's states."""
    state = STATE_ALIASES.get(state, "ok")
    icon = QIcon()
    for s in SIZES:
        icon.addPixmap(_tray_pixmap(state, s))
    return icon


def window_icon() -> QIcon:
    return app_icon("ok")

