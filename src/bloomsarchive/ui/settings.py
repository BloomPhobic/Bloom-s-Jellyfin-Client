"""Settings page.

Everything saves the moment you change it (addresses wait for *Save addresses*
because a half-typed URL shouldn't trigger a re-probe). The page re-reads the
settings each time it's shown, so changes made in the player (subtitle style,
remembered tracks) appear here too.
"""
from __future__ import annotations

import html
import io
import logging
import platform
import sys
from typing import Callable

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal, qVersion
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QGuiApplication,
                           QLinearGradient, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QApplication, QBoxLayout, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from .. import APP_ID, __version__, desktop, prefs, selfcheck
from ..api import details
from ..api.client import JellyfinError, normalize_base_url
from ..api.discovery import Candidate, probe
from ..player import launcher
from ..session import Session
from . import theme
from .nav import Page
from .servers import set_tone
from .widgets import smooth
from .widgets.flow import flow_widget
from .widgets.image_loader import ImageLoader
from .workers import run_async

log = logging.getLogger(__name__)

CONTENT_WIDTH = 900
LIST_QSS = (f"QListWidget {{ background: {theme.BG}; border: 1px solid {theme.BORDER};"
            f" border-radius: 10px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 5px 8px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: rgba(139, 92, 246, 0.30); }}")


# =========================================================================== small builders

def _label(text: str, role: str | None = None, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    return lab


def _button(text: str, role: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _combo(choices: list[tuple[object, str]], width: int = 260) -> QComboBox:
    cb = QComboBox()
    for value, text in choices:
        cb.addItem(text, value)
    cb.setFixedWidth(width)
    return cb


def _pill(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "pill")
    return lab


def _esc(text) -> str:
    return html.escape(str(text or ""))


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


def _set_combo(cb: QComboBox, value) -> None:
    i = cb.findData(value)
    cb.setCurrentIndex(i if i >= 0 else 0)


class _Blocked:
    """with _Blocked(w1, w2): set values without firing change handlers."""

    def __init__(self, *widgets: QWidget):
        self.widgets = widgets

    def __enter__(self):
        for w in self.widgets:
            w.blockSignals(True)

    def __exit__(self, *_):
        for w in self.widgets:
            w.blockSignals(False)


class Section(QFrame):
    def __init__(self, title: str, blurb: str = ""):
        super().__init__()
        self.setProperty("role", "card")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(26, 22, 26, 22)
        self.v.setSpacing(14)
        self.title_label = _label(title, "h2")
        self.v.addWidget(self.title_label)
        if blurb:
            self.v.addWidget(_label(blurb, "muted"))

    def row(self, title: str, help_text: str, *widgets: QWidget) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(_label(title))
        if help_text:
            text.addWidget(_label(help_text, "hint"))
        h.addLayout(text, 1)
        for w in widgets:
            h.addWidget(w, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.v.addLayout(h)
        return h

    def divider(self) -> None:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER};")
        self.v.addWidget(line)

    def sub(self, title: str) -> None:
        lab = _label(title.upper(), "section")
        lab.setContentsMargins(0, 6, 0, 0)
        lab.setStyleSheet("padding: 0;")
        self.v.addWidget(lab)


# =========================================================================== widgets

class LanguageList(QWidget):
    """Ordered list of language codes with add / up / down / remove."""
    changed = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.codes: list[str] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.list = QListWidget()
        self.list.setFixedHeight(118)
        self.list.setStyleSheet(LIST_QSS)
        v.addWidget(self.list)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.pick = QComboBox()
        self.pick.setEditable(True)
        self.pick.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for c in prefs.COMMON_LANGUAGES:
            self.pick.addItem(prefs.language_label(c), c)
        self.pick.setCurrentIndex(-1)
        self.pick.lineEdit().setPlaceholderText("Add a language…")
        self.pick.setMinimumWidth(190)
        self.add_btn = _button("Add")
        self.up_btn = _button("↑", "icon")
        self.down_btn = _button("↓", "icon")
        self.rm_btn = _button("Remove")
        for b in (self.up_btn, self.down_btn):
            b.setToolTip("Move up" if b is self.up_btn else "Move down")
        row.addWidget(self.pick, 1)
        for b in (self.add_btn, self.up_btn, self.down_btn, self.rm_btn):
            row.addWidget(b)
        v.addLayout(row)
        self.add_btn.clicked.connect(self._add)
        self.pick.lineEdit().returnPressed.connect(self._add)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.rm_btn.clicked.connect(self._remove)
        self.list.currentRowChanged.connect(lambda _r: self._buttons())

    def set_codes(self, codes: list[str]) -> None:
        self.codes = list(codes)
        self._fill(-1)

    def _fill(self, select: int) -> None:
        self.list.clear()
        for i, c in enumerate(self.codes, 1):
            self.list.addItem(f"{i}.  {prefs.language_label(c)}")
        if 0 <= select < len(self.codes):
            self.list.setCurrentRow(select)
        self._buttons()

    def _buttons(self) -> None:
        r = self.list.currentRow()
        self.up_btn.setEnabled(r > 0)
        self.down_btn.setEnabled(0 <= r < len(self.codes) - 1)
        self.rm_btn.setEnabled(r >= 0)

    def _commit(self, codes: list[str], select: int) -> None:
        self.codes = codes
        self._fill(select)
        self.changed.emit(list(codes))

    def _add(self) -> None:
        text = self.pick.currentText()
        i = self.pick.findText(text)
        code = self.pick.itemData(i) if i >= 0 else text
        new = prefs.add_language(self.codes, code or "")
        self.pick.setCurrentIndex(-1)
        self.pick.clearEditText()
        if new != self.codes:
            self._commit(new, len(new) - 1)

    def _move(self, delta: int) -> None:
        r = self.list.currentRow()
        new = prefs.move(self.codes, r, delta)
        if new != self.codes:
            self._commit(new, r + delta)

    def _remove(self) -> None:
        r = self.list.currentRow()
        if 0 <= r < len(self.codes):
            new = self.codes[:r] + self.codes[r + 1:]
            self._commit(new, min(r, len(new) - 1))


class SubtitlePreview(QWidget):
    """Approximation of how mpv will draw plain (non-ASS) subtitles with these settings."""

    SAMPLE = "This is how your subtitles will look"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.style_values = dict(prefs.STYLE_DEFAULTS)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_style(self, values: dict) -> None:
        self.style_values = dict(values)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor("#2b2140"))
        g.setColorAt(0.55, QColor("#3c4a5e"))
        g.setColorAt(1.0, QColor("#6b5a46"))
        frame = QPainterPath()
        frame.addRoundedRect(r, 10, 10)
        p.fillPath(frame, QBrush(g))
        p.setClipPath(frame)

        v = self.style_values
        h = r.height()
        # mpv: sub-font-size 55 at a 720-line reference, times sub-scale; outline likewise.
        k = h / 720.0
        px = max(6.0, 55.0 * float(v.get("sub-scale", 1.0)) * k)
        font = QFont(v.get("sub-font") or self.font().family())
        font.setPixelSize(int(round(px)))
        font.setBold(bool(v.get("sub-bold")))
        path = QPainterPath()
        path.addText(QPointF(0, 0), font, self.SAMPLE)
        br = path.boundingRect()
        pos = max(0, min(150, int(v.get("sub-pos", 100))))
        margin = 22 * k                                   # mpv's sub-margin-y
        baseline_bottom = h * pos / 100.0 - margin
        dx = r.left() + (r.width() - br.width()) / 2 - br.left()
        dy = r.top() + baseline_bottom - br.bottom()
        path.translate(dx, dy)
        border = float(v.get("sub-border-size", 3.0)) * k
        if border > 0:
            pen = QPen(QColor(0, 0, 0, 235), border * 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.strokePath(path, pen)
        p.fillPath(path, QBrush(QColor("#ffffff")))
        p.end()


# =========================================================================== the page

class SettingsPage(Page):
    switch_user = Signal()
    log_out = Signal()
    forget_server = Signal()
    switch_server = Signal(str)        # saved-server key
    add_server = Signal()
    toast = Signal(str, bool)          # message, is_error

    def __init__(self, session: Session, loader: ImageLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = "Settings"
        self.nav_key = "settings"
        self.session = session
        self.settings = session.settings
        self.loader = loader
        self._loaders: list[Callable[[], None]] = []
        self._addr_rows: list[dict] = []
        self._mpv_version: str | None = None
        self._selfcheck_running = False
        self._build()

    # ------------------------------------------------------------------ plumbing
    def _set(self, key: str, value) -> None:
        self.settings.set(key, value)

    def _on_load(self, fn: Callable[[], None]) -> None:
        self._loaders.append(fn)

    def page_shown(self) -> None:
        self._reload()

    def _reload(self) -> None:
        for fn in self._loaders:
            try:
                fn()
            except Exception:                   # one bad value mustn't blank the page
                log.exception("settings: couldn't show a value")

    def check(self, key: str, default: bool, after: Callable[[bool], None] | None = None) -> QCheckBox:
        cb = QCheckBox()
        cb.setCursor(Qt.CursorShape.PointingHandCursor)

        def load() -> None:
            with _Blocked(cb):
                cb.setChecked(bool(self.settings.get(key, default)))

        def changed(v: bool) -> None:
            self._set(key, bool(v))
            if after:
                after(bool(v))

        cb.toggled.connect(changed)
        self._on_load(load)
        return cb

    def choice(self, key: str, choices: list[tuple[object, str]], default, width: int = 260) -> QComboBox:
        cb = _combo(choices, width)

        def load() -> None:
            with _Blocked(cb):
                _set_combo(cb, self.settings.get(key, default))

        cb.currentIndexChanged.connect(lambda _i: self._set(key, cb.currentData()))
        self._on_load(load)
        return cb

    def spin(self, key: str, lo: int, hi: int, default: int, suffix: str = "", width: int = 130) -> QSpinBox:
        sb = QSpinBox()
        sb.setRange(lo, hi)
        sb.setSuffix(suffix)
        sb.setFixedWidth(width)
        sb.setKeyboardTracking(False)

        def load() -> None:
            with _Blocked(sb):
                try:
                    sb.setValue(int(self.settings.get(key, default)))
                except (TypeError, ValueError):
                    sb.setValue(default)

        sb.valueChanged.connect(lambda v: self._set(key, int(v)))
        self._on_load(load)
        return sb

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        self.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self._smooth = smooth.install(self.scroll)

        content = QWidget()
        content.setObjectName("root")
        center = QHBoxLayout(content)
        center.setContentsMargins(32, 28, 32, 48)
        self._center = center
        center.addStretch(1)
        col_w = QWidget()
        col_w.setProperty("role", "plain")
        col_w.setMaximumWidth(CONTENT_WIDTH)
        col_w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.col = QVBoxLayout(col_w)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(18)
        center.addWidget(col_w, 100)
        center.addStretch(1)
        self.scroll.setWidget(content)

        self.col.addWidget(_label("Settings", "h1"))
        jump_w, jump = flow_widget(6)
        self.col.addWidget(jump_w)

        sections = [
            ("Servers", self._servers_section()),
            ("Connection", self._server_section()),
            ("Account", self._account_section()),
            ("Playback", self._playback_section()),
            ("Languages", self._languages_section()),
            ("Subtitles", self._subtitle_section()),
            ("Player", self._player_section()),
            ("App", self._app_section()),
            ("Storage && about", self._about_section()),
        ]
        for name, sec in sections:
            b = _button(name, "chip")
            b.clicked.connect(lambda _=False, s=sec: self._jump(s))
            jump.addWidget(b)
            self.col.addWidget(sec)
        self.col.addStretch(1)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_width()

    def _fit_width(self) -> None:
        """Narrow window or big text: slimmer margins, language lists stacked, so nothing is cut off."""
        w = self.scroll.viewport().width()
        side = 32 if w >= 900 else 16
        self._center.setContentsMargins(side, 28, side, 48)
        need = (sum(ll.minimumSizeHint().width() for ll in self._lang_lists) + 24   # side by side
                + 2 * 26 + 2 * side)                                                  # card + page margins
        want = QBoxLayout.Direction.LeftToRight if w >= need else QBoxLayout.Direction.TopToBottom
        if self._lang_grid.direction() != want:
            self._lang_grid.setDirection(want)

    def _jump(self, section: QWidget) -> None:
        y = section.mapTo(self.scroll.widget(), section.rect().topLeft()).y() - 16
        self._smooth.vertical.scroll_to(max(0, y))

    # ================================================================== server
    def _servers_section(self) -> Section:
        s = Section("Servers",
                    "Each server is a separate Jellyfin install with its own users and libraries. "
                    "To reach the same server another way (Tailscale, a domain), add an address "
                    "under Connection instead.")
        self.srv_box = QVBoxLayout()
        self.srv_box.setSpacing(8)
        s.v.addLayout(self.srv_box)
        h = QHBoxLayout()
        add = _button("+ Add a server")
        add.clicked.connect(self.add_server.emit)
        h.addWidget(add)
        h.addStretch(1)
        s.v.addLayout(h)
        self._on_load(self._srv_fill)
        return s

    def _srv_fill(self) -> None:
        _clear_layout(self.srv_box)
        active = self.session.active_server_key
        d = self.session.discovery
        for entry in self.session.servers():
            key = entry.get("key")
            is_active = key == active
            row = QFrame()
            row.setProperty("role", "serverrow")
            row.setProperty("inuse", "true" if is_active else "false")
            h = QHBoxLayout(row)
            h.setContentsMargins(16, 12, 12, 12)
            h.setSpacing(10)
            text = QVBoxLayout()
            text.setSpacing(2)
            top = QHBoxLayout()
            top.setSpacing(8)
            top.addWidget(_label(f"<b>{_esc(self.session.server_name(entry))}</b>"), 1)
            if is_active:
                top.addWidget(_pill("CONNECTED" if d and d.chosen else "IN USE"), 0,
                              Qt.AlignmentFlag.AlignTop)
            text.addLayout(top)
            addrs = entry.get("addresses") or []
            n = len(addrs)
            bits = [f"{n} address{'es' if n != 1 else ''}"]
            if (entry.get("last_user") or {}).get("name"):
                bits.append(f"last signed in as {entry['last_user']['name']}")
            if not entry.get("server_id"):
                bits.append("not confirmed yet")
            text.addWidget(_label(" · ".join(bits), "hint"))
            h.addLayout(text, 1)
            rename = _button("Rename")
            rename.clicked.connect(lambda _=False, e=entry: self._srv_rename(e))
            h.addWidget(rename)
            if not is_active:
                rm = _button("Remove")
                rm.clicked.connect(lambda _=False, e=entry: self._srv_remove(e))
                use = _button("Switch to", "primary")
                use.clicked.connect(lambda _=False, k=key: self.switch_server.emit(k))
                h.addWidget(rm)
                h.addWidget(use)
            self.srv_box.addWidget(row)

    def _srv_rename(self, entry: dict) -> None:
        name, ok = QInputDialog.getText(self, "Rename server", "Name:", text=self.session.server_name(entry))
        if ok:
            self.session.rename_server(entry["key"], name)
            self._srv_fill()
            self._show_connection()

    def _srv_remove(self, entry: dict) -> None:
        name = self.session.server_name(entry)
        ans = QMessageBox.question(self, "Remove server?",
                                   f"Remove {name} and its saved sign-in from this app? "
                                   "Nothing changes on the server itself.",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                   QMessageBox.StandardButton.Cancel)
        if ans == QMessageBox.StandardButton.Yes:
            self.session.remove_server(entry["key"])
            self._srv_fill()
            self.toast.emit(f"Removed {name}.", False)

    def _server_section(self) -> Section:
        s = Section("Connection",
                    "On start the app reconnects through the address that worked last time, then "
                    "checks all of them in the background and moves to the highest one on this list "
                    "that answers. If the connection drops it checks again.")
        self.conn_title = s.title_label
        self.conn_lbl = _label("")
        self.conn_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.recheck_btn = _button("Re-check now")
        self.recheck_btn.setToolTip("Try every address now and use the best one that answers")
        self.recheck_btn.clicked.connect(self._recheck)
        h = QHBoxLayout()
        h.addWidget(self.conn_lbl, 1)
        h.addWidget(self.recheck_btn, 0, Qt.AlignmentFlag.AlignTop)
        s.v.addLayout(h)
        self._on_load(self._show_connection)

        s.sub("Addresses — tried top to bottom")
        self.addr_box = QVBoxLayout()
        self.addr_box.setSpacing(4)
        s.v.addLayout(self.addr_box)
        btns = QHBoxLayout()
        self.addr_add = _button("+ Add address")
        self.addr_add.clicked.connect(self._addr_add)
        self.addr_save = _button("Save addresses", "primary")
        self.addr_save.clicked.connect(self._addr_save)
        self.addr_msg = _label("", "hint")
        btns.addWidget(self.addr_add)
        btns.addWidget(self.addr_msg, 1)
        btns.addWidget(self.addr_save)
        s.v.addLayout(btns)
        self._on_load(lambda: self._addr_fill(self.settings.get("addresses", []) or []))

        s.divider()
        s.row("Never stream video over the public address",
              "Over Cloudflare you can still browse, but Play asks you to connect Tailscale or come home. "
              "Turn off only if you accept streaming through the tunnel.",
              self.check("network.never_stream_public", True))
        self.forget_btn = _button("Forget server…")
        self.forget_btn.clicked.connect(self._forget_server)
        s.row("Forget this server's ID",
              "Asks “Is this your server?” again on the next connect. Use it if the server was "
              "reinstalled and got a new ServerId. Your sign-in is kept.", self.forget_btn)
        return s

    def _show_connection(self) -> None:
        name = self.session.server_name()
        self.conn_title.setText(f"Connection — {name}")
        d = self.session.discovery
        sid = self.settings.get("server_id") or "not confirmed yet"
        if not (d and d.chosen and d.chosen.info):
            self.conn_lbl.setText(f"Not connected.<br><span style='color:{theme.MUTED}'>ServerId: {sid}</span>")
            return
        c, info = d.chosen.candidate, d.chosen.info
        ms = f" · {d.chosen.latency_ms:.0f} ms" if d.chosen.latency_ms is not None else ""
        where = c.label or c.url
        warn = ("On the public address — video plays only over Tailscale or at home."
                if d.is_public and self.settings.get("network.never_stream_public", True) else "")
        self.conn_lbl.setText(f"Connected to <b>{_esc(info.name)}</b> (Jellyfin {_esc(info.version)}) "
                              f"via <b>{_esc(where)}</b>{ms}<br>"
                              f"<span style='color:{theme.MUTED}'>ServerId: {sid}</span>"
                              + (f"<br><span style='color:{theme.DANGER}'>{warn}</span>" if warn else ""))
        self._addr_marks()

    def _recheck(self) -> None:
        self.recheck_btn.setEnabled(False)
        self.recheck_btn.setText("Checking…")

        def done(changed: bool) -> None:
            self.recheck_btn.setEnabled(True)
            self.recheck_btn.setText("Re-check now")
            self._show_connection()
            d = self.session.discovery
            where = (d.chosen.candidate.label or d.chosen.candidate.url) if d and d.chosen else "nothing"
            self.toast.emit(f"Switched to {where}." if changed else f"Still using {where} — it's the best one.",
                            False)
            if changed:
                self.loader.forget_failures()

        def failed(e: Exception) -> None:
            self.recheck_btn.setEnabled(True)
            self.recheck_btn.setText("Re-check now")
            self.toast.emit(f"Couldn't re-check: {e}", True)

        run_async(self.session.reprobe, done, failed)

    # ---- address editor
    def _addr_current(self) -> list[prefs.AddressRow]:
        return [prefs.AddressRow(r["label"].text(), r["url"].text(), r["public"].isChecked())
                for r in self._addr_rows]

    def _addr_fill(self, addresses) -> None:
        rows = [prefs.AddressRow(a.get("label", ""), a.get("url", ""), bool(a.get("public")))
                if isinstance(a, dict) else a for a in addresses]
        _clear_layout(self.addr_box)
        self._addr_rows = []
        for i, r in enumerate(rows):
            self._addr_rows.append(self._addr_row(i, r, len(rows)))
        self.addr_msg.setText("")
        self._addr_marks()

    def _addr_row(self, i: int, r: prefs.AddressRow, count: int) -> dict:
        w = QFrame()
        w.setProperty("role", "addrrow")
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(3)
        h = QHBoxLayout()
        h.setSpacing(6)
        num = _label(f"{i + 1}.", "muted", wrap=False)
        num.setFixedWidth(20)
        label = QLineEdit(r.label)
        label.setPlaceholderText("Name")
        label.setFixedWidth(130)
        url = QLineEdit(r.url)
        url.setPlaceholderText("http://host:8096")
        public = QCheckBox("Public")
        public.setChecked(r.public)
        public.setToolTip("Reached over the internet (Cloudflare). Tried last, with a longer timeout.")
        test = _button("Test")
        test.setToolTip("Check this address now")
        up = _button("↑", "icon")
        up.setToolTip("Try this one earlier")
        up.setEnabled(i > 0)
        down = _button("↓", "icon")
        down.setToolTip("Try this one later")
        down.setEnabled(i < count - 1)
        rm = _button("✕", "icon")
        rm.setToolTip("Remove")
        rm.setEnabled(count > 1)
        # Two lines, so the URL keeps the full width at any window or text size:
        #   1.  [name] [url ...................................]
        #       IN USE  answered in 3 ms        □ Public  Test  ↑ ↓ ✕
        for x in (num, label):
            h.addWidget(x)
        h.addWidget(url, 1)
        v.addLayout(h)
        foot = QHBoxLayout()
        foot.setContentsMargins(26, 0, 0, 0)
        foot.setSpacing(6)
        pill = _pill("IN USE")
        pill.hide()
        status = _label("", "hint")
        status.hide()
        foot.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
        foot.addWidget(status, 1)
        for x in (public, test, up, down, rm):
            foot.addWidget(x)
        v.addLayout(foot)
        self.addr_box.addWidget(w)
        row = {"frame": w, "label": label, "url": url, "public": public, "status": status, "pill": pill,
               "saved_url": normalize_base_url(r.url)}
        test.clicked.connect(lambda: self._addr_test(row))
        up.clicked.connect(lambda: self._addr_move(i, -1))
        down.clicked.connect(lambda: self._addr_move(i, +1))
        rm.clicked.connect(lambda: self._addr_remove(i))
        url.editingFinished.connect(lambda: self._addr_dirty())
        label.textEdited.connect(lambda _t: self._addr_dirty())
        public.toggled.connect(lambda _v: self._addr_dirty())
        return row

    def _addr_dirty(self) -> None:
        self.addr_msg.setText("Unsaved changes.")
        set_tone(self.addr_msg, "")

    def _addr_marks(self) -> None:
        """Outline the address in use; show what the last check found for the others."""
        d = self.session.discovery
        results = {r.candidate.url: r for r in (d.results if d else [])}
        in_use = d.chosen.candidate.url if d and d.chosen else None
        for row in self._addr_rows:
            u = row["saved_url"]
            on = bool(u) and u == in_use
            frame, pill, status = row["frame"], row["pill"], row["status"]
            frame.setProperty("inuse", "true" if on else "false")
            frame.style().unpolish(frame)
            frame.style().polish(frame)
            pill.setVisible(on)
            res = results.get(u)
            if on:
                ms = f"answered in {res.latency_ms:.0f} ms" if res and res.latency_ms is not None else ""
                text, tone = ms, ""
            elif res is None:
                text, tone = ("Not checked yet — the app stopped at a better address." if results else ""), ""
            elif not res.ok:
                text, tone = "✕ No answer at the last check.", "error"
            elif res.id_match is False:
                text, tone = "✕ A different server answered here — not used.", "error"
            else:
                text, tone = f"Answered in {res.latency_ms:.0f} ms — a higher one on the list is used.", ""
            status.setText(text)
            set_tone(status, tone)
            status.setVisible(bool(text))

    def _addr_add(self) -> None:
        rows = self._addr_current() + [prefs.AddressRow("", "", False)]
        self._addr_fill(rows)
        self._addr_rows[-1]["url"].setFocus()
        last = self._addr_rows[-1]
        last["url"].editingFinished.connect(
            lambda: last["public"].setChecked(prefs.guess_public(last["url"].text())))
        self._addr_dirty()

    def _addr_move(self, i: int, delta: int) -> None:
        self._addr_fill(prefs.move(self._addr_current(), i, delta))
        self._addr_dirty()

    def _addr_remove(self, i: int) -> None:
        rows = self._addr_current()
        del rows[i]
        self._addr_fill(rows)
        self._addr_dirty()

    def _addr_test(self, row: dict) -> None:
        status: QLabel = row["status"]
        rows, err = prefs.validate_addresses([prefs.AddressRow(row["label"].text(), row["url"].text(),
                                                               row["public"].isChecked())])
        status.show()
        if err:
            status.setText(err)
            set_tone(status, "error")
            return
        a = rows[0]
        cand = Candidate(a["url"], a["label"], a["public"])
        status.setText("Testing…")
        set_tone(status, "")
        sid, did = self.settings.get("server_id"), self.session.client.device_id

        def done(res) -> None:
            try:
                good = res.usable
                status.setText(("✓ " if good else "✕ ") + res.describe().split(": ", 1)[-1])
                set_tone(status, "ok" if good else "error")
            except RuntimeError:          # the row was rebuilt meanwhile
                pass

        run_async(lambda: probe(cand, sid, did, timeout=4.0), done)

    def _addr_save(self) -> None:
        rows, err = prefs.validate_addresses(self._addr_current())
        if err:
            self.addr_msg.setText(err)
            set_tone(self.addr_msg, "error")
            return
        set_tone(self.addr_msg, "")
        self.session.set_addresses(rows)
        self._addr_fill(rows)
        self.addr_msg.setText("Saved. Checking which one to use…")
        run_async(self.session.reprobe, lambda _c: (self._show_connection(),
                                                   self.addr_msg.setText("Saved.")),
                  lambda e: self.addr_msg.setText(f"Saved, but none answered: {e}"))

    def _forget_server(self) -> None:
        ans = QMessageBox.question(
            self, "Forget this server?",
            "The app will look for your server again and ask you to confirm it.\n\n"
            "Only confirm a server you recognise — on a shared network another device may answer "
            "at the same address.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if ans == QMessageBox.StandardButton.Yes:
            self.forget_server.emit()

    # ================================================================== account
    def _account_section(self) -> Section:
        s = Section("Account")
        self.user_lbl = _label("")
        sw = _button("Switch user")
        sw.clicked.connect(self.switch_user.emit)
        lo = _button("Log out")
        lo.clicked.connect(self._log_out)
        s.row("Signed in", "", self.user_lbl, sw, lo)
        self._on_load(lambda: self.user_lbl.setText(
            f"<b>{self.session.user.name}</b>" if self.session.user else "—"))
        return s

    def _log_out(self) -> None:
        ans = QMessageBox.question(self, "Log out?",
                                   "This signs you out on the server too; you'll need your password "
                                   "(or Quick Connect) to come back.",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                   QMessageBox.StandardButton.Cancel)
        if ans == QMessageBox.StandardButton.Yes:
            self.log_out.emit()

    # ================================================================== playback
    def _playback_section(self) -> Section:
        s = Section("Playback")
        s.row("Play the next episode automatically", "The AUTO button in the player toggles this too.",
              self.check("playback.autoplay", True))
        s.row("Show Up Next", "How long before the end the Up Next card appears and counts down "
              "(it also appears when the credits start).",
              self.spin("playback.up_next_seconds", 5, 60, 20, " s"))
        still = [(0, "Never ask")] + [(n, f"After {n} episodes") for n in range(2, 11)]
        s.row("Ask “Still watching?”", "After this many episodes in a row that started on their own, "
              "with no clicks, pauses or seeks.",
              self.choice("playback.autoplay_limit", still, 5, width=200))
        s.divider()
        s.row("Intros, recaps and previews", "", self.choice("playback.skip_intro", prefs.SKIP_MODES, "button"))
        s.row("Credits", "With a next episode, skipping the credits starts it (Up Next still shows "
              "unless this is set to skip).",
              self.choice("playback.skip_credits", prefs.SKIP_MODES, "button"))
        s.divider()
        s.row("Rewind when resuming", "Start a little before where you stopped.",
              self.spin("playback.resume_rewind_seconds", 0, 30, 5, " s"))
        s.row("Count as watched after", "Past this point the episode is marked watched.",
              self.spin("playback.played_threshold_pct", 70, 100, 90, " %"))
        return s

    # ================================================================== languages
    def _languages_section(self) -> Section:
        s = Section("Languages",
                    "Used when a series has no remembered choice. The first language a file has wins.")
        grid = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        grid.setSpacing(24)
        self._lang_grid, self._lang_lists = grid, []
        for key, title in (("audio", "Audio, in order of preference"),
                           ("subtitle", "Subtitles, in order of preference")):
            box = QVBoxLayout()
            box.addWidget(_label(title, "fieldlabel"))
            ll = LanguageList()
            ll.changed.connect(lambda codes, k=key: self._set(f"languages.{k}", codes))
            self._on_load(lambda ll=ll, k=key: ll.set_codes(list(self.settings.get(f"languages.{k}", []) or [])))
            box.addWidget(ll)
            self._lang_lists.append(ll)
            grid.addLayout(box, 1)
        s.v.addLayout(grid)
        s.row("When the audio is already in a subtitle language",
              "e.g. English audio with English subtitles preferred.",
              self.choice("languages.subs_when_audio_matches", prefs.SUBS_WHEN_MATCHES, "forced"))

        s.divider()
        s.sub("Remembered per series")
        s.v.addWidget(_label("Audio and subtitles you pick for a series (on its page or in the player) are "
                             "remembered for its other episodes.", "muted"))
        self.mem_list = QListWidget()
        self.mem_list.setFixedHeight(150)
        self.mem_list.setStyleSheet(LIST_QSS)
        self.mem_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        s.v.addWidget(self.mem_list)
        h = QHBoxLayout()
        self.mem_forget = _button("Forget selected")
        self.mem_forget_all = _button("Forget all")
        self.mem_forget.clicked.connect(self._mem_forget)
        self.mem_forget_all.clicked.connect(self._mem_forget_all)
        h.addStretch(1)
        h.addWidget(self.mem_forget)
        h.addWidget(self.mem_forget_all)
        s.v.addLayout(h)
        self.mem_list.itemSelectionChanged.connect(
            lambda: self.mem_forget.setEnabled(bool(self.mem_list.selectedItems())))
        self._on_load(self._mem_fill)
        return s

    def _mem_fill(self) -> None:
        rows = prefs.remembered_series(self.settings.get("series_tracks", {}) or {})
        self.mem_list.clear()
        missing = []
        for sid, name, desc in rows:
            it = QListWidgetItem(f"{name or 'Loading name…'}  —  {desc}")
            it.setData(Qt.ItemDataRole.UserRole, sid)
            self.mem_list.addItem(it)
            if not name:
                missing.append(sid)
        if not rows:
            it = QListWidgetItem("Nothing remembered yet.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.mem_list.addItem(it)
        self.mem_forget.setEnabled(False)
        self.mem_forget_all.setEnabled(bool(rows))
        if missing:
            self._mem_names(missing)

    def _mem_names(self, ids: list[str]) -> None:
        """Older entries were saved without the series name: look it up once and store it."""
        client = self.session.client

        def work() -> dict[str, str]:
            names = {}
            for sid in ids[:40]:
                try:
                    names[sid] = details.get_item(client, sid).name or ""
                except JellyfinError:
                    names[sid] = ""
            return names

        def done(names: dict[str, str]) -> None:
            mem = dict(self.settings.get("series_tracks", {}) or {})
            for sid, name in names.items():
                if sid in mem and isinstance(mem[sid], dict):
                    mem[sid] = {**mem[sid], "name": name or "Removed series"}
            self.settings.set("series_tracks", mem)
            try:
                self._mem_fill()
            except RuntimeError:
                pass

        run_async(work, done)

    def _mem_forget(self) -> None:
        ids = [it.data(Qt.ItemDataRole.UserRole) for it in self.mem_list.selectedItems()]
        mem = dict(self.settings.get("series_tracks", {}) or {})
        for sid in ids:
            mem.pop(sid, None)
        self.settings.set("series_tracks", mem)
        self._mem_fill()

    def _mem_forget_all(self) -> None:
        ans = QMessageBox.question(self, "Forget all?", "Every series goes back to your language preferences.",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                   QMessageBox.StandardButton.Cancel)
        if ans == QMessageBox.StandardButton.Yes:
            self.settings.set("series_tracks", {})
            self._mem_fill()

    # ================================================================== subtitle style
    def _subtitle_section(self) -> Section:
        s = Section("Subtitle appearance",
                    "The same settings as the Aa menu in the player. Only what you change here is "
                    "applied; everything else comes from your mpv.conf.")
        self.preview = SubtitlePreview()
        s.v.addWidget(self.preview)

        self.st_scale = QSpinBox()
        self.st_scale.setRange(30, 400)
        self.st_scale.setSingleStep(10)
        self.st_scale.setSuffix(" %")
        self.st_bold = QCheckBox()
        self.st_font = QComboBox()
        self.st_font.setEditable(True)
        self.st_font.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.st_font.addItem("Default", "")
        for fam in QFontDatabase.families():
            self.st_font.addItem(fam, fam)
        self.st_font.setFixedWidth(260)
        self.st_border = QDoubleSpinBox()
        self.st_border.setRange(0.0, 10.0)
        self.st_border.setSingleStep(0.5)
        self.st_border.setDecimals(1)
        self.st_pos = QSpinBox()
        self.st_pos.setRange(0, 150)
        self.st_pos.setToolTip("100 = bottom of the picture; smaller moves them up")
        self.st_ass = _combo(prefs.ASS_MODES, 200)
        for w in (self.st_scale, self.st_border, self.st_pos):
            w.setFixedWidth(130)
            w.setKeyboardTracking(False)

        s.row("Size", "", self.st_scale)
        s.row("Bold", "", self.st_bold)
        s.row("Font", "Type to search.", self.st_font)
        s.row("Outline", "", self.st_border)
        s.row("Position", "100 is the bottom edge; lower numbers move subtitles up.", self.st_pos)
        s.row("Styled (ASS) subtitles", "Most anime subtitles are styled. “Size only” keeps their look "
              "but applies your size.", self.st_ass)
        h = QHBoxLayout()
        self.st_status = _label("", "muted")
        self.st_reset = _button("Use my mpv.conf instead")
        self.st_reset.clicked.connect(self._style_reset)
        h.addWidget(self.st_status, 1)
        h.addWidget(self.st_reset)
        s.v.addLayout(h)

        self.st_scale.valueChanged.connect(lambda v: self._style_set("sub-scale", round(v / 100.0, 2)))
        self.st_bold.toggled.connect(lambda v: self._style_set("sub-bold", bool(v)))
        self.st_font.activated.connect(lambda _i: self._style_font())
        self.st_font.lineEdit().editingFinished.connect(self._style_font)
        self.st_border.valueChanged.connect(lambda v: self._style_set("sub-border-size", round(float(v), 1)))
        self.st_pos.valueChanged.connect(lambda v: self._style_set("sub-pos", int(v)))
        self.st_ass.currentIndexChanged.connect(lambda _i: self._style_set("sub-ass-override",
                                                                           self.st_ass.currentData()))
        self._on_load(self._style_load)
        return s

    def _style_saved(self) -> dict:
        return dict(self.settings.get("subtitles.saved", {}) or {})

    def _style_load(self) -> None:
        saved = self._style_saved()
        v = {k: prefs.style_value(saved, k) for k in prefs.STYLE_DEFAULTS}
        widgets = (self.st_scale, self.st_bold, self.st_font, self.st_border, self.st_pos, self.st_ass)
        with _Blocked(*widgets):
            self.st_scale.setValue(int(round(float(v["sub-scale"]) * 100)))
            self.st_bold.setChecked(bool(v["sub-bold"]))
            i = self.st_font.findData(v["sub-font"] or "")
            if i >= 0:
                self.st_font.setCurrentIndex(i)
            else:
                self.st_font.setEditText(str(v["sub-font"]))
            self.st_border.setValue(float(v["sub-border-size"]))
            self.st_pos.setValue(int(v["sub-pos"]))
            _set_combo(self.st_ass, v["sub-ass-override"])
        self.preview.set_style(v)
        n = len([k for k in saved if k in prefs.STYLE_DEFAULTS])
        self.st_status.setText("Using your mpv.conf for everything." if n == 0 else
                               f"{n} setting{'s' if n != 1 else ''} changed from your mpv.conf.")
        self.st_reset.setEnabled(n > 0)

    def _style_set(self, prop: str, value) -> None:
        saved = prefs.set_style(self._style_saved(), prop, value)
        self.settings.set("subtitles.saved", saved)
        self._style_load()

    def _style_font(self) -> None:
        text = self.st_font.currentText().strip()
        fam = "" if text in ("", "Default") else text
        if fam != prefs.style_value(self._style_saved(), "sub-font"):
            self._style_set("sub-font", fam)

    def _style_reset(self) -> None:
        self.settings.set("subtitles.saved", {})
        self._style_load()

    # ================================================================== player
    def _player_section(self) -> Section:
        s = Section("Player (mpv)", "Changes apply the next time a video starts.")
        self.mpv_path = QLineEdit()
        self.mpv_path.setPlaceholderText("mpv")
        self.mpv_path.setFixedWidth(260)
        detect = _button("Detect")
        detect.clicked.connect(self._mpv_detect)
        s.row("mpv program", "A name on your PATH or a full path.", self.mpv_path, detect)
        self.mpv_lbl = _label("", "muted")
        s.v.addWidget(self.mpv_lbl)
        self.mpv_path.editingFinished.connect(self._mpv_path_changed)
        self._on_load(lambda: (self.mpv_path.setText(self.settings.get("mpv.path", "mpv") or "mpv"),
                               self._mpv_detect()))

        self.mpv_args = QLineEdit()
        self.mpv_args.setPlaceholderText("--hwdec=auto --volume=80")
        self.mpv_args.setFixedWidth(340)
        s.row("Extra mpv options", "Added to every launch, after the app's own.", self.mpv_args)
        self.mpv_args_msg = _label("", "error")
        self.mpv_args_msg.hide()
        s.v.addWidget(self.mpv_args_msg)
        self.mpv_args.editingFinished.connect(self._mpv_args_changed)
        self._on_load(lambda: self.mpv_args.setText(prefs.args_to_text(self.settings.get("mpv.extra_args", []))))

        s.divider()
        s.row("Use my mpv.conf", "Your ~/.config/mpv settings, scripts and key bindings. Resume points still "
              "come from Jellyfin.", self.check("mpv.inherit_user_config", True))
        s.row("Bloom controls", "The app's on-screen controls, Skip buttons and Up Next. Turn off to use your "
              "own mpv UI (uosc etc.) — then Skip and Up Next aren't shown.",
              self.check("mpv.use_bloom_ui", True))
        s.row("Start fullscreen", "", self.check("mpv.fullscreen", True))
        return s

    def _mpv_path_changed(self) -> None:
        path = self.mpv_path.text().strip() or "mpv"
        if path != self.settings.get("mpv.path", "mpv"):
            self._set("mpv.path", path)
        self._mpv_detect()

    def _mpv_detect(self) -> None:
        path = self.mpv_path.text().strip() or "mpv"
        self.mpv_lbl.setText("Looking for mpv…")

        def done(res) -> None:
            exe, ver = res
            self._mpv_version = ver
            try:
                if exe and ver:
                    self.mpv_lbl.setStyleSheet(f"color: {theme.SUCCESS};")
                    self.mpv_lbl.setText(f"✓ {ver} — {exe}")
                elif exe:
                    self.mpv_lbl.setStyleSheet(f"color: {theme.DANGER};")
                    self.mpv_lbl.setText(f"✕ {exe} didn't run (is it mpv?)")
                else:
                    self.mpv_lbl.setStyleSheet(f"color: {theme.DANGER};")
                    self.mpv_lbl.setText(f"✕ “{path}” wasn't found. Install it with: sudo pacman -S mpv")
                self._show_versions()
            except RuntimeError:
                pass

        run_async(lambda: (launcher.find_mpv(path), launcher.mpv_version(path)), done)

    def _mpv_args_changed(self) -> None:
        args, err = prefs.text_to_args(self.mpv_args.text())
        if err:
            self.mpv_args_msg.setText(err)
            self.mpv_args_msg.show()
            return
        self.mpv_args_msg.hide()
        self._set("mpv.extra_args", args)

    # ================================================================== app
    def _app_section(self) -> Section:
        s = Section("App")
        self.text_size = QSpinBox()
        self.text_size.setRange(theme.TEXT_SCALE_MIN, theme.TEXT_SCALE_MAX)
        self.text_size.setSingleStep(theme.TEXT_SCALE_STEP)
        self.text_size.setSuffix(" %")
        self.text_size.setFixedWidth(130)
        self.text_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_size.setToolTip("Use the arrows, the mouse wheel or ↑/↓")
        reset = _button("Reset")
        reset.setToolTip(f"Back to {theme.TEXT_SCALE_DEFAULT} %")
        reset.clicked.connect(lambda: self.text_size.setValue(theme.TEXT_SCALE_DEFAULT))
        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.setInterval(220)          # let a few arrow clicks land before restyling
        self._text_timer.timeout.connect(self._apply_text_size)
        self.text_size.valueChanged.connect(lambda _v: self._text_timer.start())

        def load() -> None:
            with _Blocked(self.text_size):
                self.text_size.setValue(theme.clamp_text_scale(
                    self.settings.get("ui.text_scale", theme.TEXT_SCALE_DEFAULT)))

        self._on_load(load)
        s.row("Text size", f"Makes all text in the app bigger or smaller, from {theme.TEXT_SCALE_MIN} % "
              f"to {theme.TEXT_SCALE_MAX} % — the range every screen is laid out for. Changes apply at once.",
              self.text_size, reset)
        s.row("Smooth scrolling", "Mouse-wheel notches glide instead of jumping. Touchpads always scroll natively.",
              self.check("ui.smooth_scroll", True, after=lambda v: setattr(smooth, "ENABLED", v)))
        s.row("Wide floating window on Hyprland", "Floats the main window at 16:9 and centres it. "
              "Applies from the next start.", self.check("window.hyprland_float", True))
        return s

    def _apply_text_size(self) -> None:
        pct = theme.apply_text_scale(QApplication.instance(), self.text_size.value())
        self._set("ui.text_scale", pct)

    # ================================================================== storage & about
    def _about_section(self) -> Section:
        s = Section("Storage & about")
        self.cache_lbl = _label("", "muted", wrap=False)
        clear = _button("Clear")
        clear.clicked.connect(self._cache_clear)
        s.row("Image cache", "Posters and artwork kept on disk so pages open fast. It refills as you browse.",
              self.cache_lbl, clear)
        self._on_load(self._cache_size)

        open_dir = _button("Open folder")
        open_dir.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.settings.path.parent))))
        s.row("Settings file", str(self.settings.path), open_dir)

        self.launcher_btn = _button("Install")
        self.launcher_btn.clicked.connect(self._install_launcher)
        s.row("App launcher entry", "Adds Bloom's Archive to your app launcher (and fixes the portal app-id "
              "warning).", self.launcher_btn)
        self._on_load(lambda: self.launcher_btn.setText(
            "Reinstall" if (desktop.applications_dir() / f"{APP_ID}.desktop").exists() else "Install"))

        reset = _button("Reset…")
        reset.setStyleSheet(f"QPushButton {{ color: {theme.DANGER}; }} "
                            f"QPushButton:hover {{ border-color: {theme.DANGER}; }}")
        reset.clicked.connect(self._reset_all)
        s.row("Reset all settings", "Back to defaults. Keeps your server, its addresses and your sign-in.", reset)

        s.divider()
        s.sub("About")
        self.versions_lbl = _label("")
        self.versions_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        s.v.addWidget(self.versions_lbl)
        self._on_load(self._show_versions)

        h = QHBoxLayout()
        self.sc_btn = _button("Run self-check")
        self.sc_copy = _button("Copy report")
        self.sc_copy.hide()
        h.addWidget(_label("Checks addresses, sign-in, API routes, skip segments and direct play — "
                           "paste the report when something's wrong.", "muted"), 1)
        h.addWidget(self.sc_copy)
        h.addWidget(self.sc_btn)
        s.v.addLayout(h)
        self.sc_out = QPlainTextEdit()
        self.sc_out.setReadOnly(True)
        self.sc_out.setFixedHeight(300)
        self.sc_out.setProperty("role", "mono")
        self.sc_out.hide()
        s.v.addWidget(self.sc_out)
        self.sc_btn.clicked.connect(self._selfcheck)
        self.sc_copy.clicked.connect(self._selfcheck_copy)
        return s

    def _show_versions(self) -> None:
        d = self.session.discovery
        server = (f"{d.chosen.info.name} · Jellyfin {d.chosen.info.version}"
                  if d and d.chosen and d.chosen.info else "not connected")
        rows = [("Bloom's Archive", __version__), ("Server", server),
                ("mpv", self._mpv_version or "—"),
                ("Qt", f"{qVersion()} (PySide6 {PYSIDE_VERSION})"),
                ("Python", f"{platform.python_version()} on {sys.platform}")]
        self.versions_lbl.setText("<table cellspacing='0' cellpadding='3'>" + "".join(
            f"<tr><td style='color:{theme.MUTED}; padding-right:18px'>{k}</td><td>{v}</td></tr>"
            for k, v in rows) + "</table>")

    def _cache_size(self) -> None:
        cache = self.loader.cache
        self.cache_lbl.setText("…")

        def done(n: int) -> None:
            try:
                self.cache_lbl.setText(prefs.human_bytes(n))
            except RuntimeError:
                pass

        run_async(cache.size_bytes, done)

    def _cache_clear(self) -> None:
        cache = self.loader.cache
        self.cache_lbl.setText("Clearing…")

        def done(n: int) -> None:
            self.loader.clear()
            self.toast.emit(f"Image cache cleared ({n} files).", False)
            self._cache_size()

        run_async(cache.clear, done, lambda e: self.toast.emit(f"Couldn't clear the cache: {e}", True))

    def _install_launcher(self) -> None:
        try:
            path = desktop.install()
        except OSError as e:
            self.toast.emit(f"Couldn't install the launcher entry: {e}", True)
            return
        self.launcher_btn.setText("Reinstall")
        self.toast.emit(f"Launcher entry installed: {path}", False)

    def _reset_all(self) -> None:
        ans = QMessageBox.question(
            self, "Reset all settings?",
            "Playback, languages, subtitle style, remembered series, library sort/filters and player "
            "options go back to their defaults.\n\nYour server, its addresses and your sign-in are kept.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if ans != QMessageBox.StandardButton.Reset:
            return
        self.settings.reset_preferences()
        smooth.ENABLED = bool(self.settings.get("ui.smooth_scroll", True))
        self._reload()
        self.toast.emit("Settings reset to defaults.", False)

    def _selfcheck(self) -> None:
        if self._selfcheck_running:
            return
        self._selfcheck_running = True
        self.sc_btn.setEnabled(False)
        self.sc_btn.setText("Running…")
        self.sc_out.show()
        self.sc_out.setPlainText("Running the self-check… (this can take a few seconds)")
        settings = self.settings

        def work() -> tuple[int, str]:
            buf = io.StringIO()
            try:
                code = selfcheck.run(settings, out=buf, interactive=False)
            except Exception as e:           # report whatever we got
                buf.write(f"\nself-check crashed: {e!r}\n")
                code = 2
            return code, buf.getvalue()

        def done(res: tuple[int, str]) -> None:
            self._selfcheck_running = False
            self.sc_btn.setEnabled(True)
            self.sc_btn.setText("Run again")
            self.sc_out.setPlainText(res[1])
            self.sc_copy.show()

        run_async(work, done)

    def _selfcheck_copy(self) -> None:
        QGuiApplication.clipboard().setText(self.sc_out.toPlainText())
        self.toast.emit("Self-check report copied.", False)
