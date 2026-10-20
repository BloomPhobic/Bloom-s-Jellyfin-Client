"""Smooth (animated) mouse-wheel scrolling.

Mouse wheels send coarse 120-unit notches, which Qt applies as instant jumps.
We turn each notch into a short eased animation of the scrollbar, and
accumulate notches that arrive mid-animation so fast flicks still go far.
Touchpad gestures already move smoothly, so those keep Qt's native handling.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QScrollBar

NOTCH = 120
PIXELS_PER_NOTCH = 120
DURATION_MS = 240

# Settings → App → Smooth scrolling. Checked on every wheel notch, so it applies at once.
ENABLED = True


class SmoothScroller(QObject):
    """Animates one scrollbar towards an accumulating target."""

    def __init__(self, bar: QScrollBar, duration: int = DURATION_MS, parent: QObject | None = None):
        super().__init__(parent or bar)
        self.bar = bar
        self._target: int | None = None
        self.anim = QPropertyAnimation(bar, b"value", self)
        self.anim.setDuration(duration)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._done)

    def _done(self) -> None:
        self._target = None

    def scroll_by(self, pixels: float) -> None:
        running = self.anim.state() == QPropertyAnimation.State.Running
        base = self._target if (running and self._target is not None) else self.bar.value()
        target = int(max(self.bar.minimum(), min(self.bar.maximum(), base + pixels)))
        if not ENABLED:                        # plain jumps, same distance
            self.stop()
            self.bar.setValue(target)
            return
        if target == self.bar.value() and not running:
            return
        self._target = target
        self.anim.stop()
        self.anim.setStartValue(self.bar.value())
        self.anim.setEndValue(target)
        self.anim.start()

    def scroll_to(self, value: int) -> None:
        self.scroll_by(value - (self._target if self._target is not None else self.bar.value()))

    def stop(self) -> None:
        self.anim.stop()
        self._target = None


def is_touchpad(ev) -> bool:
    """Touchpads report a scroll phase (begin/update/end); wheels don't.

    (Pixel deltas aren't a reliable signal: on Wayland mouse wheels carry them too.)
    """
    return ev.phase() != Qt.ScrollPhase.NoScrollPhase


class SmoothWheel(QObject):
    """Event filter for a scroll area's viewport that animates vertical wheel scrolling.

    Wheel events that child widgets ignore (e.g. the horizontal card rows)
    propagate up to this viewport and get the same treatment.
    """

    def __init__(self, area: QAbstractScrollArea, pixels_per_notch: int = PIXELS_PER_NOTCH):
        super().__init__(area)
        self.area = area
        self.ppn = pixels_per_notch
        self.vertical = SmoothScroller(area.verticalScrollBar(), parent=self)
        area.viewport().installEventFilter(self)
        # A manual drag of the scrollbar should cancel a running animation.
        area.verticalScrollBar().sliderPressed.connect(self.vertical.stop)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() != QEvent.Type.Wheel:
            return False
        if ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            return False                       # zoom / sideways: not ours
        if is_touchpad(ev):
            return False
        dy = ev.angleDelta().y()
        if not dy:
            return False
        self.vertical.scroll_by(-dy / NOTCH * self.ppn)
        ev.accept()
        return True


def install(area: QAbstractScrollArea, pixels_per_notch: int = PIXELS_PER_NOTCH) -> SmoothWheel:
    return SmoothWheel(area, pixels_per_notch)
