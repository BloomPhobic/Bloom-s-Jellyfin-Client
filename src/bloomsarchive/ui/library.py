"""Library grid: filter bar, virtualised poster grid, A–Z jump bar.

Used for each library, for Favourites, and for opening a collection.
Sort and filters are remembered per library in settings.json.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..api import browse
from ..api.browse import RANDOM_LIMIT, LibraryQuery
from ..session import Session
from .actions import ItemActions
from .nav import Page
from .widgets.cards import CardDelegate, CardGridView, CardStyle
from .widgets.filterbar import FilterBar, LetterBar
from .widgets.image_loader import ImageLoader
from .widgets.paged_model import PagedItemModel
from .workers import run_async

log = logging.getLogger(__name__)


class LibraryPage(Page):
    open_item = Signal(object)

    def __init__(self, session: Session, loader: ImageLoader, actions: ItemActions, title: str,
                 base: LibraryQuery, nav_key: str, state_key: str | None,
                 show_favourites_filter: bool = True, initial: dict | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.session = session
        self.actions = actions
        self.title = title
        self.nav_key = nav_key
        self.state_key = state_key
        self._stale = False
        self._jump_gen = 0

        saved = session.settings.get(f"library_state.{state_key}") if state_key else None
        self.query = base.with_state(saved)
        if initial:                       # e.g. a genre link: start filtered, keep your sort
            self.query = replace(self.query.cleared(), **initial)

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 16, 10, 0)
        v.setSpacing(12)
        head = QHBoxLayout()
        head.setContentsMargins(7, 0, 12, 0)
        t = QLabel(title)
        t.setProperty("role", "h1")
        head.addWidget(t)
        head.addSpacing(18)
        self.filters = FilterBar(show_favourites=show_favourites_filter)
        head.addWidget(self.filters, 1)
        v.addLayout(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        self.model = PagedItemModel(CardStyle.POSTER, loader, parent=self)
        self.delegate = CardDelegate(CardStyle.POSTER, loader, parent=self)
        self.grid = CardGridView(self.model, self.delegate)
        body.addWidget(self.grid, 1)
        self.letters = LetterBar()
        body.addWidget(self.letters)
        v.addLayout(body, 1)

        self.empty = QWidget(self.grid.viewport())
        ev = QVBoxLayout(self.empty)
        self.empty_label = QLabel("")
        self.empty_label.setProperty("role", "subtitle")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_clear = QPushButton("Clear filters")
        self.empty_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.empty_clear.clicked.connect(self.filters._clear)
        ev.addStretch()
        ev.addWidget(self.empty_label)
        ev.addWidget(self.empty_clear, alignment=Qt.AlignmentFlag.AlignHCenter)
        ev.addStretch()
        self.empty.hide()

        self.filters.changed.connect(self._query_changed)
        self.letters.letter.connect(self._jump)
        self.model.total_changed.connect(self._total_changed)
        self.model.load_failed.connect(lambda msg: actions.toast.emit(f"Couldn't load {title}: {msg}", True))
        self.grid.item_clicked.connect(self.open_item)
        self.grid.item_menu.connect(lambda item, pos: actions.context_menu(item, pos, self))
        self.grid.toggle_played.connect(actions.toggle_played)
        self.grid.toggle_favorite.connect(actions.toggle_favorite)
        actions.item_changed.connect(self._item_changed)
        actions.data_changed.connect(self._mark_stale)

        self.filters.set_query(self.query)
        self.letters.set_available(self.query.letter_jump_ok)
        self._load()
        self._load_options()

    # ------------------------------------------------------------------ loading
    def _fetcher(self):
        c, q = self.session.client, self.query
        return lambda start, limit: browse.fetch_page(c, q, start, limit)

    def _load(self) -> None:
        self.empty.hide()
        self.model.reset(self._fetcher(), RANDOM_LIMIT if self.query.is_random else browse.PAGE_SIZE)
        self.grid.smooth.vertical.stop()
        self.grid.verticalScrollBar().setValue(0)

    def _load_options(self) -> None:
        c, q = self.session.client, replace(self.query)
        run_async(lambda: browse.filter_options(c, q), self.filters.set_options,
                  lambda e: log.info("filter options unavailable: %s", e))

    def _query_changed(self, q: LibraryQuery) -> None:
        self.query = q
        if self.state_key:
            self.session.settings.set(f"library_state.{self.state_key}", q.state())
        self.letters.set_available(q.letter_jump_ok)
        self._load()

    def _total_changed(self, total: int) -> None:
        self.filters.set_count(total)
        if total == 0:
            self.empty_label.setText("Nothing matches these filters." if self.query.is_filtered
                                     else "Nothing here yet.")
            self.empty_clear.setVisible(self.query.is_filtered)
            self.empty.resize(self.grid.viewport().size())
            self.empty.show()
        else:
            self.empty.hide()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.empty.isVisible():
            self.empty.resize(self.grid.viewport().size())

    # ------------------------------------------------------------------ letter jump
    def _jump(self, letter: str) -> None:
        if not self.query.letter_jump_ok:
            return
        self._jump_gen += 1
        gen = self._jump_gen
        c, q, total = self.session.client, self.query, self.model.total
        run_async(lambda: browse.letter_index(c, q, letter, total),
                  lambda row: gen == self._jump_gen and self.grid.scroll_to_row(row),
                  lambda e: self.actions.toast.emit(f"Couldn't jump to {letter}: {e}", True))

    # ------------------------------------------------------------------ updates
    def _item_changed(self, item_id: str, fn) -> None:
        self.model.update_where(item_id, fn)

    def _mark_stale(self) -> None:
        if self.isVisible():
            # Visible: cards already show the change; a soft refresh catches
            # filter effects (e.g. a now-watched title in an Unwatched view).
            self.model.refresh_loaded()
        else:
            self._stale = True

    def page_shown(self) -> None:
        if self._stale:
            self._stale = False
            self.model.refresh_loaded()
