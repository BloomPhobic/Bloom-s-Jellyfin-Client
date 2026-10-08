"""Home: library tiles, Continue Watching, Next Up, Recently Added per library,
Watch History and Favourites.

Each row loads in its own worker and appears as soon as its data arrives
(skeleton cards until then). Empty rows hide themselves. Right-click toggles
played/favourite with an optimistic update that is reverted if the server
says no.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..api import library
from ..api.models import Item
from ..session import Session
from .nav import Page
from .widgets.cards import CardRow, CardStyle
from .widgets.image_loader import ImageLoader
from .workers import run_async

log = logging.getLogger(__name__)

STALE_AFTER = 30.0      # seconds; Home refreshes when shown again after this


class HomePage(Page):
    title = "Home"
    nav_key = "home"
    persistent = True

    open_item = Signal(object)        # Item
    open_view = Signal(object)        # library Item
    views_loaded = Signal(list)       # list[Item]
    toast = Signal(str, bool)         # message, is_error

    def __init__(self, session: Session, loader: ImageLoader, parent=None):
        super().__init__(parent)
        self.session = session
        self.loader = loader
        self.setObjectName("root")
        self._gen = 0
        self._loaded_at = 0.0
        self._latest_rows: dict[str, CardRow] = {}
        self._failures = 0
        self._pending = 0

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(1200)
        self._refresh_timer.timeout.connect(lambda: self.load(initial=False))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().setSingleStep(40)
        outer.addWidget(self.scroll)

        content = QWidget()
        content.setObjectName("root")
        self.col = QVBoxLayout(content)
        self.col.setContentsMargins(22, 18, 22, 28)
        self.col.setSpacing(22)
        self.scroll.setWidget(content)

        self.error_banner = self._make_error_banner()
        self.col.addWidget(self.error_banner)

        self.row_libraries = self._row("Libraries", CardStyle.LIBRARY, wire=False)
        self.row_libraries.item_clicked.connect(self.open_view)
        self.row_resume = self._row("Continue Watching", CardStyle.LANDSCAPE)
        self.row_next = self._row("Next Up", CardStyle.LANDSCAPE)
        self.latest_box = QVBoxLayout()
        self.latest_box.setSpacing(22)
        self.col.addLayout(self.latest_box)
        self.row_history = self._row("Watch History", CardStyle.LANDSCAPE)
        self.row_favs = self._row("Favourites", CardStyle.POSTER)
        self.col.addStretch()

    # ------------------------------------------------------------------ building
    def _row(self, title: str, style: CardStyle, box: QVBoxLayout | None = None,
             wire: bool = True) -> CardRow:
        row = CardRow(title, style, self.loader)
        if wire:
            row.item_clicked.connect(self.open_item)
            row.item_menu.connect(self._show_menu)
        (box or self.col).addWidget(row)
        return row

    def _make_error_banner(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self.error_label = QLabel("")
        self.error_label.setProperty("role", "notice")
        retry = QPushButton("Retry")
        retry.setCursor(Qt.CursorShape.PointingHandCursor)
        retry.clicked.connect(lambda: self.load(initial=True))
        h.addWidget(self.error_label, 1)
        h.addWidget(retry)
        w.hide()
        return w

    def _all_rows(self) -> list[CardRow]:
        return [self.row_libraries, self.row_resume, self.row_next, *self._latest_rows.values(),
                self.row_history, self.row_favs]

    # ------------------------------------------------------------------ loading
    def clear(self) -> None:
        """Forget everything (user switched)."""
        self._gen += 1
        self._loaded_at = 0.0
        for row in self._latest_rows.values():
            row.deleteLater()
        self._latest_rows.clear()
        for row in self._all_rows():
            row.model.set_loading()
            row.show()
        self.scroll.verticalScrollBar().setValue(0)

    def page_shown(self) -> None:
        if not self._loaded_at:
            self.load(initial=True)
        elif time.monotonic() - self._loaded_at > STALE_AFTER:
            self.load(initial=False)

    def refresh_soon(self) -> None:
        """Debounced refresh, e.g. after toggles or playback."""
        self._refresh_timer.start()

    def load(self, initial: bool = True) -> None:
        self._gen += 1
        gen = self._gen
        self._loaded_at = time.monotonic()
        self._failures = 0
        self._pending = 0
        self.error_banner.hide()
        if initial:
            for row in self._all_rows():
                row.model.set_loading()
                row.show()

        c = self.session.client
        self._fetch(gen, self.row_resume, lambda: library.resume(c))
        self._fetch(gen, self.row_next, lambda: library.next_up(c))
        self._fetch(gen, self.row_history, lambda: library.history(c))
        self._fetch(gen, self.row_favs, lambda: library.favourites(c))
        self._pending += 1
        run_async(lambda: library.views(c),
                  lambda views: self._views_done(gen, views),
                  lambda e: self._row_failed(gen, self.row_libraries, e))

    def _fetch(self, gen: int, row: CardRow, fn: Callable[[], list[Item]]) -> None:
        self._pending += 1
        run_async(fn, lambda items: self._row_done(gen, row, items),
                  lambda e: self._row_failed(gen, row, e))

    def _row_done(self, gen: int, row: CardRow, items: list[Item]) -> None:
        if gen != self._gen:
            return
        self._pending -= 1
        # keep horizontal scroll position across refreshes
        sb = row.view.horizontalScrollBar()
        pos = sb.value()
        row.model.set_items(items)
        row.setVisible(bool(items))
        sb.setValue(pos)

    def _row_failed(self, gen: int, row: CardRow, e: Exception) -> None:
        if gen != self._gen:
            return
        self._pending -= 1
        self._failures += 1
        log.warning("home row %r failed: %s", row.title.text(), e)
        if row.model.loading:
            row.hide()
        if self._failures >= 3 and self._pending <= 0:
            self.error_label.setText(f"Couldn't load your home screen: {e}")
            self.error_banner.show()

    def _views_done(self, gen: int, views: list[Item]) -> None:
        if gen != self._gen:
            return
        self._row_done(gen, self.row_libraries, views)
        self.views_loaded.emit(views)
        wanted = [v for v in views if library.wants_latest_row(v)]
        wanted_ids = {v.id for v in wanted}
        for vid in list(self._latest_rows):
            if vid not in wanted_ids:
                self._latest_rows.pop(vid).deleteLater()
        c = self.session.client
        for v in wanted:
            row = self._latest_rows.get(v.id)
            if row is None:
                style = CardStyle.POSTER
                row = self._row(f"Recently Added in {v.name}", style, self.latest_box)
                self._latest_rows[v.id] = row
            else:
                row.set_title(f"Recently Added in {v.name}")
            self._fetch(gen, row, lambda vid=v.id: library.latest(c, vid))

    # ------------------------------------------------------------------ played / favourite
    def _show_menu(self, item: Item, pos: QPoint) -> None:
        menu = QMenu(self)
        ud = item.user_data
        open_act = QAction("Open", menu)
        open_act.triggered.connect(lambda: self.open_item.emit(item))
        played = QAction("Mark as unwatched" if ud.played else "Mark as watched", menu)
        played.triggered.connect(lambda: self.toggle_played(item))
        fav = QAction("Remove from Favourites" if ud.is_favorite else "Add to Favourites", menu)
        fav.triggered.connect(lambda: self.toggle_favorite(item))
        menu.addAction(open_act)
        menu.addSeparator()
        menu.addAction(played)
        menu.addAction(fav)
        menu.exec(pos)

    def _apply_everywhere(self, item_id: str, fn: Callable[[Item], None]) -> None:
        for row in self._all_rows():
            row.model.update_where(item_id, fn)

    def toggle_played(self, item: Item) -> None:
        new = not item.user_data.played
        old_pos = item.user_data.playback_position_ticks
        old_pct = item.user_data.played_percentage

        def apply(it: Item) -> None:
            it.user_data.played = new
            if new:
                it.user_data.playback_position_ticks = 0
                it.user_data.played_percentage = None

        def revert(it: Item) -> None:
            it.user_data.played = not new
            it.user_data.playback_position_ticks = old_pos
            it.user_data.played_percentage = old_pct

        self._optimistic(item, apply, revert,
                         lambda: library.set_played(self.session.client, item.id, new),
                         f"Marked “{item.name}” as {'watched' if new else 'unwatched'}")

    def toggle_favorite(self, item: Item) -> None:
        new = not item.user_data.is_favorite

        def apply(it: Item) -> None:
            it.user_data.is_favorite = new

        def revert(it: Item) -> None:
            it.user_data.is_favorite = not new

        self._optimistic(item, apply, revert,
                         lambda: library.set_favorite(self.session.client, item.id, new),
                         f"{'Added' if new else 'Removed'} “{item.name}” "
                         f"{'to' if new else 'from'} Favourites")

    def _optimistic(self, item: Item, apply: Callable[[Item], None], revert: Callable[[Item], None],
                    call: Callable[[], object], ok_text: str) -> None:
        self._apply_everywhere(item.id, apply)

        def done(_r: object) -> None:
            self.toast.emit(ok_text, False)
            self.refresh_soon()     # rows like Continue Watching may need to reshuffle

        def failed(e: Exception) -> None:
            self._apply_everywhere(item.id, revert)
            self.toast.emit(f"Couldn't update “{item.name}”: {e}", True)

        run_async(call, done, failed)
