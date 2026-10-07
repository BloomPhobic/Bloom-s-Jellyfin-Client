"""The signed-in shell: top bar with the user menu.

Phase 2 placeholder body; phase 3 puts the nav sidebar and home rows here.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout,
                               QWidget)

from .. import APP_NAME
from ..api.models import User
from ..session import Session


class Shell(QWidget):
    switch_user = Signal()
    log_out = Signal()

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.setObjectName("root")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setProperty("role", "topbar")
        bar.setFixedHeight(60)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 0, 16, 0)
        mark = QLabel(APP_NAME)
        mark.setProperty("role", "wordmark-small")
        h.addWidget(mark)
        h.addStretch()

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
        v.addWidget(bar)

        body = QVBoxLayout()
        body.addStretch()
        self.welcome = QLabel("")
        self.welcome.setProperty("role", "h1")
        self.welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note = QLabel("Your home screen arrives in phase 3.")
        note.setProperty("role", "muted")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.welcome)
        body.addWidget(note)
        body.addStretch()
        v.addLayout(body)

    def set_user(self, user: User) -> None:
        self.user_btn.setText(f"{user.name}  ▾")
        self.welcome.setText(f"Welcome back, {user.name}")
