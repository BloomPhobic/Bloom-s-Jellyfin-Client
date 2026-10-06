"""QApplication and the window shell.

Phase 1: a branded startup screen that finds the server (fast path, then a
background re-probe), asks to pin the ServerId on first run, and restores a
stored login. The login, home and library screens plug into `stack` next.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from . import APP_ID, APP_NAME, __version__
from .api.discovery import Discovery
from .session import Session
from .ui import theme
from .ui.workers import run_async

log = logging.getLogger(__name__)


def _label(text: str, role: str | None = None, center: bool = True) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    if center:
        lab.setAlignment(Qt.AlignCenter)
    lab.setWordWrap(True)
    return lab


def _button(text: str, role: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    b.setCursor(Qt.PointingHandCursor)
    return b


class StartupScreen(QWidget):
    """Wordmark + connection status. Becomes the backdrop of the login screen in phase 2."""

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._login_line = ""
        self.setObjectName("root")

        outer = QVBoxLayout(self)
        outer.addStretch(2)
        outer.addWidget(_label(APP_NAME, "wordmark"))
        outer.addWidget(_label("your library, straight from your server", "tagline"))
        outer.addSpacing(36)

        card = QFrame()
        card.setProperty("role", "card")
        card.setFixedWidth(460)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(24, 20, 24, 20)
        cl.setSpacing(10)
        self.status = _label("Looking for your server…", "h2")
        self.detail = _label("", "muted")
        cl.addWidget(self.status)
        cl.addWidget(self.detail)

        row = QHBoxLayout()
        row.addStretch()
        self.pin_btn = _button("Yes, this is my server", "primary")
        self.retry_btn = _button("Retry")
        row.addWidget(self.pin_btn)
        row.addWidget(self.retry_btn)
        row.addStretch()
        cl.addLayout(row)

        hcenter = QHBoxLayout()
        hcenter.addStretch()
        hcenter.addWidget(card)
        hcenter.addStretch()
        outer.addLayout(hcenter)
        outer.addStretch(3)
        outer.addWidget(_label(f"v{__version__}", "muted"))

        self.pin_btn.clicked.connect(self._pin)
        self.retry_btn.clicked.connect(self.start)
        self.pin_btn.hide()
        self.retry_btn.hide()

    # ------------------------------------------------------------------ flow
    def start(self) -> None:
        self.status.setText("Looking for your server…")
        self.detail.setText("")
        self.retry_btn.hide()
        self.pin_btn.hide()
        run_async(self.session.connect, self._connected, self._failed)

    def _connected(self, d: Discovery) -> None:
        if not d.chosen:
            lines = [r.describe() for r in d.results]
            self.status.setText("Can't reach your server")
            self.detail.setText(d.reason + ("\n\n" + "\n".join(lines) if lines else ""))
            self.retry_btn.show()
            return
        info = d.chosen.info
        assert info
        if d.needs_pin:
            self.status.setText(f"Found “{info.name}”")
            self.detail.setText(f"Jellyfin {info.version} at {d.chosen.candidate.url}\n"
                                "Is this your server? It will be remembered, and other devices "
                                "at the same address will be ignored.")
            self.pin_btn.show()
            self.retry_btn.show()
            return
        self._show_connected(d)
        if self.session.used_fast_path:
            run_async(self.session.reprobe, self._reprobed)
        run_async(self.session.restore_login, self._login_restored)

    def _show_connected(self, d: Discovery) -> None:
        assert d.chosen and d.chosen.info
        c = d.chosen.candidate
        self.status.setText(f"Connected to {d.chosen.info.name}")
        ms = f" · {d.chosen.latency_ms:.0f} ms" if d.chosen.latency_ms is not None else ""
        note = ""
        ok, why = self.session.playback_allowed()
        if not ok:
            note = f"\n{why}"
        login = f"\n{self._login_line}" if self._login_line else ""
        self.detail.setText(f"Jellyfin {d.chosen.info.version} via {c.label or c.url}{ms}{note}{login}")

    def _reprobed(self, changed: bool) -> None:
        if changed and self.session.discovery:
            self._show_connected(self.session.discovery)

    def _login_restored(self, user) -> None:
        self._login_line = (f"Signed in as {user.name}" if user
                            else "Sign-in screen arrives in the next build step.")
        if self.session.discovery:
            self._show_connected(self.session.discovery)

    def _pin(self) -> None:
        self.session.pin_server()
        self.pin_btn.hide()
        self.retry_btn.hide()
        assert self.session.discovery
        self._show_connected(self.session.discovery)
        run_async(self.session.restore_login, self._login_restored)

    def _failed(self, err: Exception) -> None:
        self.status.setText("Something went wrong")
        self.detail.setText(str(err))
        self.retry_btn.show()


class MainWindow(QMainWindow):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.startup = StartupScreen(session)
        self.stack.addWidget(self.startup)


def run_app(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)      # Wayland app-id for the browser window
    app.setWindowIcon(QIcon.fromTheme("multimedia-video-player"))
    theme.apply(app)

    session = Session()
    win = MainWindow(session)
    win.show()
    QTimer.singleShot(0, win.startup.start)
    return app.exec()
