"""나루 design tokens, and the stylesheet built from them.

Source: design/FastAPI 관리화면 설계/나루 디자인 시스템 (v0.1, A안 - 면과 둥글기),
sections 04 색, 05 서체, 06 간격·크기·모서리·모션 and 10 Qt 구현. The token
names are the document's (--pr, --tl, ...). Every value has a light and a
dark twin because the app follows the system theme; QSS knows no
variables, so build_qss() fills a template and apply() installs it, again
whenever Windows switches theme.

Widgets opt into a look with dynamic properties, never per-widget
stylesheets:  card="true", verdict="ok|link|warn|err", role="title|...",
kind="primary|secondary|text", size="large", state="error". After changing
one at runtime call repolish(widget).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

LIGHT = {
    # brand - 주 버튼 · 선택 · 정상 판정
    "pr": "#0067c0", "prh": "#00559e", "prt": "#0067c0", "prs": "#e6f0fa",
    # 연결 - 짝 코드 · 연결선 · 연결 중에만
    "tl": "#14b8a6", "tlt": "#0b7f73", "tls": "#e3f7f4",
    # 판정 - 주의와 실패
    "wn": "#e8a100", "wnt": "#8a5a00", "wns": "#fdf3dc",
    "er": "#c42b1c", "ert": "#b02618", "ers": "#fdecea",
    # 중립
    "bg": "#f3f5f8", "sf": "#ffffff", "sf2": "#eef1f5", "bd": "#e2e6ec", "bd2": "#c7cdd6",
    "tx": "#15191e", "tx2": "#48525e", "tx3": "#6a7480", "txd": "#a9b0b9",
    "onpr": "#ffffff", "onwn": "#2b1d00",
}

DARK = {
    "pr": "#1a73c9", "prh": "#2a84d8", "prt": "#6fb3f2", "prs": "#16293d",
    "tl": "#2dd4bf", "tlt": "#5eead4", "tls": "#0f2e2a",
    "wn": "#f0b429", "wnt": "#f5c35a", "wns": "#33280f",
    "er": "#e5534b", "ert": "#ff9a8f", "ers": "#3a1c1a",
    "bg": "#121417", "sf": "#1c1f24", "sf2": "#262a31", "bd": "#30353d", "bd2": "#464c56",
    "tx": "#eef1f4", "tx2": "#b6bdc6", "tx3": "#8f98a2", "txd": "#5c636c",
    "onpr": "#ffffff", "onwn": "#2b1d00",
}

# 06 · 크기, 모서리 (A안)
RADIUS = {"window": 14, "card": 16, "ctl": 10, "chip": 999}
SPACE = (4, 8, 12, 16, 20, 24, 32, 48)
WINDOW_MIN = (720, 560)
VERDICT_CIRCLE = 48
DOT = 8

# 05 · 서체. Latin/digits Segoe UI Variable, Hangul 맑은 고딕, code Cascadia Mono.
UI_FACES = ["Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Malgun Gothic"]
DISPLAY_FACES = ["Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI", "Malgun Gothic"]
MONO_FACES = ["Cascadia Mono", "Consolas", "D2Coding"]


def is_dark() -> bool:
    app = QApplication.instance()
    return bool(app) and app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def tokens() -> dict[str, str]:
    return DARK if is_dark() else LIGHT


def c(name: str) -> str:
    return tokens()[name]


def qcolor(name: str) -> QColor:
    return QColor(tokens()[name])


def css_families(families: list[str]) -> str:
    return ", ".join(f'"{f}"' for f in families)


def build_qss(t: dict[str, str]) -> str:
    """The design's "생성되는 QSS" (section 10), extended to every component
    this app draws. Pixel sizes are the design's px values."""
    r = RADIUS
    return f"""
/* 나루 - generated from app/theme.py tokens */
QWidget {{
  color: {t['tx']};
  font-family: {css_families(UI_FACES)};
  font-size: 14px;
}}
QMainWindow, QDialog, QWidget[page="true"], QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{
  background: {t['bg']};
}}
QLabel {{ background: transparent; }}
QToolTip {{ background: {t['sf']}; color: {t['tx']}; border: 1px solid {t['bd']}; padding: 6px 8px; border-radius: 8px; }}

/* surfaces */
QFrame[card="true"] {{ background: {t['sf']}; border: none; border-radius: {r['card']}px; }}
QFrame[card="soft"] {{ background: {t['sf2']}; border: none; border-radius: {r['card']}px; }}
QFrame[note="true"] {{ background: {t['sf2']}; border: none; border-radius: {r['ctl'] + 2}px; }}
QFrame[verdictCircle="ok"]   {{ background: {t['pr']}; border-radius: 24px; }}
QFrame[verdictCircle="link"] {{ background: {t['tl']}; border-radius: 24px; }}
QFrame[verdictCircle="warn"] {{ background: {t['wn']}; border-radius: 24px; }}
QFrame[verdictCircle="err"]  {{ background: {t['er']}; border-radius: 24px; }}
QFrame[verdictCircle="none"] {{ background: {t['sf2']}; border-radius: 24px; }}
QLabel[glyph="ok"], QLabel[glyph="link"], QLabel[glyph="err"] {{ color: {t['onpr']}; font-size: 22px; font-weight: 600; }}
QLabel[glyph="warn"] {{ color: {t['onwn']}; font-size: 22px; font-weight: 700; }}
QLabel[glyph="none"] {{ color: {t['tx3']}; font-size: 22px; }}

/* text roles */
QLabel[role="title"]    {{ font-size: 22px; font-weight: 600; }}
QLabel[role="verdict"]  {{ font-size: 17px; font-weight: 600; }}
QLabel[role="section"]  {{ font-size: 14px; font-weight: 600; }}
QLabel[role="strong"]   {{ font-weight: 600; }}
QLabel[role="body2"]    {{ color: {t['tx2']}; }}
QLabel[role="caption"]  {{ font-size: 12.5px; color: {t['tx3']}; }}
QLabel[role="caption2"] {{ font-size: 12.5px; color: {t['tx2']}; }}
QLabel[role="number"]   {{ font-family: {css_families(DISPLAY_FACES)}; font-size: 34px; font-weight: 700; }}
QLabel[role="numberSm"] {{ font-family: {css_families(DISPLAY_FACES)}; font-size: 26px; font-weight: 700; }}
QLabel[tone="pr"]   {{ color: {t['prt']}; }}
QLabel[tone="tl"]   {{ color: {t['tlt']}; }}
QLabel[tone="wn"]   {{ color: {t['wnt']}; }}
QLabel[tone="er"]   {{ color: {t['ert']}; }}
QLabel[tone="dim"]  {{ color: {t['txd']}; }}
QLabel[role="code"] {{
  font-family: {css_families(MONO_FACES)}; font-size: 36px; font-weight: 600; color: {t['tlt']};
}}
QLabel[pill="role"] {{
  background: {t['prs']}; color: {t['prt']}; font-weight: 600;
  border-radius: 14px; padding: 4px 12px;
}}
QLabel[pill="badge"] {{ color: {t['prt']}; font-weight: 600; font-size: 12.5px; }}

/* buttons */
QPushButton {{
  min-height: 40px; padding: 0 20px;
  border: none; border-radius: {r['ctl']}px;
  background: {t['sf2']}; color: {t['tx']};
}}
QPushButton:hover {{ background: {t['bd']}; }}
QPushButton:pressed {{ background: {t['bd2']}; }}
QPushButton:disabled {{ background: {t['sf2']}; color: {t['txd']}; }}
QPushButton[kind="primary"] {{ background: {t['pr']}; color: {t['onpr']}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t['prh']}; }}
QPushButton[kind="primary"]:pressed {{ background: {t['prh']}; }}
QPushButton[kind="primary"]:disabled {{ background: {t['sf2']}; color: {t['txd']}; }}
QPushButton[kind="white"] {{ background: {t['sf']}; color: {t['tlt']}; font-weight: 600; min-height: 34px; padding: 0 18px; }}
QPushButton[kind="text"] {{
  background: transparent; border: none; padding: 0 8px; color: {t['prt']}; font-weight: 600;
}}
QPushButton[kind="text"]:hover {{ background: {t['prs']}; }}
QPushButton[kind="text"]:disabled {{ color: {t['txd']}; background: transparent; }}
QPushButton[kind="quiet"] {{ background: transparent; color: {t['tx2']}; font-weight: 400; padding: 0 12px; }}
QPushButton[kind="quiet"]:hover {{ background: {t['sf2']}; }}
QPushButton[kind="tealText"] {{ background: transparent; color: {t['tlt']}; font-weight: 400; min-height: 34px; padding: 0 12px; }}
QPushButton[kind="tealText"]:hover {{ background: {t['sf']}; }}
QPushButton[size="large"] {{ min-height: 44px; padding: 0 26px; font-size: 15px; }}

/* inputs */
QLineEdit {{
  min-height: 40px; padding: 0 12px;
  background: {t['sf2']}; border: 2px solid transparent; border-radius: {r['ctl']}px;
  selection-background-color: {t['pr']}; selection-color: {t['onpr']};
}}
QLineEdit:focus {{ border: 2px solid {t['pr']}; background: {t['sf']}; }}
QLineEdit[readOnly="true"] {{ color: {t['tx']}; }}
QLineEdit[kind="code"] {{
  min-height: 56px; padding: 0 18px;
  font-family: {css_families(MONO_FACES)}; font-size: 24px;
}}
QLineEdit[kind="code"]:focus {{ border: 2px solid {t['tl']}; }}
QLineEdit[state="error"], QLineEdit[state="error"]:focus {{ border: 2px solid {t['er']}; }}
QLineEdit[state="ok"] {{ border: 2px solid {t['tl']}; }}

/* lists, menus, scrollbars */
QTreeWidget, QListWidget {{ background: transparent; border: none; }}
QMenu {{
  background: {t['sf']}; border: 1px solid {t['bd']}; border-radius: {r['ctl']}px; padding: 6px;
}}
QMenu::item {{ padding: 8px 28px 8px 12px; border-radius: 8px; background: transparent; }}
QMenu::item:selected {{ background: {t['sf2']}; color: {t['tx']}; }}
QMenu::item:disabled {{ color: {t['tx3']}; }}
QMenu::separator {{ height: 1px; background: {t['bd']}; margin: 4px 6px; }}
QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['bd2']}; border-radius: 4px; min-height: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QPlainTextEdit {{
  background: {t['sf']}; border: none; border-radius: {r['card']}px; padding: 12px;
  font-family: {css_families(MONO_FACES)}; font-size: 12px; color: {t['tx2']};
}}

/* 설정: left navigation */
QListWidget[nav="true"]::item {{ min-height: 40px; padding: 0 14px; border-radius: {r['ctl']}px; color: {t['tx2']}; }}
QListWidget[nav="true"]::item:selected {{ background: {t['prs']}; color: {t['prt']}; font-weight: 600; }}
QListWidget[nav="true"]::item:hover:!selected {{ background: {t['sf2']}; }}
QPushButton[row="true"] {{
  background: transparent; border: none; border-radius: 0; text-align: left;
  min-height: 52px; padding: 0 20px; font-weight: 400;
}}
QPushButton[row="true"]:hover {{ background: {t['sf2']}; }}
"""


def palette(t: dict[str, str]) -> QPalette:
    """The design's token -> QPalette rows, for anything QSS doesn't reach
    (native dialogs, focus rings, selection)."""
    p = QPalette()
    p.setColor(QPalette.Window, QColor(t["bg"]))
    p.setColor(QPalette.Base, QColor(t["sf"]))
    p.setColor(QPalette.AlternateBase, QColor(t["sf2"]))
    p.setColor(QPalette.WindowText, QColor(t["tx"]))
    p.setColor(QPalette.Text, QColor(t["tx"]))
    p.setColor(QPalette.ButtonText, QColor(t["tx"]))
    p.setColor(QPalette.Button, QColor(t["sf2"]))
    p.setColor(QPalette.PlaceholderText, QColor(t["tx3"]))
    p.setColor(QPalette.Highlight, QColor(t["pr"]))
    p.setColor(QPalette.HighlightedText, QColor(t["onpr"]))
    p.setColor(QPalette.Link, QColor(t["prt"]))
    p.setColor(QPalette.ToolTipBase, QColor(t["sf"]))
    p.setColor(QPalette.ToolTipText, QColor(t["tx"]))
    if hasattr(QPalette, "Accent"):
        p.setColor(QPalette.Accent, QColor(t["pr"]))
    return p


def apply_font(app: QApplication) -> None:
    font = QFont(app.font())
    font.setFamilies(UI_FACES)
    font.setPixelSize(14)
    app.setFont(font)


def apply(app: QApplication) -> None:
    """Install fonts, palette and QSS for the current system theme, and
    reinstall them whenever Windows switches between light and dark."""
    apply_font(app)
    t = tokens()
    app.setPalette(palette(t))
    app.setStyleSheet(build_qss(t))
    if not getattr(app, "_naru_theme_hooked", False):
        app.styleHints().colorSchemeChanged.connect(lambda _s: _reapply(app))
        app._naru_theme_hooked = True  # type: ignore[attr-defined]


def _reapply(app: QApplication) -> None:
    t = tokens()
    app.setPalette(palette(t))
    app.setStyleSheet(build_qss(t))
    # Widgets that paint themselves (circles, dots, icons) read tokens at
    # paint time; ask everything to repaint.
    for w in app.allWidgets():
        w.update()


def repolish(w: QWidget) -> None:
    """Re-evaluate QSS after changing a dynamic property."""
    s = w.style()
    s.unpolish(w)
    s.polish(w)
    w.update()


def set_prop(w: QWidget, name: str, value) -> None:
    if w.property(name) != value:
        w.setProperty(name, value)
        repolish(w)


def animations_enabled() -> bool:
    """Windows' "애니메이션 효과" switch (SPI_GETCLIENTAREAANIMATION). Off means
    every transition is instant (section 06 모션)."""
    import sys

    if sys.platform != "win32":
        return True
    try:
        import ctypes

        value = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0)
        return bool(value.value) if ok else True
    except Exception:  # noqa: BLE001 - a missing API means "assume on"
        return True
