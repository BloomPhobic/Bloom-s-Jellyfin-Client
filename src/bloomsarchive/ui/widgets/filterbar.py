"""Sort and filter controls for a library grid, plus the A–Z jump bar."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from ...api.browse import DEFAULT_DESC, LETTERS, SORTS, FilterOptions, LibraryQuery

STATUS = [("all", "All"), ("unplayed", "Unwatched"), ("played", "Watched")]


def _menu_button(text: str) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setProperty("role", "filter")
    b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class FilterBar(QWidget):
    changed = Signal(object)        # LibraryQuery

    def __init__(self, show_favourites: bool = True, parent=None):
        super().__init__(parent)
        self.q: LibraryQuery | None = None
        self._opts = FilterOptions([], [])
        self._quiet = False

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        self.sort = QComboBox()
        for key, (label, _) in SORTS.items():
            self.sort.addItem(label, key)
        self.sort.setToolTip("Sort by")
        self.sort.currentIndexChanged.connect(self._sort_changed)

        self.order = QToolButton()
        self.order.setProperty("role", "filter")
        self.order.setCursor(Qt.CursorShape.PointingHandCursor)
        self.order.clicked.connect(self._flip_order)

        self.status = QComboBox()
        for key, label in STATUS:
            self.status.addItem(label, key)
        self.status.currentIndexChanged.connect(self._status_changed)

        self.fav = QPushButton("♡ Favourites")
        self.fav.setProperty("role", "chip")
        self.fav.setCheckable(True)
        self.fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fav.toggled.connect(self._fav_changed)
        self.fav.setVisible(show_favourites)

        self.genres_btn = _menu_button("Genres")
        self.genres_menu = QMenu(self.genres_btn)
        self.genres_btn.setMenu(self.genres_menu)
        self.years_btn = _menu_button("Years")
        self.years_menu = QMenu(self.years_btn)
        self.years_btn.setMenu(self.years_menu)

        self.clear_btn = QPushButton("Clear filters")
        self.clear_btn.setProperty("role", "link")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear)

        self.count = QLabel("")
        self.count.setProperty("role", "muted")

        for w in (self.sort, self.order, self.status, self.fav, self.genres_btn, self.years_btn,
                  self.clear_btn):
            h.addWidget(w)
        h.addStretch()
        h.addWidget(self.count)

    # ------------------------------------------------------------------ state in
    def set_query(self, q: LibraryQuery) -> None:
        self.q = q
        self._quiet = True
        try:
            self.sort.setCurrentIndex(max(0, self.sort.findData(q.sort)))
            self.status.setCurrentIndex(max(0, self.status.findData(q.status)))
            self.fav.setChecked(q.favourites)
        finally:
            self._quiet = False
        self._refresh_labels()
        self._rebuild_menus()

    def set_options(self, opts: FilterOptions) -> None:
        self._opts = opts
        self._rebuild_menus()

    def set_count(self, n: int | None) -> None:
        self.count.setText("" if n is None or n < 0 else f"{n:,} title{'s' if n != 1 else ''}")

    # ------------------------------------------------------------------ internals
    def _emit(self, q: LibraryQuery) -> None:
        self.q = q
        self._refresh_labels()
        # Rebuild after the triggering QAction's signal has returned: clearing
        # a menu deletes its actions, and deleting the sender mid-signal crashes.
        QTimer.singleShot(0, self._rebuild_menus)
        self.changed.emit(q)

    def _refresh_labels(self) -> None:
        q = self.q
        if q is None:
            return
        if q.is_random:
            self.order.setText("⟳ Shuffle")
            self.order.setToolTip("Shuffle again")
        elif q.sort == "name":
            self.order.setText("Z → A" if q.descending else "A → Z")
            self.order.setToolTip("Reverse order")
        else:
            self.order.setText("↓ Newest / highest" if q.descending else "↑ Oldest / lowest")
            self.order.setToolTip("Reverse order")
        self.fav.setText("♥ Favourites" if q.favourites else "♡ Favourites")
        self.genres_btn.setText(f"Genres · {len(q.genres)}" if q.genres else "Genres")
        self.years_btn.setText(f"Years · {len(q.years)}" if q.years else "Years")
        for b, on in ((self.genres_btn, bool(q.genres)), (self.years_btn, bool(q.years))):
            b.setProperty("active", "true" if on else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        self.clear_btn.setVisible(q.is_filtered)

    def _rebuild_menus(self) -> None:
        q = self.q
        if q is None:
            return
        self.genres_menu.clear()
        if not self._opts.genres:
            a = self.genres_menu.addAction("No genres")
            a.setEnabled(False)
        for g in self._opts.genres:
            act = QAction(g, self.genres_menu)
            act.setCheckable(True)
            act.setChecked(g in q.genres)
            act.toggled.connect(lambda on, g=g: self._toggle_genre(g, on))
            self.genres_menu.addAction(act)

        self.years_menu.clear()
        if not self._opts.years:
            a = self.years_menu.addAction("No years")
            a.setEnabled(False)
        decades: dict[int, list[int]] = {}
        for y in self._opts.years:
            decades.setdefault(y // 10 * 10, []).append(y)
        for decade in sorted(decades, reverse=True):
            years = decades[decade]
            chosen = sum(1 for y in years if y in q.years)
            sub = self.years_menu.addMenu(f"{decade}s" + (f"  ·  {chosen}" if chosen else ""))
            all_act = QAction(f"All of the {decade}s", sub)
            all_act.triggered.connect(lambda _=False, ys=years: self._set_years_bulk(ys))
            sub.addAction(all_act)
            sub.addSeparator()
            for y in years:
                act = QAction(str(y), sub)
                act.setCheckable(True)
                act.setChecked(y in q.years)
                act.toggled.connect(lambda on, y=y: self._toggle_year(y, on))
                sub.addAction(act)

    def _sort_changed(self, _i: int) -> None:
        if self._quiet or self.q is None:
            return
        key = self.sort.currentData()
        self._emit(replace(self.q, sort=key, descending=key in DEFAULT_DESC))

    def _flip_order(self) -> None:
        if self.q is None:
            return
        if self.q.is_random:
            self._emit(replace(self.q))          # same query = reshuffle
        else:
            self._emit(replace(self.q, descending=not self.q.descending))

    def _status_changed(self, _i: int) -> None:
        if self._quiet or self.q is None:
            return
        self._emit(replace(self.q, status=self.status.currentData()))

    def _fav_changed(self, on: bool) -> None:
        if self._quiet or self.q is None:
            return
        self._emit(replace(self.q, favourites=on))

    def _toggle_genre(self, g: str, on: bool) -> None:
        if self.q is None:
            return
        genres = [x for x in self.q.genres if x != g] + ([g] if on else [])
        self._emit(replace(self.q, genres=genres))

    def _toggle_year(self, y: int, on: bool) -> None:
        if self.q is None:
            return
        years = sorted({x for x in self.q.years if x != y} | ({y} if on else set()), reverse=True)
        self._emit(replace(self.q, years=years))

    def _set_years_bulk(self, ys: list[int]) -> None:
        if self.q is None:
            return
        self._emit(replace(self.q, years=sorted(set(self.q.years) | set(ys), reverse=True)))

    def _clear(self) -> None:
        if self.q is None:
            return
        cleared = self.q.cleared()
        self.set_query(cleared)
        self.changed.emit(cleared)


class LetterBar(QWidget):
    """Vertical # A–Z strip; jumps the grid to the first title under a letter."""
    letter = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 4)
        v.setSpacing(0)
        self.buttons: list[QToolButton] = []
        for ch in LETTERS:
            b = QToolButton()
            b.setText(ch)
            b.setProperty("role", "letter")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=ch: self.letter.emit(c))
            v.addWidget(b, 1)
            self.buttons.append(b)
        self.setFixedWidth(30)

    def set_available(self, ok: bool) -> None:
        for b in self.buttons:
            b.setEnabled(ok)
        self.setToolTip("" if ok else "Sort by Name to jump to a letter")
