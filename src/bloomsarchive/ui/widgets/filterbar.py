"""Sort and filter controls for a library grid, plus the A–Z jump bar."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QToolButton, QVBoxLayout, QWidget)

from ...api.browse import DEFAULT_DESC, LETTERS, SORTS, FilterOptions, LibraryQuery

STATUS = [("all", "All"), ("unplayed", "Unwatched"), ("played", "Watched")]


# =========================================================================== chip picker

class ChipPopup(QFrame):
    """A popup of toggle chips that stays open while you pick several.

    Used for Genres and Years. Years get a Newest/Oldest order toggle.
    """
    toggled = Signal(object, bool)      # value, on
    cleared = Signal()

    def __init__(self, title: str, columns: int, orderable: bool = False, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setProperty("role", "popup")
        self.columns = columns
        self.newest_first = True
        self._values: list = []
        self._chips: dict = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel(title)
        t.setProperty("role", "h2")
        head.addWidget(t)
        head.addStretch()
        self.order_btn = QPushButton("")
        self.order_btn.setProperty("role", "chip")
        self.order_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.order_btn.clicked.connect(self._flip_order)
        self.order_btn.setVisible(orderable)
        head.addWidget(self.order_btn)
        clear = QPushButton("Clear")
        clear.setProperty("role", "link")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.cleared.emit)
        head.addWidget(clear)
        v.addLayout(head)

        self.empty = QLabel("Nothing to filter by in this library.")
        self.empty.setProperty("role", "muted")
        v.addWidget(self.empty)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 0, 4, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(6)
        self.scroll.setWidget(self.body)
        v.addWidget(self.scroll)
        self._update_order_text()

    # -- content
    def set_values(self, values: list, selected) -> None:
        self._values = list(values)
        self._rebuild(set(selected))

    def set_selected(self, selected) -> None:
        sel = set(selected)
        for value, chip in self._chips.items():
            chip.blockSignals(True)
            chip.setChecked(value in sel)
            chip.blockSignals(False)

    def _ordered(self) -> list:
        if all(isinstance(v, int) for v in self._values):
            return sorted(self._values, reverse=self.newest_first)
        return list(self._values)

    def _rebuild(self, selected: set) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self._chips = {}
        values = self._ordered()
        for i, value in enumerate(values):
            chip = QPushButton(str(value))
            chip.setProperty("role", "chip")
            chip.setCheckable(True)
            chip.setChecked(value in selected)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.toggled.connect(lambda on, v=value: self.toggled.emit(v, on))
            self.grid.addWidget(chip, i // self.columns, i % self.columns)
            self._chips[value] = chip
        self.empty.setVisible(not values)
        self.scroll.setVisible(bool(values))
        rows = (len(values) + self.columns - 1) // self.columns
        self.scroll.setFixedHeight(min(360, max(40, rows * 40)))
        self.setFixedWidth(min(560, 34 + self.columns * (110 if self.columns <= 3 else 78)))

    def _flip_order(self) -> None:
        self.newest_first = not self.newest_first
        self._update_order_text()
        self._rebuild({v for v, c in self._chips.items() if c.isChecked()})

    def _update_order_text(self) -> None:
        self.order_btn.setText("↓ Newest first" if self.newest_first else "↑ Oldest first")

    def show_under(self, anchor: QWidget) -> None:
        self.adjustSize()
        self.move(anchor.mapToGlobal(QPoint(0, anchor.height() + 6)))
        self.show()


# =========================================================================== bar

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

        self.genres_btn = self._filter_button("Genres")
        self.genres_pop = ChipPopup("Genres", columns=3, parent=self)
        self.genres_pop.toggled.connect(self._toggle_genre)
        self.genres_pop.cleared.connect(lambda: self._set(genres=[]))
        self.genres_btn.clicked.connect(lambda: self.genres_pop.show_under(self.genres_btn))

        self.years_btn = self._filter_button("Years")
        self.years_pop = ChipPopup("Years", columns=6, orderable=True, parent=self)
        self.years_pop.toggled.connect(self._toggle_year)
        self.years_pop.cleared.connect(lambda: self._set(years=[]))
        self.years_btn.clicked.connect(lambda: self.years_pop.show_under(self.years_btn))

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

    @staticmethod
    def _filter_button(text: str) -> QToolButton:
        b = QToolButton()
        b.setText(text + "  ▾")
        b.setProperty("role", "filter")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

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
        self.genres_pop.set_values(self._opts.genres, q.genres)
        self.years_pop.set_values(self._opts.years, q.years)

    def set_options(self, opts: FilterOptions) -> None:
        self._opts = opts
        if self.q is not None:
            self.genres_pop.set_values(opts.genres, self.q.genres)
            self.years_pop.set_values(opts.years, self.q.years)

    def set_count(self, n: int | None) -> None:
        self.count.setText("" if n is None or n < 0 else f"{n:,} title{'s' if n != 1 else ''}")

    # ------------------------------------------------------------------ internals
    def _emit(self, q: LibraryQuery) -> None:
        self.q = q
        self._refresh_labels()
        self.genres_pop.set_selected(q.genres)
        self.years_pop.set_selected(q.years)
        self.changed.emit(q)

    def _set(self, **changes) -> None:
        if self.q is not None:
            self._emit(replace(self.q, **changes))

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
        self.genres_btn.setText((f"Genres · {len(q.genres)}" if q.genres else "Genres") + "  ▾")
        if len(q.years) == 1:
            years_text = f"Year · {q.years[0]}"
        else:
            years_text = f"Years · {len(q.years)}" if q.years else "Years"
        self.years_btn.setText(years_text + "  ▾")
        for b, on in ((self.genres_btn, bool(q.genres)), (self.years_btn, bool(q.years))):
            b.setProperty("active", "true" if on else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        self.clear_btn.setVisible(q.is_filtered)

    def _sort_changed(self, _i: int) -> None:
        if self._quiet or self.q is None:
            return
        key = self.sort.currentData()
        self._set(sort=key, descending=key in DEFAULT_DESC)

    def _flip_order(self) -> None:
        if self.q is None:
            return
        if self.q.is_random:
            self._emit(replace(self.q))          # same query = reshuffle
        else:
            self._set(descending=not self.q.descending)

    def _status_changed(self, _i: int) -> None:
        if not self._quiet:
            self._set(status=self.status.currentData())

    def _fav_changed(self, on: bool) -> None:
        if not self._quiet:
            self._set(favourites=on)

    def _toggle_genre(self, g: str, on: bool) -> None:
        if self.q is not None:
            self._set(genres=[x for x in self.q.genres if x != g] + ([g] if on else []))

    def _toggle_year(self, y: int, on: bool) -> None:
        if self.q is not None:
            self._set(years=sorted({x for x in self.q.years if x != y} | ({y} if on else set()),
                                   reverse=True))

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
