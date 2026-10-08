"""Pages and a browser-style back/forward stack.

Pages stay alive while they're in history, so going back restores scroll
position and filters exactly. Persistent pages (Home) are never destroyed.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

MAX_HISTORY = 25


class Page(QWidget):
    """Base for everything shown in the shell's content area."""
    title: str = ""
    nav_key: str = ""          # which sidebar entry to highlight
    persistent: bool = False   # kept alive outside history (e.g. Home)

    def page_shown(self) -> None:
        """Called every time the page becomes visible (first time and after Back)."""

    def page_hidden(self) -> None:
        """Called when another page replaces it."""


class PlaceholderPage(Page):
    """Stand-in for screens that arrive in later build phases."""

    def __init__(self, title: str, message: str, nav_key: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.nav_key = nav_key
        self.setObjectName("root")
        v = QVBoxLayout(self)
        v.addStretch(2)
        t = QLabel(title)
        t.setProperty("role", "h1")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setWordWrap(True)
        m = QLabel(message)
        m.setProperty("role", "muted")
        m.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(m)
        v.addStretch(3)


class NavStack(QStackedWidget):
    changed = Signal()     # current page or back/forward availability changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._back: list[Page] = []
        self._fwd: list[Page] = []
        self._current: Page | None = None

    @property
    def current(self) -> Page | None:
        return self._current

    def can_back(self) -> bool:
        return bool(self._back)

    def can_forward(self) -> bool:
        return bool(self._fwd)

    def push(self, page: Page) -> None:
        if page is self._current:
            return
        if self._current is not None:
            self._back.append(self._current)
        dropped, self._fwd = self._fwd, []
        while len(self._back) > MAX_HISTORY:
            dropped.append(self._back.pop(0))
        self._show(page)
        self._dispose(dropped)

    def back(self) -> None:
        if not self._back:
            return
        assert self._current is not None
        self._fwd.append(self._current)
        self._show(self._back.pop())

    def forward(self) -> None:
        if not self._fwd:
            return
        assert self._current is not None
        self._back.append(self._current)
        self._show(self._fwd.pop())

    def reset(self, page: Page) -> None:
        """Clear all history (e.g. on user switch) and show `page`."""
        dropped = self._back + self._fwd + ([self._current] if self._current else [])
        self._back, self._fwd = [], []
        self._current = None
        self._show(page)
        self._dispose(dropped)

    def _show(self, page: Page) -> None:
        old = self._current
        if self.indexOf(page) < 0:
            self.addWidget(page)
        self._current = page
        self.setCurrentWidget(page)
        if old is not None and old is not page:
            old.page_hidden()
        page.page_shown()
        self.changed.emit()

    def _dispose(self, pages: list[Page]) -> None:
        live = set(map(id, self._back + self._fwd + [self._current]))
        for p in pages:
            if p is None or p.persistent or id(p) in live:
                continue
            self.removeWidget(p)
            p.deleteLater()
