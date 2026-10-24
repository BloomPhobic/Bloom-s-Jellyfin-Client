"""QApplication and the top-level window flow.

    startup ──ready(user)──▶ shell
       └────ready(None)───▶ login ──signed_in──▶ shell
    shell ──switch user / log out──▶ login
    any 401 while signed in ──▶ login ("session ended")
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from . import APP_ID, APP_NAME, wm
from .api.models import User
from .session import Session
from .ui import theme
from .ui.login import LoginScreen
from .ui.shell import Shell
from .ui.startup import StartupScreen
from .ui.widgets import smooth
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
        self.setMinimumSize(900, 560)
        self._size_wide()

        self.images = ImageLoader(session, parent=self)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.startup = StartupScreen(session)
        self.login = LoginScreen(session, self.images)
        self.shell = Shell(session, self.images)
        for w in (self.startup, self.login, self.shell):
            self.stack.addWidget(w)

        self.startup.ready.connect(self._on_ready)
        self.startup.switch_server.connect(self.switch_server)
        self.login.switch_server.connect(self.switch_server)
        self.login.signed_in.connect(self.show_shell)
        self.shell.switch_user.connect(self._switch_user)
        self.shell.log_out.connect(self._log_out)
        self.shell.forget_server.connect(self._forget_server)
        self.shell.switch_server.connect(self.switch_server)

        self.bridge = SessionBridge(self)
        self.bridge.unauthorized.connect(self._session_ended)
        session.client.on_unauthorized = self._on_unauthorized_worker
        QApplication.instance().installEventFilter(self)

    # ------------------------------------------------------------------ window shape
    def _size_wide(self) -> None:
        """16:9, ~80% of the screen. Honoured by stacking WMs; tiling ones see make_wide()."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1600, 900)
            return
        g = screen.availableGeometry()
        w = int(min(g.width() * 0.8, g.height() * 0.86 * 16 / 9))
        self.resize(w, int(w * 9 / 16))

    def make_wide(self) -> None:
        if not (wm.is_hyprland() and self.session.settings.get("window.hyprland_float", True)):
            return

        def work() -> str:
            msg = ""
            for _ in range(8):           # the window may not be mapped yet
                msg = wm.make_window_wide(APP_ID)
                if msg != "own window not found":
                    break
                time.sleep(0.15)
            return msg

        run_async(work, lambda msg: log.info("hyprland: %s", msg))

    # ------------------------------------------------------------------ mouse back/forward
    def eventFilter(self, obj, ev) -> bool:
        if ev.type() != QEvent.Type.MouseButtonPress:
            return False
        try:
            active = self.isActiveWindow() and self.stack.currentWidget() is self.shell
        except RuntimeError:       # during teardown
            return False
        if active:
            b = ev.button()
            if b == Qt.MouseButton.BackButton:
                self.shell.nav.back()
                return True
            if b == Qt.MouseButton.ForwardButton:
                self.shell.nav.forward()
                return True
        return False

    def closeEvent(self, e) -> None:
        # Final position to Jellyfin and close mpv, even if the user quits mid-episode.
        self.shell.player.shutdown()
        super().closeEvent(e)

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

    def _forget_server(self) -> None:
        """Settings → Forget server: back to the startup screen, which asks to pin again."""
        self.shell.player.shutdown()
        self.session.forget_server()
        self.setWindowTitle(APP_NAME)
        self.stack.setCurrentWidget(self.startup)
        self.startup.start()

    def switch_server(self, key: str) -> None:
        """Change to another saved server: same path as a fresh start."""
        if key == self.session.active_server_key and self.stack.currentWidget() is self.shell:
            return
        self.shell.player.shutdown()
        self.login.leave()
        self.stack.setCurrentWidget(self.startup)
        self.session.switch_server(key)
        self.shell.forget_user()
        self.images.forget_failures()
        self.setWindowTitle(APP_NAME)
        self.startup.start()

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
    session = Session()
    theme.set_text_scale(session.settings.get("ui.text_scale", theme.TEXT_SCALE_DEFAULT))
    theme.apply(app)
    smooth.ENABLED = bool(session.settings.get("ui.smooth_scroll", True))
    win = MainWindow(session)
    win.show()
    QTimer.singleShot(0, win.startup.start)
    QTimer.singleShot(120, win.make_wide)
    return app.exec()
