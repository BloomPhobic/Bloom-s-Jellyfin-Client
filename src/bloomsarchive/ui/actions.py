"""Watched/favourite toggles shared by every page.

A toggle updates every visible card at once (optimistic), then calls the
server; if the server refuses, the same cards are reverted and a toast
explains. Pages listen to `item_changed` and apply the mutator to their
models; Home also listens to `data_changed` to reshuffle its rows.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QPoint, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from ..api import library
from ..api.models import Item
from ..session import Session
from .workers import run_async

Mutator = Callable[[Item], None]


class ItemActions(QObject):
    item_changed = Signal(str, object)      # item id, mutator to apply to each copy
    data_changed = Signal()                 # server state changed (after success)
    toast = Signal(str, bool)               # message, is_error
    open_requested = Signal(object)         # Item

    def __init__(self, session: Session, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session

    # ------------------------------------------------------------------ menu
    def context_menu(self, item: Item, pos: QPoint, parent: QWidget) -> None:
        menu = QMenu(parent)
        ud = item.user_data
        a_open = QAction("Open", menu)
        a_open.triggered.connect(lambda: self.open_requested.emit(item))
        a_played = QAction("Mark as unwatched" if ud.played else "Mark as watched", menu)
        a_played.triggered.connect(lambda: self.toggle_played(item))
        a_fav = QAction("Remove from Favourites" if ud.is_favorite else "Add to Favourites", menu)
        a_fav.triggered.connect(lambda: self.toggle_favorite(item))
        menu.addAction(a_open)
        menu.addSeparator()
        menu.addAction(a_played)
        menu.addAction(a_fav)
        menu.exec(pos)

    # ------------------------------------------------------------------ toggles
    def toggle_played(self, item: Item) -> None:
        new = not item.user_data.played
        old_pos = item.user_data.playback_position_ticks
        old_pct = item.user_data.played_percentage
        old_unplayed = item.user_data.unplayed_item_count

        def apply(it: Item) -> None:
            it.user_data.played = new
            if new:
                it.user_data.playback_position_ticks = 0
                it.user_data.played_percentage = None
                if it.user_data.unplayed_item_count:
                    it.user_data.unplayed_item_count = 0

        def revert(it: Item) -> None:
            it.user_data.played = not new
            it.user_data.playback_position_ticks = old_pos
            it.user_data.played_percentage = old_pct
            it.user_data.unplayed_item_count = old_unplayed

        what = "watched" if new else "unwatched"
        self._optimistic(item, apply, revert,
                         lambda: library.set_played(self.session.client, item.id, new),
                         f"Marked “{item.name}” as {what}")

    def toggle_favorite(self, item: Item) -> None:
        new = not item.user_data.is_favorite

        def apply(it: Item) -> None:
            it.user_data.is_favorite = new

        def revert(it: Item) -> None:
            it.user_data.is_favorite = not new

        msg = f"Added “{item.name}” to Favourites" if new else f"Removed “{item.name}” from Favourites"
        self._optimistic(item, apply, revert,
                         lambda: library.set_favorite(self.session.client, item.id, new), msg)

    def _optimistic(self, item: Item, apply: Mutator, revert: Mutator,
                    call: Callable[[], object], ok_text: str) -> None:
        self.item_changed.emit(item.id, apply)

        def done(_r: object) -> None:
            self.toast.emit(ok_text, False)
            self.data_changed.emit()

        def failed(e: Exception) -> None:
            self.item_changed.emit(item.id, revert)
            self.toast.emit(f"Couldn't update “{item.name}”: {e}", True)

        run_async(call, done, failed)
