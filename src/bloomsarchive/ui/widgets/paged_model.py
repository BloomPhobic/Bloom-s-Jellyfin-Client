"""A sparse, paged list model for big libraries.

rowCount is the library's real total from the first response, so the
scrollbar is right from the start and any row can be reached instantly
(the letter jump depends on this). Pages of ~60 are fetched only when a
card in them is painted; until then the card paints as a skeleton.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, Qt, Signal

from ...api.browse import PAGE_SIZE, Page
from ...api.models import Item
from ...display import card_text
from ..workers import run_async
from .cards import CardStyle, ItemRole, KeyRole
from .image_loader import ImageLoader

log = logging.getLogger(__name__)

SKELETONS = 24
RETRY_AFTER = 3.0

Fetcher = Callable[[int, int], Page]       # (start, limit) -> Page, runs in a worker


class PagedItemModel(QAbstractListModel):
    total_changed = Signal(int)            # -1 while unknown
    load_failed = Signal(str)

    def __init__(self, style: CardStyle, loader: ImageLoader, parent=None):
        super().__init__(parent)
        self.style = style
        self.loader = loader
        self._fetch: Fetcher | None = None
        self.page_size = PAGE_SIZE
        self._gen = 0
        self.total: int | None = None
        self._items: dict[int, Item] = {}
        self._keys: dict[int, str | None] = {}
        self._rows_by_key: dict[str, set[int]] = {}
        self._inflight: set[int] = set()
        self._failed: dict[int, float] = {}
        loader.loaded.connect(self._on_image)

    # ------------------------------------------------------------------ qt api
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return SKELETONS if self.total is None and self._fetch else (self.total or 0)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.total is None:
            return None
        row = index.row()
        item = self._items.get(row)
        if item is None:
            if role in (ItemRole, KeyRole):
                self._ensure_page(row // self.page_size)
            return None
        if role == ItemRole:
            return item
        if role == KeyRole:
            return self._keys.get(row)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole):
            title, sub = card_text(item)
            return f"{title} — {sub}" if sub else title
        return None

    # ------------------------------------------------------------------ control
    def reset(self, fetch: Fetcher, page_size: int = PAGE_SIZE) -> None:
        """Start over with a new query. Shows skeletons until page 0 arrives."""
        self.beginResetModel()
        self._gen += 1
        self._fetch = fetch
        self.page_size = page_size
        self.total = None
        self._items.clear()
        self._keys.clear()
        self._rows_by_key.clear()
        self._inflight.clear()
        self._failed.clear()
        self.endResetModel()
        self.total_changed.emit(-1)
        self._ensure_page(0)

    def refresh_loaded(self) -> None:
        """Re-fetch pages already on screen without resetting (keeps scroll position)."""
        pages = sorted({r // self.page_size for r in self._items})
        for p in pages or [0]:
            self._request(p, force=True)

    def item_at(self, row: int) -> Item | None:
        return self._items.get(row)

    def update_where(self, item_id: str, fn: Callable[[Item], None]) -> int:
        n = 0
        for row, it in self._items.items():
            if it.id == item_id:
                fn(it)
                idx = self.index(row)
                self.dataChanged.emit(idx, idx)
                n += 1
        return n

    # ------------------------------------------------------------------ paging
    def _ensure_page(self, page: int) -> None:
        if page in self._inflight:
            return
        failed_at = self._failed.get(page)
        if failed_at and time.monotonic() - failed_at < RETRY_AFTER:
            return
        self._request(page)

    def _request(self, page: int, force: bool = False) -> None:
        if self._fetch is None or (page in self._inflight and not force):
            return
        self._inflight.add(page)
        gen, fetch, size = self._gen, self._fetch, self.page_size
        run_async(lambda: fetch(page * size, size),
                  lambda result: self._page_done(gen, page, result),
                  lambda e: self._page_failed(gen, page, e))

    def _page_done(self, gen: int, page: int, result: Page) -> None:
        if gen != self._gen:
            return
        self._inflight.discard(page)
        self._failed.pop(page, None)
        first = result.start
        if self.total is None or result.total != self.total:
            # First page, or the library changed size: rebuild around the new total.
            self.beginResetModel()
            if self.total is not None:
                self._items.clear()
                self._keys.clear()
                self._rows_by_key.clear()
            self.total = result.total
            self._store(first, result.items)
            self.endResetModel()
            self.total_changed.emit(self.total)
            return
        self._store(first, result.items)
        if result.items:
            self.dataChanged.emit(self.index(first), self.index(first + len(result.items) - 1))

    def _store(self, first: int, items: list[Item]) -> None:
        for offset, it in enumerate(items):
            row = first + offset
            old = self._keys.get(row)
            if old and old in self._rows_by_key:
                self._rows_by_key[old].discard(row)
            key = self.style.key_for(it)
            self._items[row] = it
            self._keys[row] = key
            if key:
                self._rows_by_key.setdefault(key, set()).add(row)

    def _page_failed(self, gen: int, page: int, e: Exception) -> None:
        if gen != self._gen:
            return
        self._inflight.discard(page)
        self._failed[page] = time.monotonic()
        log.warning("page %d failed: %s", page, e)
        self.load_failed.emit(str(e))

    def _on_image(self, key: str) -> None:
        for row in self._rows_by_key.get(key, ()):
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)
