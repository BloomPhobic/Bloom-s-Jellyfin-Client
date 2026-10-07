"""QApplication and the top-level window flow.

    startup ──ready(user)──▶ shell
       └────ready(None)───▶ login ──signed_in──▶ shell
    shell ──switch user / log out──▶ login
    any 401 while signed in ──▶ login ("session ended")
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from . import APP_ID, APP_NAME
from .api.models import User
from .session import Session
from .ui import theme
from .ui.login import LoginScreen
from .ui.shell import Shell
from .ui.startup import StartupScreen
from .ui.widgets.image_loader import ImageLoader
from .ui.workers import run_async

log = logging.getLogger(__name__)


class SessionBridge(QObject):
    """Carries callbacks that fire on worker threads over to the UI thread."""
    unauthorized = Signal()


class MainWindow(QMainWindow):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self.setMinimumSize(720, 560)

        self.images = ImageLoader(session, parent=self)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.startup = StartupScreen(session)
        self.login = LoginScreen(session, self.images)
        self.shell = Shell(session)
        for w in (self.startup, self.login, self.shell):
            self.stack.addWidget(w)

        self.startup.ready.connect(self._on_ready)
        self.login.signed_in.connect(self.show_shell)
        self.shell.switch_user.connect(self._switch_user)
        self.shell.log_out.connect(self._log_out)

        self.bridge = SessionBridge(self)
        self.bridge.unauthorized.connect(self._session_ended)
        session.client.on_unauthorized = self._on_unauthorized_worker

    # ------------------------------------------------------------------ flow
    def _on_ready(self, user: User | None) -> None:
        if user:
            self.show_shell(user)
        else:
            self.show_login()

    def show_login(self, message: str = "", open_last: bool = False) -> None:
        self.login.enter(message, open_last=open_last)
        self.stack.setCurrentWidget(self.login)

    def show_shell(self, user: User) -> None:
        self.login.leave()
        self.shell.set_user(user)
        self.stack.setCurrentWidget(self.shell)
        self.setWindowTitle(f"{APP_NAME} — {user.name}")

    def _switch_user(self) -> None:
        self.session.switch_user()
        self.setWindowTitle(APP_NAME)
        self.show_login()

    def _log_out(self) -> None:
        self.setWindowTitle(APP_NAME)
        run_async(self.session.logout, lambda _r: self.show_login(), lambda _e: self.show_login())

    # ------------------------------------------------------------------ 401
    def _on_unauthorized_worker(self) -> None:
        # Runs on whichever worker got the 401.
        self.session.handle_unauthorized()
        self.bridge.unauthorized.emit()

    def _session_ended(self) -> None:
        if self.stack.currentWidget() is self.shell:
            self.setWindowTitle(APP_NAME)
            self.show_login("Your session ended — please sign in again.", open_last=True)


def run_app(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)      # Wayland app-id; matches bloomsarchive.desktop
    app.setWindowIcon(QIcon.fromTheme("multimedia-video-player"))
    theme.apply(app)

    session = Session()
    win = MainWindow(session)
    win.show()
    QTimer.singleShot(0, win.startup.start)
    return app.exec()
