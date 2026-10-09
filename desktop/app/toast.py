"""나루's own toast - 나루 디자인 시스템 §09 트레이 · 알림.

The design's toast has a header (logo, 나루, ✕), a title, one or two lines,
and up to two buttons ("지금 다시 보내기" / "열어 보기"). Windows' tray
balloon (QSystemTrayIcon.showMessage) cannot carry buttons, so this is a
small frameless window shown above the taskbar's notification corner.
It is the only place besides the tray menu that casts a shadow (§06: 그림자는
창 밖에 뜨는 것에만).

Toasts can be missed, so nothing depends on one being seen: the same state
stays on the tray icon and the home verdict (principle ④).
"""

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QVBoxLayout, QWidget

from .theme import animations_enabled
from .widgets import LogoMark, button, hbox, label

SHOW_MS = 12_000


class Toast(QWidget):
    def __init__(self, title: str, body: str, actions: list[tuple[str, object]]) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        card = QFrame()
        card.setProperty("card", "true")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 70))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 16, 18)
        lay.setSpacing(8)
        close = button("✕", "quiet")
        close.setFixedSize(32, 32)
        close.setStyleSheet("min-height: 0; padding: 0;")
        close.clicked.connect(self.close)
        lay.addLayout(hbox(LogoMark(28), 2, label("나루", "caption2"), None, close, spacing=6))
        t = label(title, "section", wrap=True)
        t.setStyleSheet("font-size: 15px;")
        lay.addWidget(t)
        lay.addWidget(label(body, "body2", wrap=True))
        if actions:
            lay.addSpacing(4)
            btns = []
            for i, (text, fn) in enumerate(actions[:2]):     # 최대 둘
                b = button(text, "primary" if i == 0 else None)
                b.clicked.connect(lambda _c=False, f=fn: (f(), self.close()))
                btns.append(b)
            lay.addLayout(hbox(*btns, spacing=8))
            for b in btns:
                b.setMinimumWidth(150)
        self.setFixedWidth(420)
        self._timer = QTimer(self, singleShot=True, interval=SHOW_MS, timeout=self.close)

    def enterEvent(self, e) -> None:  # noqa: N802 - hovering holds it open
        self._timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._timer.start()
        super().leaveEvent(e)

    def popup(self) -> None:
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 8, screen.bottom() - self.height() - 8)
        if animations_enabled():
            self.setWindowOpacity(0.0)
            self.show()
            fade = QPropertyAnimation(self, b"windowOpacity", self)
            fade.setDuration(200)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.start(QPropertyAnimation.DeleteWhenStopped)
        else:
            self.show()
        self._timer.start()
