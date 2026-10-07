"""Circular user avatars: the server image, or a letter on a purple gradient."""
from __future__ import annotations

import zlib

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import QAbstractButton, QWidget

from ...api.auth import user_image_path
from ...api.models import User
from .. import theme
from .image_loader import ImageLoader

# Gradient pairs for letter avatars, all within the purple family.
_GRADIENTS = [
    ("#7c3aed", "#c084fc"),
    ("#6d28d9", "#a78bfa"),
    ("#5b21b6", "#e879f9"),
    ("#7e22ce", "#f0abfc"),
    ("#4c1d95", "#8b5cf6"),
]


def paint_avatar(p: QPainter, rect: QRectF, name: str, pixmap: QPixmap | None) -> None:
    path = QPainterPath()
    path.addEllipse(rect)
    p.save()
    p.setClipPath(path)
    if pixmap is not None and not pixmap.isNull():
        scaled = pixmap.scaled(QSize(int(rect.width()), int(rect.height())),
                               Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        x = rect.x() + (rect.width() - scaled.width()) / 2
        y = rect.y() + (rect.height() - scaled.height()) / 2
        p.drawPixmap(int(x), int(y), scaled)
    else:
        a, b = _GRADIENTS[zlib.crc32(name.encode("utf-8")) % len(_GRADIENTS)]
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(a))
        grad.setColorAt(1.0, QColor(b))
        p.fillRect(rect, QBrush(grad))
        f = QFont(p.font())
        f.setPixelSize(max(12, int(rect.height() * 0.42)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, (name[:1] or "?").upper())
    p.restore()


class AvatarTile(QAbstractButton):
    """A clickable profile: avatar circle with the name underneath."""

    DIAMETER = 112

    def __init__(self, user: User, loader: ImageLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self.user = user
        self.loader = loader
        self.key = user_image_path(user)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(150, 176)
        self.setToolTip(f"Sign in as {user.name}")
        self.setAccessibleName(user.name)
        loader.loaded.connect(self._on_loaded)

    def _on_loaded(self, key: str) -> None:
        if key == self.key:
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(150, 176)

    def enterEvent(self, e) -> None:
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        d = self.DIAMETER
        hover = self.underMouse()
        grow = 4 if hover or self.hasFocus() else 0
        dd = d + grow
        rect = QRectF((self.width() - dd) / 2, 10 - grow / 2, dd, dd)

        paint_avatar(p, rect, self.user.name, self.loader.get(self.key))

        if self.isChecked() or hover or self.hasFocus():
            color = QColor(theme.ACCENT if self.isChecked() else theme.ACCENT_HOVER)
            if not self.isChecked():
                color.setAlpha(170)
            p.setPen(QPen(color, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(rect.adjusted(-5, -5, 5, 5))

        f = QFont(self.font())
        f.setPixelSize(15)
        f.setBold(self.isChecked())
        p.setFont(f)
        p.setPen(QColor(theme.TEXT if (self.isChecked() or hover) else theme.MUTED))
        name_rect = QRectF(4, 10 + d + 14, self.width() - 8, 26)
        text = p.fontMetrics().elidedText(self.user.name, Qt.TextElideMode.ElideRight, int(name_rect.width()))
        p.drawText(name_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
        p.end()
