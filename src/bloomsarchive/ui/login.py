"""Login: profile tiles, password, manual login, Quick Connect.

Every network call runs in a worker against a throwaway login client and
returns an AuthResult. Only results whose generation is still current reach
Session.complete_login, so pressing Back/Cancel mid-request can never leave a
half-finished sign-in behind.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..api import auth
from ..api.client import ConnectionFailed, HttpError
from ..api.models import User
from ..session import Session
from .widgets.avatar import AvatarTile
from .widgets.image_loader import ImageLoader
from .workers import run_async

log = logging.getLogger(__name__)

QC_POLL_MS = 3000
MAX_TILES_PER_ROW = 5


def _label(text: str = "", role: str | None = None, center: bool = True, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    if center:
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lab.setWordWrap(wrap)
    return lab


def _button(text: str, role: str | None = None, large: bool = False) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    if large:
        b.setProperty("size", "large")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _field(placeholder: str, password: bool = False) -> QLineEdit:
    f = QLineEdit()
    f.setPlaceholderText(placeholder)
    if password:
        f.setEchoMode(QLineEdit.EchoMode.Password)
    f.setMinimumHeight(40)
    return f


def describe_error(e: Exception) -> str:
    if isinstance(e, auth.AuthError):
        return str(e)
    if isinstance(e, ConnectionFailed):
        return "Can't reach the server right now."
    if isinstance(e, HttpError):
        return f"The server said no ({e.status})."
    return str(e) or type(e).__name__


class LoginScreen(QWidget):
    signed_in = Signal(object)      # User

    def __init__(self, session: Session, loader: ImageLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("root")
        self.session = session
        self.loader = loader
        self._gen = 0
        self._users: list[User] = []
        self._tiles: list[AvatarTile] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._selected: User | None = None
        self._qc: auth.QuickConnectSession | None = None
        self._qc_polling = False
        self._qc_enabled = True
        self._open_last_after_load = False

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(QC_POLL_MS)
        self._poll_timer.timeout.connect(self._qc_poll)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._qc_tick)

        self._build()

    # ================================================================== layout
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 16)
        outer.addStretch(2)
        outer.addWidget(_label(APP_NAME, "wordmark"))
        outer.addWidget(_label("Who's watching?", "subtitle"))
        outer.addSpacing(30)

        self.tiles_host = QWidget()
        self.tiles_layout = QVBoxLayout(self.tiles_host)
        self.tiles_layout.setContentsMargins(0, 0, 0, 0)
        self.tiles_layout.setSpacing(8)
        outer.addWidget(self.tiles_host)
        outer.addSpacing(22)

        card = QFrame()
        card.setProperty("role", "card")
        card.setFixedWidth(440)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(26, 22, 26, 22)
        self.panels = QStackedWidget()
        cl.addWidget(self.panels)
        self.panels.addWidget(self._build_choose())
        self.panels.addWidget(self._build_password())
        self.panels.addWidget(self._build_manual())
        self.panels.addWidget(self._build_qc())

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(card)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch(3)

        self.footer = _label("", "muted")
        outer.addWidget(self.footer)

    def _build_choose(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        self.notice = _label("", "notice")
        self.notice.hide()
        self.choose_status = _label("Loading profiles…", "muted")
        self.qc_btn = _button("Sign in with Quick Connect", "primary", large=True)
        self.qc_btn.clicked.connect(self._qc_start)
        links = QHBoxLayout()
        links.addStretch()
        self.manual_link = _button("Manual login", "link")
        self.manual_link.clicked.connect(self._show_manual)
        self.retry_link = _button("Retry", "link")
        self.retry_link.clicked.connect(self._load_users)
        self.retry_link.hide()
        links.addWidget(self.manual_link)
        links.addWidget(self.retry_link)
        links.addStretch()
        v.addWidget(self.notice)
        v.addWidget(self.choose_status)
        v.addWidget(self.qc_btn)
        v.addLayout(links)
        return w

    def _build_password(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        self.pw_title = _label("", "h2")
        self.pw_field = _field("Password (leave blank if none)", password=True)
        self.pw_field.returnPressed.connect(self._submit_password)
        self.pw_error = _label("", "error")
        self.pw_submit = _button("Sign in", "primary", large=True)
        self.pw_submit.clicked.connect(self._submit_password)
        back = _button("Back", "link")
        back.clicked.connect(self._back)
        v.addWidget(self.pw_title)
        v.addWidget(self.pw_field)
        v.addWidget(self.pw_error)
        v.addWidget(self.pw_submit)
        v.addWidget(back, alignment=Qt.AlignmentFlag.AlignHCenter)
        return w

    def _build_manual(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        v.addWidget(_label("Manual login", "h2"))
        self.mn_user = _field("Username")
        self.mn_pw = _field("Password (leave blank if none)", password=True)
        self.mn_user.returnPressed.connect(self.mn_pw.setFocus)
        self.mn_pw.returnPressed.connect(self._submit_manual)
        self.mn_error = _label("", "error")
        self.mn_submit = _button("Sign in", "primary", large=True)
        self.mn_submit.clicked.connect(self._submit_manual)
        back = _button("Back", "link")
        back.clicked.connect(self._back)
        for x in (self.mn_user, self.mn_pw, self.mn_error, self.mn_submit):
            v.addWidget(x)
        v.addWidget(back, alignment=Qt.AlignmentFlag.AlignHCenter)
        return w

    def _build_qc(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        v.addWidget(_label("Quick Connect", "h2"))
        self.qc_code = _label("··· ···", "qccode")
        self.qc_code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.qc_help = _label("On a device that's already signed in, open your Jellyfin "
                              "profile menu → Quick Connect, and enter this code.", "muted")
        self.qc_status = _label("", "muted")
        row = QHBoxLayout()
        row.addStretch()
        self.qc_new = _button("Get a new code", "primary")
        self.qc_new.clicked.connect(self._qc_start)
        self.qc_new.hide()
        qc_cancel = _button("Cancel")
        qc_cancel.clicked.connect(self._back)
        row.addWidget(self.qc_new)
        row.addWidget(qc_cancel)
        row.addStretch()
        for x in (self.qc_code, self.qc_help, self.qc_status):
            v.addWidget(x)
        v.addLayout(row)
        return w

    # ================================================================== entry
    def enter(self, message: str = "", open_last: bool = False) -> None:
        """Show the screen fresh. `message` appears as a notice (e.g. session expired)."""
        self._cancel_pending()
        self.notice.setText(message)
        self.notice.setVisible(bool(message))
        self._update_footer()
        self.panels.setCurrentIndex(0)
        self._open_last_after_load = open_last
        self._load_users()

    def _update_footer(self) -> None:
        d = self.session.discovery
        if d and d.chosen and d.chosen.info:
            c = d.chosen.candidate
            self.footer.setText(f"{d.chosen.info.name} · Jellyfin {d.chosen.info.version} · "
                                f"via {c.label or c.url}")
        else:
            self.footer.setText("")

    def _load_users(self) -> None:
        self.choose_status.setText("Loading profiles…")
        self.retry_link.hide()
        lc = self.session.login_client()
        self._async(lambda: (auth.public_users(lc), auth.quick_connect_enabled(lc)),
                    self._users_loaded, self._users_failed)

    def _users_loaded(self, result: tuple[list[User], bool]) -> None:
        users, qc_enabled = result
        self._qc_enabled = qc_enabled
        self.qc_btn.setVisible(qc_enabled)
        self._set_users(users)
        if users:
            self.choose_status.setText("Choose your profile" + (", or use Quick Connect." if qc_enabled else "."))
        else:
            self.choose_status.setText("No public profiles here — use "
                                       + ("Quick Connect or " if qc_enabled else "")
                                       + "Manual login.")
        last = self.session.settings.get("last_user") or {}
        tile = next((t for t in self._tiles if t.user.id == last.get("id")), None)
        if tile:
            tile.setChecked(True)
            tile.setFocus()
            if self._open_last_after_load:
                self._show_password(tile.user)

    def _users_failed(self, e: Exception) -> None:
        self._set_users([])
        self.choose_status.setText(f"Couldn't load profiles. {describe_error(e)}")
        self.retry_link.show()

    def _set_users(self, users: list[User]) -> None:
        for t in self._tiles:
            self._group.removeButton(t)
            t.deleteLater()
        self._tiles = []
        while self.tiles_layout.count():
            item = self.tiles_layout.takeAt(0)
            if item.layout():
                item.layout().deleteLater()
        self._users = users
        row: QHBoxLayout | None = None
        for i, u in enumerate(users):
            if i % MAX_TILES_PER_ROW == 0:
                if row is not None:
                    row.addStretch()
                row = QHBoxLayout()
                row.setSpacing(18)
                row.addStretch()
                self.tiles_layout.addLayout(row)
            tile = AvatarTile(u, self.loader)
            tile.clicked.connect(lambda _=False, user=u: self._tile_clicked(user))
            self._group.addButton(tile)
            self._tiles.append(tile)
            assert row is not None
            row.addWidget(tile)
        if row is not None:
            row.addStretch()
        self.tiles_host.setVisible(bool(users))

    # ================================================================== profiles + password
    def _tile_clicked(self, user: User) -> None:
        self._cancel_pending()
        self._selected = user
        if self.session.has_stored_token(user.id):
            self._busy_status(f"Signing in as {user.name}…")
            self._async(lambda: self.session.try_stored_token(user.id),
                        lambda u: self._finish(u) if u else self._show_password(user),
                        lambda _e: self._show_password(user))
        elif not user.has_password:
            self._busy_status(f"Signing in as {user.name}…")
            lc = self.session.login_client()
            self._async(lambda: auth.login_password(lc, user.name, ""),
                        self._complete,
                        lambda _e: self._show_password(user))
        else:
            self._show_password(user)

    def _busy_status(self, text: str) -> None:
        self.panels.setCurrentIndex(0)
        self.choose_status.setText(text)

    def _show_password(self, user: User, error: str = "") -> None:
        self._selected = user
        self.pw_title.setText(f"Sign in as {user.name}")
        self.pw_field.clear()
        self.pw_error.setText(error)
        self._set_busy(self.pw_submit, False)
        self.panels.setCurrentIndex(1)
        self.pw_field.setFocus()

    def _submit_password(self) -> None:
        if self._selected is None or not self.pw_submit.isEnabled():
            return
        name, pw = self._selected.name, self.pw_field.text()
        self.pw_error.setText("")
        self._set_busy(self.pw_submit, True)
        lc = self.session.login_client()
        self._async(lambda: auth.login_password(lc, name, pw), self._complete,
                    lambda e: self._fail(self.pw_submit, self.pw_error, e, self.pw_field))

    # ================================================================== manual
    def _show_manual(self) -> None:
        self._cancel_pending()
        self.mn_user.clear()
        self.mn_pw.clear()
        self.mn_error.setText("")
        self._set_busy(self.mn_submit, False)
        self.panels.setCurrentIndex(2)
        self.mn_user.setFocus()

    def _submit_manual(self) -> None:
        if not self.mn_submit.isEnabled():
            return
        name, pw = self.mn_user.text().strip(), self.mn_pw.text()
        if not name:
            self.mn_error.setText("Enter a username.")
            self.mn_user.setFocus()
            return
        self.mn_error.setText("")
        self._set_busy(self.mn_submit, True)
        lc = self.session.login_client()
        self._async(lambda: auth.login_password(lc, name, pw), self._complete,
                    lambda e: self._fail(self.mn_submit, self.mn_error, e, self.mn_pw))

    # ================================================================== quick connect
    def _qc_start(self) -> None:
        self._cancel_pending()
        self.panels.setCurrentIndex(3)
        self.qc_code.setText("··· ···")
        self.qc_status.setText("Getting a code…")
        self.qc_new.hide()
        lc = self.session.login_client()

        def initiate() -> auth.QuickConnectSession:
            qc = auth.QuickConnectSession(lc)
            qc.initiate()
            return qc

        self._async(initiate, self._qc_ready, self._qc_failed)

    def _qc_ready(self, qc: auth.QuickConnectSession) -> None:
        self._qc = qc
        code = qc.code
        self.qc_code.setText(f"{code[:3]} {code[3:]}" if len(code) == 6 else code)
        self._qc_tick()
        self._poll_timer.start()
        self._tick_timer.start()

    def _qc_tick(self) -> None:
        qc = self._qc
        if not qc:
            return
        left = max(0, int(qc.timeout - (time.monotonic() - qc.started_at)))
        self.qc_status.setText(f"Waiting for approval · {left // 60}:{left % 60:02d}")

    def _qc_poll(self) -> None:
        qc = self._qc
        if not qc or self._qc_polling:
            return
        self._qc_polling = True

        def done(result: auth.AuthResult | None) -> None:
            self._qc_polling = False
            if result:
                self._stop_qc()
                self.qc_status.setText("Approved — signing in…")
                self._complete(result)

        def failed(e: Exception) -> None:
            self._qc_polling = False
            self._qc_failed(e)

        self._async(qc.poll, done, failed)

    def _qc_failed(self, e: Exception) -> None:
        self._stop_qc()
        self.qc_status.setText(describe_error(e))
        self.qc_new.show()

    def _stop_qc(self) -> None:
        self._poll_timer.stop()
        self._tick_timer.stop()
        self._qc = None
        self._qc_polling = False

    # ================================================================== shared
    def _async(self, fn: Callable[[], Any], on_done: Callable[[Any], None],
               on_error: Callable[[Exception], None]) -> None:
        """run_async, but results are dropped if the user has moved on (Back/Cancel)."""
        gen = self._gen

        def guard(cb):
            def wrapped(value):
                if gen == self._gen:
                    cb(value)
                else:
                    log.debug("dropping stale login result")
            return wrapped

        run_async(fn, guard(on_done), guard(on_error))

    def _cancel_pending(self) -> None:
        self._gen += 1
        self._stop_qc()

    def _complete(self, result: auth.AuthResult) -> None:
        # Fresh generation: the apply step can't be cancelled by a late Back press.
        self._gen += 1
        self._async(lambda: self.session.complete_login(result), self._finish, self._apply_failed)

    def _apply_failed(self, e: Exception) -> None:
        self.panels.setCurrentIndex(0)
        self.choose_status.setText(f"Signed in, but couldn't save the session: {describe_error(e)}")

    def _finish(self, user: User) -> None:
        self._cancel_pending()
        self.signed_in.emit(user)

    def _fail(self, button: QPushButton, label: QLabel, e: Exception, focus: QLineEdit) -> None:
        self._set_busy(button, False)
        label.setText(describe_error(e))
        focus.selectAll()
        focus.setFocus()

    @staticmethod
    def _set_busy(button: QPushButton, busy: bool) -> None:
        button.setEnabled(not busy)
        button.setText("Signing in…" if busy else "Sign in")

    def _back(self) -> None:
        self._cancel_pending()
        self.panels.setCurrentIndex(0)
        if self._users:
            self.choose_status.setText("Choose your profile" + (", or use Quick Connect." if self._qc_enabled else "."))

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape and self.panels.currentIndex() != 0:
            self._back()
            return
        super().keyPressEvent(e)

    def leave(self) -> None:
        """Called when navigating away: stop timers and drop in-flight results."""
        self._cancel_pending()
