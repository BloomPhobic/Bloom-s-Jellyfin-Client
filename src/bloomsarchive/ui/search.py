"""Search: live results as you type, grouped into Movies, Shows and Episodes rows."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..api import browse
from ..session import Session
from .actions import ItemActions
from .nav import Page
from .widgets import smooth
from .widgets.cards import CardRow, CardStyle
from .widgets.image_loader import ImageLoader
from .workers import run_async

DEBOUNCE_MS = 300
STYLES = {"Movie": CardStyle.POSTER, "Series": CardStyle.POSTER, "Episode": CardStyle.LANDSCAPE}


class SearchPage(Page):
    title = "Search"
    nav_key = "search"
    open_item = Signal(object)

    def __init__(self, session: Session, loader: ImageLoader, actions: ItemActions, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.session = session
        self.actions = actions
        self.term = ""
        self._gen = 0
        self._found = 0
        self._pending = 0

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._run)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        self._smooth = smooth.install(scroll)

        content = QWidget()
        content.setObjectName("root")
        col = QVBoxLayout(content)
        col.setContentsMargins(22, 18, 22, 28)
        col.setSpacing(22)
        scroll.setWidget(content)

        self.heading = QLabel("Search")
        self.heading.setProperty("role", "h1")
        self.status = QLabel("Type to search your library.")
        self.status.setProperty("role", "muted")
        col.addWidget(self.heading)
        col.addWidget(self.status)

        self.rows: dict[str, CardRow] = {}
        for label, item_type in browse.SEARCH_GROUPS:
            row = CardRow(label, STYLES[item_type], loader)
            row.item_clicked.connect(self.open_item)
            row.item_menu.connect(lambda item, pos: actions.context_menu(item, pos, self))
            row.toggle_played.connect(actions.toggle_played)
            row.toggle_favorite.connect(actions.toggle_favorite)
            row.hide()
            col.addWidget(row)
            self.rows[item_type] = row
        col.addStretch()
        actions.item_changed.connect(self._item_changed)

    def set_term(self, term: str) -> None:
        term = term.strip()
        if term == self.term:
            return
        self.term = term
        self._debounce.start()

    def _run(self) -> None:
        self._gen += 1
        gen = self._gen
        term = self.term
        if len(term) < 2:
            self.heading.setText("Search")
            self.status.setText("Type at least two characters.")
            self.status.show()
            for row in self.rows.values():
                row.hide()
            return
        self.heading.setText(f"Results for “{term}”")
        self.status.setText("Searching…")
        self.status.show()
        self._found, self._pending = 0, len(self.rows)
        c = self.session.client
        for item_type, row in self.rows.items():
            row.model.set_loading()
            row.show()
            run_async(lambda t=item_type: browse.search(c, term, t),
                      lambda items, r=row: self._done(gen, r, items),
                      lambda e, r=row: self._done(gen, r, [], e))

    def _done(self, gen: int, row: CardRow, items: list, error: Exception | None = None) -> None:
        if gen != self._gen:
            return
        self._pending -= 1
        row.model.set_items(items)
        row.setVisible(bool(items))
        self._found += len(items)
        if self._pending <= 0:
            if self._found:
                self.status.hide()
            else:
                self.status.setText(f"Couldn't search: {error}" if error else
                                    f"Nothing found for “{self.term}”.")
                self.status.show()

    def _item_changed(self, item_id: str, fn) -> None:
        for row in self.rows.values():
            row.model.update_where(item_id, fn)
