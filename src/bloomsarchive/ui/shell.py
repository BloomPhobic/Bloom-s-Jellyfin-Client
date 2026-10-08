"""The signed-in shell: top bar (back/forward, search, user menu), sidebar, and
the page stack. Home is real; library, details, favourites, search and
settings are placeholders until their phases.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..api.models import Item, User
from ..display import card_text
from ..session import Session
from .home import HomePage
from .nav import NavStack, Page, PlaceholderPage
from .widgets.image_loader import ImageLoader
from .widgets.toast import Toast

SIDEBAR_WIDTH = 224


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

        self.home = HomePage(session, loader)
        self.home.open_item.connect(self.open_item)
        self.home.open_view.connect(self.open_view)
        self.home.views_loaded.connect(self._set_views)
        self.nav.changed.connect(self._nav_changed)

        self.toast = Toast(self)
        self.home.toast.connect(self.toast.show_message)

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
        self.nav.push(PlaceholderPage(
            view.name, "The library grid — sorting, filters, letter jump — arrives in phase 4.",
            nav_key=f"view:{view.id}"))

    def open_item(self, item: Item) -> None:
        title, sub = card_text(item)
        heading = f"{title} — {sub}" if sub and item.type == "Episode" else title
        self.nav.push(PlaceholderPage(
            heading, f"{item.type} details, track pickers and Play arrive in phase 5."))

    def _open_favourites(self) -> None:
        self.nav.push(PlaceholderPage(
            "Favourites", "A full favourites grid arrives in phase 4 — they're also on Home for now.",
            nav_key="favourites"))

    def _open_settings(self) -> None:
        self.nav.push(PlaceholderPage("Settings", "Settings arrive in phase 9.", nav_key="settings"))

    def _search(self) -> None:
        q = self.search.text().strip()
        if q:
            self.nav.push(PlaceholderPage(f"Search: {q}", "Search results arrive in phase 4.",
                                          nav_key="search"))

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def current_page(self) -> Page | None:
        return self.nav.current
