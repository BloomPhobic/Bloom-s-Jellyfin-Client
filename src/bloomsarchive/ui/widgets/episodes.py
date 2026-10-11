"""Episode list for series pages: thumbnail (with progress and a hover ▶), number,
title, runtime, two lines of overview, and a watched toggle on the right."""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QListView, QStyle, QStyleOptionViewItem

from ...api.models import Item
from ...display import progress_fraction
from .. import theme
from .cards import PULSE_SECONDS, CardDelegate, CardStyle, CardViewBase, ItemListModel, ItemRole, KeyRole
from .image_loader import ImageLoader

ROW_H = 128
THUMB_W, THUMB_H = 192, 108
BTN = 34.0


def runtime_text(item: Item) -> str:
    mins = int(round(item.runtime_seconds / 60)) if item.run_time_ticks else 0
    if not mins:
        return ""
    return f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins} min"


class EpisodeDelegate(CardDelegate):
    def __init__(self, loader: ImageLoader, parent=None):
        super().__init__(CardStyle.LANDSCAPE, loader, parent)

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return QSize(640, ROW_H)

    def image_rect(self, cell) -> QRectF:
        return QRectF(cell.x() + 12, cell.y() + (ROW_H - THUMB_H) / 2, THUMB_W, THUMB_H)

    def icon_rects(self, cell) -> tuple[QRectF, QRectF] | None:
        played = QRectF(cell.x() + cell.width() - 16 - BTN, cell.y() + (ROW_H - BTN) / 2, BTN, BTN)
        return played, QRectF()          # no favourite button on episode rows

    def hit_icon(self, cell, pos) -> str | None:
        played, _ = self.icon_rects(cell)
        if played.contains(pos):
            return "played"
        if self.image_rect(cell).contains(pos):
            return "play"
        return None

    def _paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cell = option.rect
        item: Item | None = index.data(ItemRole)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        row = QRectF(cell).adjusted(2, 3, -2, -3)

        if hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(31, 23, 48, 200))
            p.drawRoundedRect(row, 12, 12)

        img = self.image_rect(cell)
        if item is None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.RAISED))
            p.drawRoundedRect(img, 10, 10)
            return

        # ---- thumbnail
        path = QPainterPath()
        path.addRoundedRect(img, 10, 10)
        p.save()
        p.setClipPath(path)
        pm = self.loader.get(index.data(KeyRole))
        if pm is not None and not pm.isNull():
            dpr = p.device().devicePixelRatioF() if p.device() else 1.0
            p.drawPixmap(img.topLeft(), self._cover(pm, int(img.width()), int(img.height()), dpr))
        else:
            p.fillRect(img, QColor(theme.RAISED))
        frac = progress_fraction(item)
        if frac > 0:
            bar = QRectF(img.left(), img.bottom() - 5, img.width(), 5)
            p.fillRect(bar, QColor(0, 0, 0, 170))
            p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * frac, 5), QColor(theme.ACCENT))
        hot_play = self.hot == (item.id, "play")
        if hover:
            p.fillRect(img, QColor(10, 7, 18, 110 if hot_play else 70))
        p.restore()
        if hover:                                          # ▶ over the thumbnail
            c = img.center()
            r = 22 if hot_play else 19
            p.setPen(QPen(QColor(255, 255, 255, 120), 1))
            p.setBrush(QColor(theme.ACCENT) if hot_play else QColor(20, 14, 32, 200))
            p.drawEllipse(c, r, r)
            tri = QPainterPath()
            tri.moveTo(c.x() - 5, c.y() - 8)
            tri.lineTo(c.x() + 9, c.y())
            tri.lineTo(c.x() - 5, c.y() + 8)
            tri.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255))
            p.drawPath(tri)

        # ---- watched button (always visible on rows)
        played_rect, _ = self.icon_rects(cell)
        started = self.pulses.get((item.id, "played"))
        pulse = (time.monotonic() - started) / PULSE_SECONDS if started is not None else None
        if pulse is not None and pulse >= 1:
            pulse = None
        self._draw_icon_button(p, played_rect, "played", item.user_data.played,
                               self.hot == (item.id, "played"), pulse)

        # ---- text
        x = img.right() + 18
        w = played_rect.left() - 18 - x
        f = QFont(option.font)
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT if not item.user_data.played else theme.MUTED))
        num = f"{item.index_number}. " if item.index_number is not None else ""
        title_rect = QRectF(x, img.top() + 2, w, 22)
        p.drawText(title_rect, int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignVCenter),
                   p.fontMetrics().elidedText(num + item.name, Qt.TextElideMode.ElideRight, int(w)))

        meta = [b for b in (runtime_text(item), (item.premiere_date or "")[:10]) if b]
        if item.user_data.played:
            meta.append("watched")
        elif frac > 0:
            meta.append(f"{int(frac * 100)}% watched")
        f.setPixelSize(12)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(x, title_rect.bottom() + 1, w, 18),
                   int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignVCenter), " · ".join(meta))

        if item.overview:
            f.setPixelSize(13)
            p.setFont(f)
            p.setPen(QColor(200, 194, 220))
            ov_rect = QRectF(x, title_rect.bottom() + 24, w, 40)
            text = " ".join(item.overview.split())
            fm = p.fontMetrics()
            # two lines max, ellipsis on the second
            cut = len(text)
            while cut > 0 and fm.horizontalAdvance(text[:cut]) > w:
                cut = text.rfind(" ", 0, cut)
            first = text[:cut] if cut > 0 else ""
            second = fm.elidedText(text[len(first):].strip(), Qt.TextElideMode.ElideRight, int(w))
            p.drawText(QRectF(ov_rect.x(), ov_rect.y(), w, 20),
                       int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignVCenter), first)
            p.drawText(QRectF(ov_rect.x(), ov_rect.y() + 19, w, 20),
                       int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignVCenter), second)


class EpisodeListView(CardViewBase):
    """Vertical episode list that grows to fit (the page scrolls, not the list)."""

    def __init__(self, loader: ImageLoader, parent=None):
        model = ItemListModel(CardStyle.LANDSCAPE, loader, skeletons=4)
        super().__init__(model, EpisodeDelegate(loader), parent)
        self.list_model = model
        self.list_model.setParent(self)
        self.card_delegate.setParent(self)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_model.modelReset.connect(self._fit)
        self._fit()

    def _fit(self) -> None:
        self.setFixedHeight(max(1, self.list_model.rowCount()) * ROW_H + 4)

    def wheelEvent(self, e) -> None:
        e.ignore()          # the page scrolls
