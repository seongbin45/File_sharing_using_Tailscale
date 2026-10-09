"""나루 components, one class per item in 나루 디자인 시스템 §07 컴포넌트.

Looks come from the QSS in theme.py via dynamic properties; what QSS
cannot draw (circles with glyphs, dots, step pills, the toggle, the radio
mark, motion) is painted here, reading colours from theme tokens at paint
time so a theme switch repaints correctly.

Motion follows §06: only the wizard page turn (250ms, 24px slide + fade),
a verdict change (400ms, circle 0.9 -> 1), a test step completing (150ms
check pop), and the teal waiting dot's slow breath (1.6s). All of it is
skipped when Windows' animation effects are off.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation,
    QRectF, QSize, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import theme
from .theme import MONO_FACES, animations_enabled, qcolor, set_prop


def ease() -> QEasingCurve:
    """§06 곡선 (0.2, 0, 0, 1)."""
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.2, 0.0), QPointF(0.0, 1.0), QPointF(1.0, 1.0))
    return curve


def label(text: str = "", role: str | None = None, tone: str | None = None,
          wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    if tone:
        w.setProperty("tone", tone)
    w.setWordWrap(wrap)
    return w


def button(text: str, kind: str | None = None, large: bool = False) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if large:
        b.setProperty("size", "large")
    b.setCursor(Qt.PointingHandCursor)
    return b


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


class ElidedLabel(QLabel):
    """One line that shortens with … instead of widening its parent - paths
    and device names have no spaces for Qt to wrap at, and a long one would
    otherwise push the whole page wider than the window. The full text is
    the tooltip."""

    def __init__(self, text: str = "", role: str | None = None, mode=Qt.ElideMiddle) -> None:
        super().__init__()
        if role:
            self.setProperty("role", role)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def fullText(self) -> str:  # noqa: N802
        return self._full

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, super().minimumSizeHint().height())

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, self._mode, max(10, self.width()))
        super().setText(shown)


class Card(QFrame):
    """A white (A안: borderless, 16px) surface on the window background."""

    def __init__(self, padding: int = 16, spacing: int = 8, soft: bool = False) -> None:
        super().__init__()
        self.setProperty("card", "soft" if soft else "true")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding + 4, padding, padding + 4, padding)
        self.body.setSpacing(spacing)


class Dot(QWidget):
    """An 8px status dot. tone: token name (pr, tl, wn, er, bd2, txd).
    pulse=True breathes like the design's waiting state (1.6s)."""

    def __init__(self, tone: str = "pr", size: int = 8, pulse: bool = False) -> None:
        super().__init__()
        self._tone = tone
        self._size = size
        self._halo = 0.0
        self.setFixedSize(size * 2 + 4, size * 2 + 4)
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=1600)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)
        self.set_pulse(pulse)

    def _tick(self, v) -> None:
        self._halo = float(v)
        self.update()

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def set_pulse(self, on: bool) -> None:
        if on and animations_enabled():
            self._anim.start()
        else:
            self._anim.stop()
            self._halo = 0.0
            self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QRectF(self.rect()).center()
        color = qcolor(self._tone)
        if self._halo:
            halo = QColor(color)
            halo.setAlphaF(0.35 * (1 - self._halo))
            r = self._size / 2 + self._size * 0.9 * self._halo
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(c, r, r)
        elif self._anim.state() != QVariantAnimation.Running and self._tone == "tl":
            # Resting halo, as in the design's waiting line.
            soft = QColor(qcolor("tls"))
            p.setPen(Qt.NoPen)
            p.setBrush(soft)
            p.drawEllipse(c, self._size, self._size)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, self._size / 2, self._size / 2)


class VerdictCard(QFrame):
    """§07 판정 - 네 가지 상태: ok ✓ 파랑, link ⇄ 청록, warn ! 호박, err ✕ 빨강
    (plus "none", a quiet grey for "nothing yet"). A 48px circle, the
    verdict line, one supporting line, and an optional right-hand slot."""

    GLYPH = {"ok": "✓", "link": "⇄", "warn": "!", "err": "✕", "none": "·"}

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("card", "true")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(16)

        self.circle = QFrame()
        self.circle.setFixedSize(48, 48)
        self.circle.setProperty("verdictCircle", "none")
        cl = QVBoxLayout(self.circle)
        cl.setContentsMargins(0, 0, 0, 0)
        self.glyph = QLabel("·")
        self.glyph.setAlignment(Qt.AlignCenter)
        self.glyph.setProperty("glyph", "none")
        cl.addWidget(self.glyph)
        lay.addWidget(self.circle, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = label("", "verdict")
        self.title.setWordWrap(True)
        self.sub = label("", "caption2")
        self.sub.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.sub)
        lay.addLayout(text, 1)

        self.right = QHBoxLayout()
        self.right.setSpacing(8)
        lay.addLayout(self.right)
        self._state = "none"
        self._fade = QGraphicsOpacityEffect(self.title)
        self._fade.setOpacity(1.0)
        self.title.setGraphicsEffect(self._fade)

    @property
    def state(self) -> str:
        return self._state

    def set_verdict(self, state: str, title: str, sub: str = "") -> None:
        changed = state != self._state
        self._state = state
        set_prop(self.circle, "verdictCircle", state)
        set_prop(self.glyph, "glyph", state)
        self.glyph.setText(self.GLYPH.get(state, "·"))
        self.title.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        if changed and animations_enabled() and self.isVisible():
            self._animate()

    def _animate(self) -> None:
        # 400ms: the circle grows 0.9 -> 1, the title cross-fades in.
        group = QParallelAnimationGroup(self)
        grow = QVariantAnimation(self, startValue=43, endValue=48, duration=400)
        grow.setEasingCurve(ease())
        grow.valueChanged.connect(lambda v: self.circle.setFixedSize(int(v), int(v)))
        fade = QPropertyAnimation(self._fade, b"opacity", self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(400)
        fade.setEasingCurve(ease())
        group.addAnimation(grow)
        group.addAnimation(fade)
        group.start(QParallelAnimationGroup.DeleteWhenStopped)

    def set_right(self, *widgets: QWidget) -> None:
        while self.right.count():
            item = self.right.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        for w in widgets:
            self.right.addWidget(w, 0, Qt.AlignVCenter)


class StatTile(QFrame):
    """§07 통계 타일: caption, a large number, a caption under it."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        self.setProperty("card", "true")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(2)
        self.caption = label(caption, "caption")
        self.value = label("-", "number")
        self.detail = label("", "caption")
        lay.addWidget(self.caption)
        lay.addWidget(self.value)
        lay.addWidget(self.detail)

    def set(self, value: str, detail: str = "", tone: str | None = None) -> None:
        self.value.setText(value)
        self.detail.setText(detail)
        set_prop(self.detail, "tone", tone)


class ListCard(QFrame):
    """A titled card holding §07 목록 행 rows; an optional text button on the
    title line ("전체 기록", "폴더 열기")."""

    def __init__(self, title: str, action: str | None = None) -> None:
        super().__init__()
        self.setProperty("card", "true")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 12, 10)
        outer.setSpacing(2)
        self.action = button(action, "text") if action else None
        outer.addLayout(hbox(label(title, "section"), None, self.action, margins=(0, 0, 4, 4)))
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        outer.addLayout(self.rows)
        self.empty = label("", "caption")
        self.empty.setContentsMargins(0, 8, 0, 8)
        outer.addWidget(self.empty)
        self.empty.hide()

    def clear(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def add_row(self, tone: str, when: str, what: str, size: str = "",
                state: str = "", state_tone: str | None = None, tip: str = "") -> QWidget:
        row = QWidget()
        row.setMinimumHeight(40)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(12)
        lay.addWidget(Dot(tone))
        t = label(when, "body2")
        t.setMinimumWidth(86)
        lay.addWidget(t)
        lay.addWidget(label(what), 1)
        if size:
            s = label(size, "caption")
            s.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lay.addWidget(s)
        if state:
            st = label(state, "caption2", state_tone)
            st.setMinimumWidth(78)
            st.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lay.addWidget(st)
        if tip:
            row.setToolTip(tip)
        self.rows.addWidget(row)
        return row

    def set_empty(self, text: str) -> None:
        self.empty.setText(text)
        self.empty.setVisible(bool(text))


class StepDots(QWidget):
    """Wizard progress: pills for pages reached, small dots for the rest."""

    def __init__(self, total: int = 5) -> None:
        super().__init__()
        self._total = total
        self._current = 0
        self.setFixedHeight(10)

    def set_step(self, current: int, total: int | None = None) -> None:
        self._current = current
        if total is not None:
            self._total = total
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(28 * (self._current + 1) + 10 * self._total + 8, 10)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        x = 0.0
        for i in range(self._total):
            if i <= self._current:
                p.setBrush(qcolor("pr"))
                p.drawRoundedRect(QRectF(x, 2, 26, 6), 3, 3)
                x += 32
            else:
                p.setBrush(qcolor("bd2"))
                p.drawEllipse(QRectF(x, 2, 6, 6))
                x += 12


class _Clickable(QFrame):
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._selected = False

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def isSelected(self) -> bool:  # noqa: N802
        return self._selected

    def setSelected(self, on: bool) -> None:  # noqa: N802
        self._selected = on
        self._restyle()

    def _restyle(self) -> None:
        t = theme.tokens()
        name = type(self).__name__
        if self._selected:
            css = f"{name} {{ background: {t['prs']}; border: 2px solid {t['pr']}; border-radius: 16px; }}"
        else:
            css = f"{name} {{ background: {t['sf']}; border: 1px solid {t['bd2']}; border-radius: 16px; }}"
        self.setStyleSheet(css)
        self.update()


class ChoiceCard(_Clickable):
    """§09 role choice: a round icon, the role, what it does, who it usually is."""

    def __init__(self, arrow: str, title: str, desc: str, hint: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(6)
        self.icon = QLabel(arrow)
        self.icon.setFixedSize(40, 40)
        self.icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.icon)
        lay.addSpacing(10)
        t = label(title)
        t.setStyleSheet("font-size: 17px; font-weight: 600;")
        lay.addWidget(t)
        lay.addWidget(label(desc, "body2", wrap=True))
        lay.addSpacing(4)
        lay.addWidget(label(hint, "caption"))
        lay.addStretch(1)
        self.setMinimumHeight(180)
        self._restyle()

    def _restyle(self) -> None:
        super()._restyle()
        t = theme.tokens()
        bg = "transparent" if self._selected else t["prs"]
        self.icon.setStyleSheet(
            f"background: {bg}; color: {t['prt']}; border-radius: 20px; font-size: 18px; border: none;")


class RadioMark(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.on = False
        self.setFixedSize(22, 22)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(2, 2, 18, 18)
        if self.on:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor("pr"))
            p.drawEllipse(r)
            p.setBrush(qcolor("sf"))
            p.drawEllipse(r.center(), 4, 4)
        else:
            p.setPen(QPen(qcolor("bd2"), 1.6))
            p.setBrush(qcolor("sf"))
            p.drawEllipse(r)


class OptionRow(_Clickable):
    """§09 얼마나 자주: a radio row with an optional badge and a right note."""

    def __init__(self, title: str, note: str = "", badge: str = "") -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(12)
        self.mark = RadioMark()
        lay.addWidget(self.mark)
        t = label(title)
        t.setStyleSheet("font-size: 15px; font-weight: 600;")
        lay.addWidget(t)
        if badge:
            b = label(badge)
            b.setProperty("pill", "badge")
            lay.addSpacing(8)
            lay.addWidget(b)
        lay.addStretch(1)
        self.note = label(note, "body2")
        lay.addWidget(self.note)
        self.setFixedHeight(56)
        self._restyle()

    def setSelected(self, on: bool) -> None:  # noqa: N802
        self.mark.on = on
        self.mark.update()
        super().setSelected(on)


def set_code_font(w: QWidget, px: int, entry: bool = False) -> None:
    """Cascadia Mono, 600, letters spaced 0.14em (§07 짝 코드); the input
    that takes a code is regular weight at 0.12em (§07 코드 입력)."""
    f = QFont()
    f.setFamilies(MONO_FACES)
    f.setPixelSize(px)
    f.setWeight(QFont.Normal if entry else QFont.DemiBold)
    f.setLetterSpacing(QFont.AbsoluteSpacing, px * (0.12 if entry else 0.14))
    w.setFont(f)


class CodePanel(QFrame):
    """§07 짝 코드: the code on a soft teal face, 복사 / 새 코드, its lifetime."""

    copy = Signal()
    renew = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("codePanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 26, 24, 22)
        lay.setSpacing(12)
        # role=code carries family, size and weight (theme.py): the app
        # stylesheet's font rules beat a bare setFont(). Letter spacing has no
        # QSS property, so that alone comes from set_code_font().
        self.code = label("", "code")
        self.code.setAlignment(Qt.AlignCenter)
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        set_code_font(self.code, 36)
        lay.addWidget(self.code)
        self.copy_btn = button("복사", "white")
        self.copy_btn.clicked.connect(self.copy)
        self.renew_btn = button("새 코드", "tealText")
        self.renew_btn.clicked.connect(self.renew)
        lay.addLayout(hbox(None, self.copy_btn, self.renew_btn, None))
        self.life = label("10분 동안, 한 번만 쓸 수 있어요", "caption", "tl")
        self.life.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.life)
        self.restyle()

    def restyle(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(f"#codePanel {{ background: {t['tls']}; border-radius: 16px; }}")

    def set_code(self, code: str) -> None:
        self.code.setText(code)


class StatusLine(QWidget):
    """A dot and one line: teal (breathing) while waiting for the other side,
    blue once it is done, red when it failed."""

    TONES = {"wait": ("tl", "tl", True), "linked": ("tl", "tl", False),
             "ok": ("pr", "pr", False), "err": ("er", "er", False), "warn": ("wn", "wn", False)}

    def __init__(self) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.dot = Dot("tl")
        self.text = label("", "body2")
        self.text.setWordWrap(True)
        lay.addWidget(self.dot, 0, Qt.AlignTop)
        lay.addWidget(self.text, 1)

    def set(self, state: str, text: str) -> None:
        dot_tone, text_tone, pulse = self.TONES.get(state, ("tl", "tl", False))
        self.dot.set_tone(dot_tone)
        self.dot.set_pulse(pulse)
        set_prop(self.text, "tone", text_tone)
        self.text.setText(text)


class StepIcon(QWidget):
    """24px: done (blue ✓), running (teal ring, …), waiting (grey ring),
    failed (red ✕). Completion pops (150ms)."""

    def __init__(self) -> None:
        super().__init__()
        self.state = "wait"
        self._scale = 1.0
        self.setFixedSize(30, 30)

    def set_state(self, state: str) -> None:
        old, self.state = self.state, state
        if state in ("ok", "err") and old != state and animations_enabled():
            anim = QVariantAnimation(self, startValue=0.6, endValue=1.0, duration=150)
            anim.setEasingCurve(QEasingCurve.OutBack)
            anim.valueChanged.connect(self._grow)
            anim.start(QVariantAnimation.DeleteWhenStopped)
        self.update()

    def _grow(self, v) -> None:
        self._scale = float(v)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QRectF(self.rect()).center()
        r = 12 * (self._scale if self.state in ("ok", "err") else 1.0)
        if self.state in ("ok", "err"):
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor("pr" if self.state == "ok" else "er"))
            p.drawEllipse(c, r, r)
            p.setPen(QPen(QColor("#ffffff"), 2))
            f = p.font()
            f.setPixelSize(13)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter,
                       "✓" if self.state == "ok" else "✕")
        else:
            running = self.state == "run"
            p.setPen(QPen(qcolor("tl" if running else "bd2"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, 11, 11)
            if running:
                p.setPen(qcolor("tlt"))
                f = p.font()
                f.setPixelSize(11)
                p.setFont(f)
                p.drawText(QRectF(c.x() - 11, c.y() - 13, 22, 22), Qt.AlignCenter, "…")


class StepList(QFrame):
    """§07 시험 전송 단계: each step's icon, name and a right-hand note."""

    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.setProperty("card", "true")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(0)
        self.rows: list[tuple[StepIcon, QLabel, QLabel]] = []
        for name in names:
            icon = StepIcon()
            text = label(name)
            note = label("", "caption")
            note.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row = QWidget()
            row.setMinimumHeight(52)
            row.setLayout(hbox(icon, 4, text, None, note, spacing=10))
            lay.addWidget(row)
            self.rows.append((icon, text, note))
        self.reset()

    def reset(self) -> None:
        for icon, text, note in self.rows:
            icon.set_state("wait")
            set_prop(text, "role", "body2")
            note.setText("")
            set_prop(note, "tone", None)

    def set_step(self, i: int, state: str, note: str = "") -> None:
        icon, text, n = self.rows[i]
        icon.set_state(state)
        set_prop(text, "role", None if state != "wait" else "body2")
        n.setText(note)
        set_prop(n, "tone", "tl" if state == "run" else ("er" if state == "err" else None))


class ToggleSwitch(QAbstractButton):
    """§07 켜짐/꺼짐: a 44x24 track, blue when on."""

    def __init__(self) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(48, 28)
        self._pos = 0.0
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        end = 1.0 if on else 0.0
        if not animations_enabled():
            self._pos = end
            self.update()
            return
        a = QVariantAnimation(self, startValue=self._pos, endValue=end, duration=100)
        a.valueChanged.connect(self._set_pos)
        a.start(QVariantAnimation.DeleteWhenStopped)

    def _set_pos(self, v) -> None:
        self._pos = float(v)
        self.update()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(2, 2, 44, 24)
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor("pr") if on else qcolor("bd2"))
        p.drawRoundedRect(track, 12, 12)
        x = 4 + self._pos * 20
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, 4, 20, 20))


class NumberedSteps(QFrame):
    """§09 오류: "이렇게 해 보세요" and numbered things to try."""

    def __init__(self, title: str, steps: list[str]) -> None:
        super().__init__()
        self.setProperty("card", "true")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        lay.setSpacing(4)
        lay.addWidget(label(title, "section"))
        for i, s in enumerate(steps, start=1):
            n = QLabel(str(i))
            n.setFixedSize(24, 24)
            n.setAlignment(Qt.AlignCenter)
            n.setObjectName("stepNumber")
            n.setStyleSheet(f"#stepNumber {{ background: {theme.c('sf2')}; color: {theme.c('tx2')};"
                            " border-radius: 12px; font-size: 12px; }")
            row = QWidget()
            row.setMinimumHeight(40)
            text = label(s, wrap=True)
            line = hbox(n, text, spacing=12)
            line.setStretch(1, 1)
            row.setLayout(line)
            lay.addWidget(row)


class NoteBar(QFrame):
    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.setProperty("note", "true")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        self.text = label(text, "caption2", wrap=True)
        lay.addWidget(self.text)


class LogoMark(QWidget):
    """The logo, painted at a given width (for empty states and headers)."""

    def __init__(self, width: int, alpha: float = 1.0, line: bool = True) -> None:
        super().__init__()
        self._alpha = alpha
        self._line = line
        self.setFixedSize(width, width // 2)

    def paintEvent(self, _e) -> None:  # noqa: N802
        from .icons import draw_logo

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        draw_logo(p, QRectF(self.rect()), line=self._line, alpha=self._alpha)


class SlideStack(QStackedWidget):
    """A QStackedWidget whose page turns follow §06: the new page comes in
    from 24px to the right and fades in, 250ms."""

    def slide_to(self, index: int) -> None:
        if index == self.currentIndex():
            return
        forward = index > self.currentIndex()
        self.setCurrentIndex(index)
        if not animations_enabled() or not self.isVisible():
            return
        page = self.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        end = page.pos()
        start = end + QPoint(24 if forward else -24, 0)
        group = QParallelAnimationGroup(self)
        move = QPropertyAnimation(page, b"pos", self)
        move.setStartValue(start)
        move.setEndValue(end)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        for a in (move, fade):
            a.setDuration(250)
            a.setEasingCurve(ease())
            group.addAnimation(a)
        group.finished.connect(lambda: page.setGraphicsEffect(None))
        group.start(QParallelAnimationGroup.DeleteWhenStopped)


def page_widget() -> QWidget:
    w = QWidget()
    w.setProperty("page", True)
    w.setAttribute(Qt.WA_StyledBackground, True)
    return w

