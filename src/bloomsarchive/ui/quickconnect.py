"""Quick Connect page: approve a code shown on another device (TV, phone, laptop)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ..api import auth
from ..api.client import NotFound, Unauthorized
from ..session import Session
from .nav import Page
from .servers import set_tone
from .workers import run_async


def _label(text: str, role: str | None = None) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(True)
    return lab


class QuickConnectPage(Page):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.title = "Quick Connect"
        self.nav_key = "quickconnect"
        self.session = session
        self.setObjectName("root")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 32)
        outer.setSpacing(18)
        outer.addWidget(_label("Quick Connect", "h1"))

        card = QFrame()
        card.setProperty("role", "card")
        card.setMaximumWidth(620)
        v = QVBoxLayout(card)
        v.setContentsMargins(26, 22, 26, 24)
        v.setSpacing(14)
        v.addWidget(_label("Sign in another device", "h2"))
        v.addWidget(_label("On the TV, phone or computer you want to sign in, choose <b>Quick Connect</b>. "
                           "It shows a 6-digit code. Type it here and press Approve; that device signs in "
                           "as you. Codes last 5 minutes.", "muted"))

        h = QHBoxLayout()
        h.setSpacing(10)
        self.code = QLineEdit()
        self.code.setProperty("role", "code")
        self.code.setPlaceholderText("123456")
        self.code.setMaxLength(12)
        self.code.setMinimumWidth(200)
        self.code.setMaximumWidth(260)
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.approve = QPushButton("Approve")
        self.approve.setProperty("role", "primary")
        self.approve.setProperty("size", "large")
        self.approve.setCursor(Qt.CursorShape.PointingHandCursor)
        h.addWidget(self.code)
        h.addWidget(self.approve)
        h.addStretch(1)
        v.addLayout(h)
        self.msg = _label("", "hint")
        self.msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.msg)
        self.who = _label("", "hint")
        v.addWidget(self.who)

        outer.addWidget(card)
        outer.addStretch(1)

        self.approve.clicked.connect(self._approve)
        self.code.returnPressed.connect(self._approve)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.approve.setFixedHeight(self.code.sizeHint().height())     # same height as the code box

    def page_shown(self) -> None:
        u = self.session.user
        self.who.setText(f"The other device will be signed in as <b>{u.name}</b>." if u else "")
        self.code.setFocus()

    def _say(self, text: str, tone: str = "") -> None:
        self.msg.setText(text)
        set_tone(self.msg, tone)

    def _approve(self) -> None:
        code = "".join(ch for ch in self.code.text() if ch.isalnum())
        if not code:
            self._say("Type the code from the other device.", "error")
            return
        self.approve.setEnabled(False)
        self._say("Approving…")
        client = self.session.client

        def done(ok: bool) -> None:
            self.approve.setEnabled(True)
            if ok:
                self.code.clear()
                self._say("✓ Approved — that device is signing in now.", "ok")
            else:
                failed(RuntimeError("the server refused it"))

        def failed(e: Exception) -> None:
            self.approve.setEnabled(True)
            if isinstance(e, NotFound):
                self._say("That code wasn't found — it may have expired (codes last 5 minutes).", "error")
            elif isinstance(e, Unauthorized):
                self._say("The server didn't allow it (is Quick Connect enabled?).", "error")
            else:
                self._say(f"Couldn't approve: {e}", "error")

        run_async(lambda: auth.authorize_code(client, code), done, failed)
