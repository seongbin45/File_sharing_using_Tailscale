"""Colour roles from design/FastAPI 관리화면 설계/나루 디자인 참고 조사.

The app follows the system theme, so every colour is a role with a light
and a dark value instead of a literal in a stylesheet. The roles are the
guide's, and there are deliberately few of them:

  primary  main buttons, selection, and the "잘 되고 있어요" verdict -
           success is blue too, there is no green
  teal     the pairing code and connection states only
  warn     caution (amber)
  danger   failure (red)

#0067c0 is too dark to read as text on a dark background, so the dark
theme uses Fluent's dark-mode accent for the same role.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

_LIGHT = {
    "card": "#ffffff",
    "border": "#e5e5e5",
    "muted": "#5d5d5d",
    "faint": "#767676",
    "primary": "#0067c0",
    "on_primary": "#ffffff",
    "primary_bg": "#eaf2fb",
    "teal": "#0f766e",
    "warn": "#7a5900",
    "warn_bg": "#fff4ce",
    "warn_border": "#f2d27a",
    "danger": "#c42b1c",
}

_DARK = {
    "card": "#2b2b2b",
    "border": "#3d3d3d",
    "muted": "#c5c5c5",
    "faint": "#9a9a9a",
    "primary": "#4cc2ff",
    "on_primary": "#000000",
    "primary_bg": "rgba(76, 194, 255, 0.16)",
    "teal": "#2dd4bf",
    "warn": "#fce100",
    "warn_bg": "rgba(252, 225, 0, 0.10)",
    "warn_border": "rgba(252, 225, 0, 0.35)",
    "danger": "#ff99a4",
}

# The pairing code's own teal (#14b8a6) is the brand colour; the text roles
# above are darkened/lightened copies so it stays readable on each theme.
TEAL_BRAND = "#14b8a6"


def is_dark() -> bool:
    app = QApplication.instance()
    return bool(app) and app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def c(role: str) -> str:
    return (_DARK if is_dark() else _LIGHT)[role]


def apply_font(app: QApplication) -> None:
    """Segoe UI Variable with 맑은 고딕 for Hangul - the system faces."""
    font = QFont(app.font())
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Malgun Gothic"])
    app.setFont(font)
