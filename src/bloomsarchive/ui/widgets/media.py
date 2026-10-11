"""Image widgets for detail pages: a rounded poster/still and a backdrop header."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme
from .image_loader import ImageLoader


def _cover(pm: QPixmap, w: int, h: int, dpr: float) -> QPixmap:
    tw, th = max(1, int(w * dpr)), max(1, int(h * dpr))
    scaled = pm.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                       Qt.TransformationMode.SmoothTransformation)
    out = scaled.copy(max(0, (scaled.width() - tw) // 2), max(0, (scaled.height() - th) // 2), tw, th)
    out.setDevicePixelRatio(dpr)
    return out


class ImageBox(QWidget):
    """A fixed-size rounded image (poster or episode still) with a soft placeholder."""

    def __init__(self, loader: ImageLoader, size: QSize, radius: int = 14, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.key: str | None = None
        self.radius = radius
        self.setFixedSize(size)
        self._cache: tuple | None = None
        loader.loaded.connect(self._on_loaded)

    def set_key(self, key: str | None) -> None:
        if key != self.key:
            self.key = key
            self._cache = None
            self.update()

    def _on_loaded(self, key: str) -> None:
        if key == self.key:
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        p.setClipPath(path)
        pm = self.loader.get(self.key)
        if pm is not None and not pm.isNull():
            dpr = self.devicePixelRatioF()
            ck = (pm.cacheKey(), self.width(), self.height(), dpr)
            if not self._cache or self._cache[0] != ck:
                self._cache = (ck, _cover(pm, self.width(), self.height(), dpr))
            p.drawPixmap(0, 0, self._cache[1])
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor(theme.RAISED))
            g.setColorAt(1, QColor("#2a1f45"))
            p.fillRect(r, QBrush(g))
        p.setClipping(False)
        p.setPen(QColor(255, 255, 255, 30))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)
        p.end()


class BackdropHeader(QWidget):
    """Full-width backdrop, faded into the page so text on top stays readable."""

    def __init__(self, loader: ImageLoader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.key: str | None = None
        self._cache: tuple | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        loader.loaded.connect(self._on_loaded)

    def set_key(self, key: str | None) -> None:
        if key != self.key:
            self.key = key
            self._cache = None
            self.update()

    def _on_loaded(self, key: str) -> None:
        if key == self.key:
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r, QColor(theme.BG))
        pm = self.loader.get(self.key)
        if pm is not None and not pm.isNull():
            dpr = self.devicePixelRatioF()
            ck = (pm.cacheKey(), self.width(), self.height(), dpr)
            if not self._cache or self._cache[0] != ck:
                self._cache = (ck, _cover(pm, self.width(), self.height(), dpr))
            p.setOpacity(0.55)
            p.drawPixmap(0, 0, self._cache[1])
            p.setOpacity(1.0)
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor("#1c1430"))
            g.setColorAt(1, QColor(theme.BG))
            p.fillRect(r, QBrush(g))
        side = QLinearGradient(r.left(), 0, r.right(), 0)
        side.setColorAt(0.0, QColor(13, 10, 20, 245))
        side.setColorAt(0.45, QColor(13, 10, 20, 170))
        side.setColorAt(1.0, QColor(13, 10, 20, 40))
        p.fillRect(r, QBrush(side))
        bottom = QLinearGradient(0, r.bottom() - r.height() * 0.45, 0, r.bottom())
        bottom.setColorAt(0, QColor(13, 10, 20, 0))
        bottom.setColorAt(1, QColor(13, 10, 20, 255))
        p.fillRect(r, QBrush(bottom))
        p.end()
