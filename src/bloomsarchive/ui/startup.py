"""Startup: find the server, pin it on first run, try the stored login.

Emits ready(User | None) once connected: a User means the stored token worked.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import APP_NAME, __version__
from ..api.discovery import Discovery
from ..session import Session
from .workers import run_async


def _label(text: str, role: str | None = None) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lab.setWordWrap(True)
    return lab


def _button(text: str, role: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class StartupScreen(QWidget):
    ready = Signal(object)           # User | None

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
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
        if self.session.used_fast_path:
            run_async(self.session.reprobe)      # switch to a better address in the background
        self._restore()

    def _restore(self) -> None:
        d = self.session.discovery
        assert d and d.chosen and d.chosen.info
        self.status.setText(f"Connected to {d.chosen.info.name}")
        self.detail.setText("Checking your sign-in…")
        run_async(self.session.restore_login, self.ready.emit, lambda _e: self.ready.emit(None))

    def _pin(self) -> None:
        self.session.pin_server()
        self.pin_btn.hide()
        self.retry_btn.hide()
        self._restore()

    def _failed(self, err: Exception) -> None:
        self.status.setText("Something went wrong")
        self.detail.setText(str(err))
        self.retry_btn.show()
