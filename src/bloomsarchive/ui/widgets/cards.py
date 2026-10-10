"""Poster/thumb cards: a list model, a painted delegate, and a horizontal row view.

One QListView per row, painted by a delegate: no widget per poster, so rows
of hundreds stay cheap. Images load lazily, only when a card is painted.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from enum import Enum

from PySide6.QtCore import (QAbstractListModel, QEasingCurve, QModelIndex, QPersistentModelIndex,
                            QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListView, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QVBoxLayout,
                               QWidget)

from ...api import images
from ...api.models import Item
from ...display import card_text, progress_fraction, unplayed_badge
from .. import theme
from .image_loader import ImageLoader
from .smooth import SmoothScroller, is_touchpad
from .smooth import install as smooth_install

log = logging.getLogger(__name__)

ItemRole = Qt.ItemDataRole.UserRole + 1
KeyRole = Qt.ItemDataRole.UserRole + 2

PAD = 7          # room around each card for the hover zoom
TEXT_H = 46
RADIUS = 10


class CardStyle(Enum):
    POSTER = (150, 225, True)       # width, image height, has text below
    LANDSCAPE = (272, 153, True)
    LIBRARY = (272, 153, False)

    @property
    def width(self) -> int:
        return self.value[0]

    @property
    def image_height(self) -> int:
        return self.value[1]

    @property
    def has_text(self) -> bool:
        return self.value[2]

    def size(self) -> QSize:
        return QSize(self.width + 2 * PAD, self.image_height + (TEXT_H if self.has_text else 0) + 2 * PAD)

    def key_for(self, item: Item) -> str | None:
        if self is CardStyle.POSTER:
            return images.poster(item)
        return images.landscape(item)


# =========================================================================== model

class ItemListModel(QAbstractListModel):
    def __init__(self, style: CardStyle, loader: ImageLoader, skeletons: int = 8, parent=None):
        super().__init__(parent)
        self.style = style
        self.loader = loader
        self.skeletons = skeletons
        self.loading = True
        self.items: list[Item] = []
        self._keys: list[str | None] = []
        self._rows_by_key: dict[str, list[int]] = {}
        loader.loaded.connect(self._on_image)

    # -- qt api
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return self.skeletons if self.loading else len(self.items)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.loading:
            return None
        r = index.row()
        if r >= len(self.items):
            return None
        if role == ItemRole:
            return self.items[r]
        if role == KeyRole:
            return self._keys[r]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole):
            title, sub = card_text(self.items[r])
            return f"{title} — {sub}" if sub else title
        return None

    # -- content
    def set_loading(self) -> None:
        self.beginResetModel()
        self.loading = True
        self.items, self._keys, self._rows_by_key = [], [], {}
        self.endResetModel()

    def set_items(self, items: list[Item]) -> None:
        self.beginResetModel()
        self.loading = False
        self.items = list(items)
        self._keys = [self.style.key_for(i) for i in self.items]
        self._rows_by_key = {}
        for row, k in enumerate(self._keys):
            if k:
                self._rows_by_key.setdefault(k, []).append(row)
        self.endResetModel()

    def update_where(self, item_id: str, fn) -> int:
        """Apply fn(item) to every row showing item_id; returns how many changed."""
        n = 0
        for row, it in enumerate(self.items):
            if it.id == item_id:
                fn(it)
                idx = self.index(row)
                self.dataChanged.emit(idx, idx)
                n += 1
        return n

    def _on_image(self, key: str) -> None:
        for row in self._rows_by_key.get(key, []):
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)


# =========================================================================== delegate

class CardDelegate(QStyledItemDelegate):
    def __init__(self, style: CardStyle, loader: ImageLoader, parent=None):
        super().__init__(parent)
        self.style = style
        self.loader = loader
        self._scaled: OrderedDict[tuple, QPixmap] = OrderedDict()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return self.style.size()

    # -- geometry (shared by paint and hit-testing)
    def image_rect(self, cell) -> QRectF:
        """The card image, centred horizontally in its cell (grid cells can be wider)."""
        st = self.style
        x0 = cell.x() + (cell.width() - st.width) / 2
        return QRectF(x0, cell.y() + PAD, st.width, st.image_height)

    def icon_rects(self, cell) -> tuple[QRectF, QRectF] | None:
        """(watched, favourite) hover buttons at the image's bottom-right, or None."""
        if self.style is CardStyle.LIBRARY:
            return None
        img = self.image_rect(cell)
        size, gap, margin = 30.0, 6.0, 8.0
        fav = QRectF(img.right() - margin - size, img.bottom() - margin - size - 6, size, size)
        played = QRectF(fav.left() - gap - size, fav.top(), size, size)
        return played, fav

    def hit_icon(self, cell, pos) -> str | None:
        rects = self.icon_rects(cell)
        if not rects:
            return None
        if rects[0].contains(pos):
            return "played"
        if rects[1].contains(pos):
            return "favorite"
        return None

    # -- helpers
    def _cover(self, pm: QPixmap, w: int, h: int, dpr: float) -> QPixmap:
        """Scale-to-fill + centre-crop, cached per size."""
        k = (pm.cacheKey(), w, h, dpr)
        hit = self._scaled.get(k)
        if hit is not None:
            self._scaled.move_to_end(k)
            return hit
        tw, th = max(1, int(w * dpr)), max(1, int(h * dpr))
        scaled = pm.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
        x = max(0, (scaled.width() - tw) // 2)
        y = max(0, (scaled.height() - th) // 2)
        out = scaled.copy(x, y, tw, th)
        out.setDevicePixelRatio(dpr)
        self._scaled[k] = out
        while len(self._scaled) > 400:
            self._scaled.popitem(last=False)
        return out

    @staticmethod
    def _font(base: QFont, px: int, bold: bool = False) -> QFont:
        f = QFont(base)
        f.setPixelSize(px)
        f.setBold(bold)
        return f

    def _draw_icon_button(self, p: QPainter, r: QRectF, kind: str, on: bool) -> None:
        p.save()
        p.setPen(QPen(QColor(theme.ACCENT_HOVER if on else theme.BORDER), 1))
        p.setBrush(QColor(theme.ACCENT) if (on and kind == "played") else QColor(13, 10, 20, 215))
        p.drawEllipse(r)
        cx, cy = r.center().x(), r.center().y()
        if kind == "played":
            p.setPen(QPen(QColor(theme.TEXT if on else theme.MUTED), 2.2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath()
            path.moveTo(cx - 6, cy + 0.5)
            path.lineTo(cx - 2, cy + 4.5)
            path.lineTo(cx + 6, cy - 4.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        else:
            p.setPen(QColor("#f472b6" if on else theme.MUTED))
            f = QFont(p.font())
            f.setPixelSize(16)
            p.setFont(f)
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "♥" if on else "♡")
        p.restore()

    # -- paint
    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        p.save()
        try:
            self._paint(p, option, index)
        except Exception:      # never let one bad card take the row down
            log.exception("card paint failed")
        finally:
            p.restore()

    def _paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        st = self.style
        img = self.image_rect(option.rect)
        x0, y0 = img.x(), img.y()
        w, ih = st.width, st.image_height
        item: Item | None = index.data(ItemRole)

        if item is None:                                   # skeleton
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.RAISED))
            p.drawRoundedRect(img, RADIUS, RADIUS)
            if st.has_text:
                p.setBrush(QColor(theme.SURFACE))
                p.drawRoundedRect(QRectF(x0, y0 + ih + 10, w * 0.75, 11), 4, 4)
                p.drawRoundedRect(QRectF(x0, y0 + ih + 28, w * 0.45, 9), 4, 4)
            return

        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if hover:
            dx, dy = w * 0.025, ih * 0.025
            img = img.adjusted(-dx, -dy, dx, dy)

        path = QPainterPath()
        path.addRoundedRect(img, RADIUS, RADIUS)
        title, subtitle = card_text(item)

        # ---- image or placeholder
        pm = self.loader.get(index.data(KeyRole))
        p.save()
        p.setClipPath(path)
        if pm is not None and not pm.isNull():
            dpr = p.device().devicePixelRatioF() if p.device() else 1.0
            p.drawPixmap(img.topLeft(), self._cover(pm, int(img.width()), int(img.height()), dpr))
        else:
            grad = QLinearGradient(img.topLeft(), img.bottomRight())
            grad.setColorAt(0, QColor(theme.RAISED))
            grad.setColorAt(1, QColor("#2a1f45" if st is CardStyle.LIBRARY else theme.SURFACE))
            p.fillRect(img, QBrush(grad))
            if st is not CardStyle.LIBRARY:
                p.setPen(QColor(theme.MUTED))
                p.setFont(self._font(option.font, 13, True))
                p.drawText(img.adjusted(10, 10, -10, -10),
                           int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap), title)

        if st is CardStyle.LIBRARY:                       # name over a bottom fade
            fade = QLinearGradient(img.left(), img.bottom() - img.height() * 0.6, img.left(), img.bottom())
            fade.setColorAt(0, QColor(13, 10, 20, 0))
            fade.setColorAt(1, QColor(13, 10, 20, 225))
            p.fillRect(img, QBrush(fade))
            p.setPen(QColor(theme.TEXT))
            p.setFont(self._font(option.font, 18, True))
            p.drawText(img.adjusted(16, 0, -16, -12),
                       int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignBottom), title)

        # ---- progress bar
        frac = progress_fraction(item)
        if frac > 0:
            bar = QRectF(img.left(), img.bottom() - 5, img.width(), 5)
            p.fillRect(bar, QColor(0, 0, 0, 170))
            p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * frac, bar.height()), QColor(theme.ACCENT))
        p.restore()

        # ---- badges (top-right): played tick or unplayed count; favourite heart top-left
        badge_font = self._font(option.font, 12, True)
        if item.user_data.played and st is not CardStyle.LIBRARY:
            c = QRectF(img.right() - 30, img.top() + 8, 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(c)
            p.setPen(QPen(QColor(theme.TEXT), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            cx, cy = c.center().x(), c.center().y()
            tick = QPainterPath()
            tick.moveTo(cx - 5, cy)
            tick.lineTo(cx - 1.5, cy + 3.5)
            tick.lineTo(cx + 5, cy - 4)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(tick)
        else:
            count = unplayed_badge(item)
            if count:
                p.setFont(badge_font)
                tw = p.fontMetrics().horizontalAdvance(count) + 14
                c = QRectF(img.right() - tw - 8, img.top() + 8, max(22, tw), 22)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.ACCENT))
                p.drawRoundedRect(c, 11, 11)
                p.setPen(QColor(theme.TEXT))
                p.drawText(c, int(Qt.AlignmentFlag.AlignCenter), count)
        if item.user_data.is_favorite and st is not CardStyle.LIBRARY:
            c = QRectF(img.left() + 8, img.top() + 8, 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(13, 10, 20, 190))
            p.drawEllipse(c)
            p.setPen(QColor("#f472b6"))
            p.setFont(self._font(option.font, 13, True))
            p.drawText(c, int(Qt.AlignmentFlag.AlignCenter), "♥")

        # ---- hover ring
        if hover:
            p.setPen(QPen(QColor(theme.ACCENT_HOVER), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(img.adjusted(1, 1, -1, -1), RADIUS, RADIUS)

        # ---- hover buttons: watched / favourite
        rects = self.icon_rects(option.rect) if hover else None
        if rects:
            self._draw_icon_button(p, rects[0], "played", item.user_data.played)
            self._draw_icon_button(p, rects[1], "favorite", item.user_data.is_favorite)

        # ---- text below
        if st.has_text:
            tr = QRectF(x0, y0 + st.image_height + 8, w, 20)
            p.setFont(self._font(option.font, 14, True))
            p.setPen(QColor(theme.TEXT))
            p.drawText(tr, int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignVCenter),
                       p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, w))
            if subtitle:
                sr = QRectF(x0, tr.bottom() + 1, w, 18)
                p.setFont(self._font(option.font, 12))
                p.setPen(QColor(theme.MUTED))
                p.drawText(sr, int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignVCenter),
                           p.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight, w))


# =========================================================================== views

class CardViewBase(QListView):
    """Shared setup: painted cards, hover, click, right-click, hover ✓/♥ buttons."""

    item_clicked = Signal(object)              # Item
    item_menu = Signal(object, QPoint)         # Item, global position
    toggle_played = Signal(object)             # Item
    toggle_favorite = Signal(object)           # Item

    def __init__(self, model: QAbstractListModel, delegate: "CardDelegate", parent=None):
        super().__init__(parent)
        self.card_delegate = delegate
        self.setModel(model)
        self.setItemDelegate(delegate)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.clicked.connect(self._on_click)
        self.customContextMenuRequested.connect(self._on_menu)

    def _item_at(self, pos: QPoint) -> Item | None:
        idx = self.indexAt(pos)
        return idx.data(ItemRole) if idx.isValid() else None

    def _on_click(self, idx: QModelIndex) -> None:
        item = idx.data(ItemRole)
        if item is not None:
            self.item_clicked.emit(item)

    def _on_menu(self, pos: QPoint) -> None:
        item = self._item_at(pos)
        if item is not None:
            self.item_menu.emit(item, self.viewport().mapToGlobal(pos))

    def _icon_hit(self, pos: QPoint) -> tuple[Item, str] | None:
        idx = self.indexAt(pos)
        item = idx.data(ItemRole) if idx.isValid() else None
        if item is None:
            return None
        kind = self.card_delegate.hit_icon(self.visualRect(idx), QPointF(pos))
        return (item, kind) if kind else None

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._icon_hit(e.position().toPoint()):
            e.accept()          # handled on release; don't start a click on the card
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            hit = self._icon_hit(e.position().toPoint())
            if hit:
                item, kind = hit
                (self.toggle_played if kind == "played" else self.toggle_favorite).emit(item)
                e.accept()
                return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e) -> None:
        self.viewport().update()
        super().leaveEvent(e)


class CardListView(CardViewBase):
    """A single horizontal row of cards."""

    def __init__(self, model: ItemListModel, delegate: "CardDelegate", parent=None):
        super().__init__(model, delegate, parent)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.horizontalScrollBar().setSingleStep(48)
        self.setFixedHeight(model.style.size().height() + 2)
        self.smooth = SmoothScroller(self.horizontalScrollBar(), parent=self)

    def wheelEvent(self, e) -> None:
        d = e.angleDelta()
        sideways = abs(d.x()) > abs(d.y())
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if sideways and is_touchpad(e):            # trackpad swipe: follow the finger
            sb = self.horizontalScrollBar()
            px = e.pixelDelta().x() or d.x()
            sb.setValue(sb.value() - int(px))
            e.accept()
        elif sideways or shift:                     # tilt wheel / Shift+wheel: animate
            delta = d.x() if sideways else d.y()
            self.smooth.scroll_by(-delta / 120 * 260)
            e.accept()
        else:
            e.ignore()       # let the page scroll vertically


class CardGridView(CardViewBase):
    """A wrapping, virtualised poster grid whose columns stretch to fill the width."""

    def __init__(self, model: QAbstractListModel, delegate: "CardDelegate", parent=None):
        super().__init__(model, delegate, parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalScrollBar().setSingleStep(60)
        self.smooth = smooth_install(self)
        self._fit_grid()

    def _fit_grid(self) -> None:
        card = self.card_delegate.style.size()
        avail = max(card.width(), self.viewport().width() - 2)
        cols = max(1, avail // card.width())
        self.setGridSize(QSize(avail // cols, card.height() + 6))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_grid()

    def scroll_to_row(self, row: int) -> None:
        model = self.model()
        if model is None or row >= model.rowCount():
            return
        rect = self.visualRect(model.index(row, 0))
        target = self.verticalScrollBar().value() + rect.top()
        self.smooth.vertical.scroll_to(max(0, min(target, self.verticalScrollBar().maximum())))


class CardRow(QWidget):
    """Title + horizontal card list + ‹ › arrows that appear on hover."""

    item_clicked = Signal(object)
    item_menu = Signal(object, QPoint)
    toggle_played = Signal(object)
    toggle_favorite = Signal(object)

    def __init__(self, title: str, style: CardStyle, loader: ImageLoader, parent=None):
        super().__init__(parent)
        self.model = ItemListModel(style, loader, parent=self)
        self.delegate = CardDelegate(style, loader, parent=self)
        self.view = CardListView(self.model, self.delegate)
        self.view.item_clicked.connect(self.item_clicked)
        self.view.item_menu.connect(self.item_menu)
        self.view.toggle_played.connect(self.toggle_played)
        self.view.toggle_favorite.connect(self.toggle_favorite)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        head = QHBoxLayout()
        head.setContentsMargins(PAD, 0, 0, 0)
        self.title = QLabel(title)
        self.title.setProperty("role", "rowtitle")
        head.addWidget(self.title)
        head.addStretch()
        v.addLayout(head)
        v.addWidget(self.view)

        self.left = self._arrow("‹", -1)
        self.right = self._arrow("›", +1)
        self._anim = QPropertyAnimation(self.view.horizontalScrollBar(), b"value", self)
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.view.horizontalScrollBar().valueChanged.connect(self._on_scrolled)

    def _arrow(self, text: str, direction: int) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setProperty("role", "rowarrow")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedSize(40, 40)
        b.hide()
        b.clicked.connect(lambda: self.scroll_page(direction))
        return b

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def scroll_page(self, direction: int) -> None:
        sb = self.view.horizontalScrollBar()
        step = int(self.view.viewport().width() * 0.85)
        target = max(sb.minimum(), min(sb.maximum(), sb.value() + direction * step))
        self._anim.stop()
        self._anim.setStartValue(sb.value())
        self._anim.setEndValue(target)
        self._anim.start()

    def _place_arrows(self) -> None:
        g = self.view.geometry()
        y = g.y() + (self.model.style.image_height + 2 * PAD) // 2 - 20
        self.left.move(g.x() + 4, y)
        self.right.move(g.right() - 44, y)

    def _update_arrows(self, visible: bool) -> None:
        sb = self.view.horizontalScrollBar()
        self._place_arrows()
        self.left.setVisible(visible and sb.value() > sb.minimum())
        self.right.setVisible(visible and sb.value() < sb.maximum())
        self.left.raise_()
        self.right.raise_()

    def enterEvent(self, e) -> None:
        self._update_arrows(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._update_arrows(False)
        super().leaveEvent(e)

    def _on_scrolled(self, _v: int) -> None:
        self._update_arrows(self.underMouse())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_arrows()
