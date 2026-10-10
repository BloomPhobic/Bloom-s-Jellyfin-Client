"""The signed-in shell: top bar (back/forward, search, user menu), sidebar, and
the page stack. Details (phase 5) and Settings (phase 9) are placeholders.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..api import browse
from ..api.browse import LibraryQuery
from ..api.models import Item, User
from ..display import card_text
from ..session import Session
from .actions import ItemActions
from .home import HomePage
from .library import LibraryPage
from .search import SearchPage
from .nav import NavStack, Page, PlaceholderPage
from .widgets.image_loader import ImageLoader
from .widgets.toast import Toast

SIDEBAR_WIDTH = 224
UNSUPPORTED_LIBRARIES = {"music", "books", "photos", "livetv", "playlists", "homevideos", "musicvideos"}


def _nav_button(text: str, key: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("role", "nav")
    b.setProperty("navkey", key)
    b.setCheckable(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class Shell(QWidget):
    switch_user = Signal()
    log_out = Signal()

    def __init__(self, session: Session, loader: ImageLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.loader = loader
        self.setObjectName("root")
        self._user_id: str | None = None
        self._views: list[Item] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        self.nav = NavStack()
        body.addWidget(self.nav, 1)
        root.addLayout(body, 1)

        self.actions = ItemActions(session, self)
        self.toast = Toast(self)
        self.actions.toast.connect(self.toast.show_message)
        self.actions.open_requested.connect(self.open_item)

        self.home = HomePage(session, loader, self.actions)
        self.home.open_item.connect(self.open_item)
        self.home.open_view.connect(self.open_view)
        self.home.views_loaded.connect(self._set_views)
        self.nav.changed.connect(self._nav_changed)

        for seq, fn in ((QKeySequence.StandardKey.Back, self.nav.back),
                        (QKeySequence.StandardKey.Forward, self.nav.forward),
                        (QKeySequence.StandardKey.Refresh, self.refresh),
                        (QKeySequence.StandardKey.Find, self._focus_search)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)

    # ================================================================== chrome
    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("role", "topbar")
        bar.setFixedHeight(60)
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 0, 16, 0)
        h.setSpacing(8)

        self.back_btn = QToolButton()
        self.back_btn.setText("‹")
        self.back_btn.setToolTip("Back (Alt+←, mouse back)")
        self.fwd_btn = QToolButton()
        self.fwd_btn.setText("›")
        self.fwd_btn.setToolTip("Forward (Alt+→)")
        for b, fn in ((self.back_btn, lambda: self.nav.back()), (self.fwd_btn, lambda: self.nav.forward())):
            b.setProperty("role", "navbtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(34, 34)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addSpacing(8)

        mark = QLabel(APP_NAME)
        mark.setProperty("role", "wordmark-small")
        h.addWidget(mark)
        h.addStretch(1)

        self.search = QLineEdit()
        self.search.setProperty("role", "search")
        self.search.setPlaceholderText("Search movies, shows, episodes…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(380)
        self.search.returnPressed.connect(self._search)
        self.search.textEdited.connect(self._search_typed)
        h.addWidget(self.search)
        h.addStretch(1)

        self.user_btn = QToolButton()
        self.user_btn.setProperty("role", "usermenu")
        self.user_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.user_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        menu = QMenu(self.user_btn)
        act_switch = QAction("Switch user", self)
        act_switch.triggered.connect(self.switch_user.emit)
        act_logout = QAction("Log out", self)
        act_logout.triggered.connect(self.log_out.emit)
        menu.addAction(act_switch)
        menu.addSeparator()
        menu.addAction(act_logout)
        self.user_btn.setMenu(menu)
        h.addWidget(self.user_btn)
        return bar

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setProperty("role", "sidebar")
        side.setFixedWidth(SIDEBAR_WIDTH)
        self.side = QVBoxLayout(side)
        self.side.setContentsMargins(12, 16, 12, 16)
        self.side.setSpacing(2)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.btn_home = _nav_button("Home", "home")
        self.btn_home.clicked.connect(self.go_home)
        self.side.addWidget(self.btn_home)
        self.btn_favs = _nav_button("Favourites", "favourites")
        self.btn_favs.clicked.connect(self._open_favourites)
        self.side.addWidget(self.btn_favs)

        self.side.addSpacing(14)
        lib = QLabel("LIBRARIES")
        lib.setProperty("role", "section")
        self.side.addWidget(lib)
        self.lib_box = QVBoxLayout()
        self.lib_box.setSpacing(2)
        self.side.addLayout(self.lib_box)
        self.side.addStretch()

        self.btn_settings = _nav_button("Settings", "settings")
        self.btn_settings.clicked.connect(self._open_settings)
        self.side.addWidget(self.btn_settings)
        for b in (self.btn_home, self.btn_favs, self.btn_settings):
            self.nav_group.addButton(b)
        return side

    def _set_views(self, views: list[Item]) -> None:
        if [v.id for v in views] == [v.id for v in self._views]:
            return
        self._views = views
        while self.lib_box.count():
            w = self.lib_box.takeAt(0).widget()
            if w is not None:
                self.nav_group.removeButton(w)
                w.deleteLater()
        for v in views:
            b = _nav_button(v.name, f"view:{v.id}")
            b.clicked.connect(lambda _=False, view=v: self.open_view(view))
            self.nav_group.addButton(b)
            self.lib_box.addWidget(b)
        self._nav_changed()

    def _nav_changed(self) -> None:
        self.back_btn.setEnabled(self.nav.can_back())
        self.fwd_btn.setEnabled(self.nav.can_forward())
        page = self.nav.current
        key = page.nav_key if page else ""
        hit = False
        for b in self.nav_group.buttons():
            on = b.property("navkey") == key
            b.setChecked(on)
            hit = hit or on
        if not hit:                      # e.g. a details page: nothing highlighted
            self.nav_group.setExclusive(False)
            for b in self.nav_group.buttons():
                b.setChecked(False)
            self.nav_group.setExclusive(True)

    # ================================================================== navigation
    def set_user(self, user: User) -> None:
        self.user_btn.setText(f"{user.name}  ▾")
        if user.id != self._user_id:
            self._user_id = user.id
            self.home.clear()
            self._views = []
            self._set_views([])
        self.nav.reset(self.home)

    def go_home(self) -> None:
        self.nav.push(self.home)

    def refresh(self) -> None:
        if self.nav.current is self.home:
            self.home.load(initial=False)

    def open_view(self, view: Item) -> None:
        cur = self.nav.current
        key = f"view:{view.id}"
        if isinstance(cur, LibraryPage) and cur.nav_key == key:
            return
        ct = (view.collection_type or "").lower()
        if ct in UNSUPPORTED_LIBRARIES:
            self.nav.push(PlaceholderPage(view.name, f"{ct.title()} libraries aren't supported here.",
                                          nav_key=key))
            return
        q = LibraryQuery(view.id, browse.types_for(view.collection_type))
        self._push_library(view.name, q, key, state_key=view.id)

    def open_item(self, item: Item) -> None:
        if item.type in ("BoxSet", "Folder", "CollectionFolder", "UserView"):
            if item.type in ("CollectionFolder", "UserView"):
                self.open_view(item)
                return
            q = LibraryQuery(item.id, "Movie,Series")
            self._push_library(item.name, q, nav_key="", state_key=f"collection:{item.id}")
            return
        title, sub = card_text(item)
        heading = f"{title} — {sub}" if sub and item.type == "Episode" else title
        self.nav.push(PlaceholderPage(
            heading, f"{item.type} details, track pickers and Play arrive in phase 5."))

    def _push_library(self, title: str, q: LibraryQuery, nav_key: str, state_key: str | None,
                      show_fav: bool = True) -> None:
        page = LibraryPage(self.session, self.loader, self.actions, title, q, nav_key, state_key,
                           show_favourites_filter=show_fav)
        page.open_item.connect(self.open_item)
        self.nav.push(page)

    def _open_favourites(self) -> None:
        if isinstance(self.nav.current, LibraryPage) and self.nav.current.nav_key == "favourites":
            return
        q = LibraryQuery(None, "Movie,Series,Episode", fixed_filters=["IsFavorite"])
        self._push_library("Favourites", q, "favourites", state_key="favourites", show_fav=False)

    def _open_settings(self) -> None:
        self.nav.push(PlaceholderPage("Settings", "Settings arrive in phase 9.", nav_key="settings"))

    def _search_page(self) -> SearchPage:
        cur = self.nav.current
        if isinstance(cur, SearchPage):
            return cur
        page = SearchPage(self.session, self.loader, self.actions)
        page.open_item.connect(self.open_item)
        self.nav.push(page)
        return page

    def _search_typed(self, text: str) -> None:
        if text.strip() or isinstance(self.nav.current, SearchPage):
            self._search_page().set_term(text)

    def _search(self) -> None:
        q = self.search.text().strip()
        if q:
            self._search_page().set_term(q)

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def current_page(self) -> Page | None:
        return self.nav.current
