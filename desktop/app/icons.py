"""The app / tray icon, drawn in code.

Drawing it rather than shipping a .png means one fewer data file for
PyInstaller to bundle and find at runtime - a --onefile build unpacks to a
temp dir, and a missing asset there is a classic packaging failure. A
QPixmap painted at startup has no path to get wrong.

The dot changes colour with state so the tray icon alone tells you whether the
engine is idle, running, or stopped.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

_BG = QColor("#0c0c0c")
_BAR = QColor("#61d6d6")
STATE_COLORS = {
    "idle": QColor("#767676"),
    "running": QColor("#23d18b"),
    "paused": QColor("#c19c00"),
    "error": QColor("#e74856"),
}


def app_icon(state: str = "idle", size: int = 64) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    p.setBrush(_BG)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRect(0, 0, size, size), size * 0.18, size * 0.18)

    # Two stacked bars: the "archive" mark.
    p.setBrush(_BAR)
    p.drawRoundedRect(QRect(int(size * 0.22), int(size * 0.28),
                            int(size * 0.56), int(size * 0.12)), 2, 2)
    p.drawRoundedRect(QRect(int(size * 0.22), int(size * 0.50),
                            int(size * 0.40), int(size * 0.12)), 2, 2)

    # State dot, lower-right.
    p.setBrush(STATE_COLORS.get(state, STATE_COLORS["idle"]))
    r = int(size * 0.16)
    p.drawEllipse(QRect(size - r - int(size * 0.12), size - r - int(size * 0.12), r, r))
    p.end()
    return QIcon(pix)
