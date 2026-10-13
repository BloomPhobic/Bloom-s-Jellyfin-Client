"""mpv JSON IPC over QLocalSocket (unix socket now, named pipe on Windows later).

Newline-delimited JSON. Commands carry a request_id; replies are routed to
the caller's callback. Property changes and events become Qt signals.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

log = logging.getLogger(__name__)

Reply = Callable[[str | None, Any], None]      # (error or None, data)


class MpvIpc(QObject):
    connected = Signal()
    connect_failed = Signal(str)
    disconnected = Signal()
    property_changed = Signal(str, object)    # name, value
    event = Signal(str, object)               # event name, full message dict

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.sock = QLocalSocket(self)
        self.sock.readyRead.connect(self._read)
        self.sock.connected.connect(self._on_connected)
        self.sock.disconnected.connect(self.disconnected)
        self._buf = b""
        self._next_id = 1
        self._pending: dict[int, Reply] = {}
        self._observed: dict[int, str] = {}
        self._path = ""
        self._deadline = 0
        self._retry = QTimer(self)
        self._retry.setInterval(100)
        self._retry.timeout.connect(self._try_connect)
        self._tries = 0

    # ------------------------------------------------------------------ connection
    @property
    def is_connected(self) -> bool:
        return self.sock.state() == QLocalSocket.LocalSocketState.ConnectedState

    def connect_to(self, path: str, timeout_ms: int = 10000) -> None:
        """mpv creates the socket a moment after it starts, so keep trying for a while."""
        self._path = path
        self._tries = 0
        self._deadline = timeout_ms // self._retry.interval()
        self._retry.start()
        self._try_connect()

    def _try_connect(self) -> None:
        if self.is_connected:
            self._retry.stop()
            return
        self._tries += 1
        if self._tries > self._deadline:
            self._retry.stop()
            self.connect_failed.emit(f"mpv didn't open its control socket ({self.sock.errorString()})")
            return
        if self.sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self.sock.connectToServer(self._path)

    def _on_connected(self) -> None:
        self._retry.stop()
        self.connected.emit()

    def close(self) -> None:
        self._retry.stop()
        self._pending.clear()
        self.sock.abort()

    # ------------------------------------------------------------------ sending
    def command(self, *args: Any, reply: Reply | None = None) -> int:
        rid = self._next_id
        self._next_id += 1
        if reply:
            self._pending[rid] = reply
        line = json.dumps({"command": list(args), "request_id": rid}) + "\n"
        if self.is_connected:
            self.sock.write(line.encode("utf-8"))
            self.sock.flush()
        else:
            log.debug("mpv ipc not connected; dropped %s", args[:1])
        return rid

    def set_property(self, name: str, value: Any, reply: Reply | None = None) -> int:
        return self.command("set_property", name, value, reply=reply)

    def get_property(self, name: str, reply: Reply) -> int:
        return self.command("get_property", name, reply=reply)

    def observe(self, *names: str) -> None:
        for name in names:
            oid = len(self._observed) + 1
            self._observed[oid] = name
            self.command("observe_property", oid, name)

    # ------------------------------------------------------------------ receiving
    def _read(self) -> None:
        self._buf += bytes(self.sock.readAll())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                log.debug("bad ipc line: %r", line[:200])
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        ev = msg.get("event")
        if ev == "property-change":
            self.property_changed.emit(msg.get("name", ""), msg.get("data"))
            return
        if ev:
            self.event.emit(ev, msg)
            return
        rid = msg.get("request_id")
        cb = self._pending.pop(rid, None) if rid is not None else None
        if cb:
            err = msg.get("error")
            try:
                cb(None if err in (None, "success") else err, msg.get("data"))
            except Exception:
                log.exception("ipc reply handler failed")
