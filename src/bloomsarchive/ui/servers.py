"""Saved servers: the "switch server" menu and the "Add a server" dialog.

A server here is a whole Jellyfin install (its own ServerId, users and
libraries), not another address of the one you use; those live in its
address list.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QVBoxLayout, QWidget)

from .. import prefs
from ..api.discovery import Candidate, ProbeResult, probe
from ..session import Session
from .workers import run_async


def fill_server_menu(menu: QMenu, session: Session, on_pick: Callable[[str], None],
                     include_active: bool = True) -> int:
    """Rebuild `menu` with one entry per saved server; returns how many others there are."""
    menu.clear()
    active = session.active_server_key
    others = 0
    for entry in session.servers():
        key = entry.get("key")
        is_active = key == active
        if is_active and not include_active:
            continue
        act = QAction(session.server_name(entry), menu)
        if is_active:
            act.setCheckable(True)
            act.setChecked(True)
            act.setEnabled(False)
        else:
            others += 1
            act.triggered.connect(lambda _=False, k=key: on_pick(k))
        menu.addAction(act)
    return others


def set_tone(label: QLabel, tone: str) -> None:
    """Recolour a hint label: "" (muted), "ok" or "error"."""
    label.setProperty("tone", tone)
    label.style().unpolish(label)
    label.style().polish(label)


class AddServerDialog(QDialog):
    """Name + first address. Checks the address and refuses one of your existing servers."""

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Add a server")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.result_key: str | None = None
        self._probe: ProbeResult | None = None
        self._gen = 0

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(12)
        t = QLabel("Add a server")
        t.setProperty("role", "h2")
        v.addWidget(t)
        blurb = QLabel("A different Jellyfin server — a friend's, or a second one of yours. To reach the "
                       "server you already use by another route (Tailscale, a domain), add an address "
                       "to it instead.")
        blurb.setProperty("role", "muted")
        blurb.setWordWrap(True)
        v.addWidget(blurb)

        self.url = QLineEdit()
        self.url.setPlaceholderText("http://192.168.1.20:8096  or  https://jellyfin.example.com")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name (optional — taken from the server)")
        self.public = QCheckBox("Reached over the internet")
        self.public.setToolTip("Tried last with a longer timeout, and video won't stream over it "
                               "while “Never stream video over the public address” is on.")
        v.addWidget(self._field("Address", self.url))
        v.addWidget(self._field("Name", self.name))
        v.addWidget(self.public)

        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.status)

        btns = QHBoxLayout()
        self.check_btn = QPushButton("Check")
        cancel = QPushButton("Cancel")
        self.add_btn = QPushButton("Add server")
        self.add_btn.setProperty("role", "primary")
        for b in (self.check_btn, cancel, self.add_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        btns.addWidget(self.check_btn)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(self.add_btn)
        v.addLayout(btns)

        self.check_btn.clicked.connect(self._check)
        cancel.clicked.connect(self.reject)
        self.add_btn.clicked.connect(self._add)
        self.url.returnPressed.connect(self._check)
        self.url.textEdited.connect(self._edited)
        self.public.toggled.connect(lambda _v: self._edited())

    @staticmethod
    def _field(title: str, w: QWidget) -> QWidget:
        box = QWidget()
        h = QVBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        lab = QLabel(title.upper())
        lab.setProperty("role", "fieldlabel")
        h.addWidget(lab)
        h.addWidget(w)
        return box

    def _edited(self, *_a) -> None:
        self._probe = None
        self._gen += 1
        self.status.setText("")

    def _row(self) -> tuple[dict | None, str]:
        rows, err = prefs.validate_addresses([prefs.AddressRow("", self.url.text(),
                                                               self.public.isChecked())])
        if err:
            return None, err
        if not rows:
            return None, "Type the server's address."
        return rows[0], ""

    def _check(self, then_add: bool = False) -> None:
        row, err = self._row()
        if err:
            self.status.setText(err)
            set_tone(self.status, "error")
            return
        self._gen += 1
        gen = self._gen
        self.status.setText("Checking…")
        set_tone(self.status, "")
        self.check_btn.setEnabled(False)
        cand = Candidate(row["url"], row["label"], row["public"])
        did = self.session.client.device_id

        def done(res: ProbeResult) -> None:
            if gen != self._gen:
                return
            self.check_btn.setEnabled(True)
            self._probe = res
            self._show_probe(res)
            if then_add:
                self._add()

        run_async(lambda: probe(cand, None, did, timeout=5.0), done)

    def _duplicate(self, res: ProbeResult) -> dict | None:
        if res.ok and res.info and res.info.id:
            return self.session.find_server_by_id(res.info.id)
        return None

    def _show_probe(self, res: ProbeResult) -> None:
        dup = self._duplicate(res)
        if not res.ok:
            self.status.setText(f"✕ No answer — {res.error}\nYou can still add it if the server is "
                                "off right now.")
            set_tone(self.status, "error")
        elif dup is not None:
            self.status.setText(f"✕ That's {self.session.server_name(dup)}, which you already have. "
                                "To use this address for it, add it under Settings → Addresses.")
            set_tone(self.status, "error")
        else:
            info = res.info
            assert info
            self.status.setText(f"✓ Found “{info.name}” — Jellyfin {info.version}, "
                                f"{res.latency_ms:.0f} ms\nServerId {info.id}")
            set_tone(self.status, "ok")
            if not self.name.text().strip():
                self.name.setPlaceholderText(info.name or self.name.placeholderText())

    def _add(self) -> None:
        row, err = self._row()
        if err:
            self.status.setText(err)
            set_tone(self.status, "error")
            return
        if self._probe is None:            # check first, then come back here
            self._check(then_add=True)
            return
        if self._duplicate(self._probe) is not None:
            return
        name = self.name.text().strip()
        if not name and self._probe.ok and self._probe.info:
            name = self._probe.info.name or ""
        self.result_key = self.session.add_server(name, [row])
        self.accept()
