"""App-level session state, free of Qt so it can be tested directly.

Owns settings, the token store and the one JellyfinClient. The UI calls these
blocking methods from workers and reacts to the results.
"""
from __future__ import annotations

import logging
import threading

from .api import auth
from .api.client import ConnectionFailed, JellyfinClient, JellyfinError, Unauthorized
from .api.discovery import Candidate, Discovery, discover
from .api.models import User
from .config import Settings, TokenStore

log = logging.getLogger(__name__)


class Session:
    def __init__(self, settings: Settings | None = None, tokens: TokenStore | None = None):
        self.settings = settings or Settings()
        self.tokens = tokens or TokenStore(self.settings)
        self.client = JellyfinClient("", device_id=self.settings.device_id())
        self.discovery: Discovery | None = None
        self.user: User | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ addresses
    def candidates(self) -> list[Candidate]:
        return Candidate.from_settings(self.settings.get("addresses", []))

    def _discover(self, last_good: str | None) -> Discovery:
        net = self.settings.get("network", {})
        return discover(self.candidates(), self.settings.get("server_id"), self.client.device_id,
                        last_good=last_good,
                        private_timeout=net.get("probe_timeout_private", 1.5),
                        public_timeout=net.get("probe_timeout_public", 3.0))

    def connect(self) -> Discovery:
        """Startup: last working address first (fast), else full priority probe.

        If the result came from the fast path, call reprobe() in the background.
        """
        d = self._discover(self.settings.get("last_address"))
        self._adopt(d)
        return d

    @property
    def used_fast_path(self) -> bool:
        return bool(self.discovery and len(self.discovery.results) == 1
                    and self.discovery.reason.startswith("last working"))

    def reprobe(self) -> bool:
        """Full probe without the shortcut. Returns True if the address changed."""
        before = self.client.base_url
        d = self._discover(None)
        if d.chosen:
            self._adopt(d)
        return self.client.base_url != before

    def _adopt(self, d: Discovery) -> None:
        with self._lock:
            self.discovery = d
            if d.chosen and d.chosen.info:
                self.client.base_url = d.chosen.candidate.url
                self.client.server_info = d.chosen.info.raw
                self.client.compat.set_version(d.chosen.info.version)
                if not d.needs_pin:
                    self.settings.set("last_address", d.chosen.candidate.url)
        self.client.on_connection_error = self._on_connection_error

    def _on_connection_error(self, err: ConnectionFailed) -> None:
        log.info("connection error (%s); re-probing", err)
        try:
            self.reprobe()
        except Exception as e:
            log.debug("reprobe failed: %s", e)

    def pin_server(self) -> None:
        """Confirm the discovered server as ours (first run)."""
        d = self.discovery
        if not (d and d.chosen and d.chosen.info):
            raise RuntimeError("nothing to pin")
        self.settings.set("server_id", d.chosen.info.id)
        self.settings.set("last_address", d.chosen.candidate.url)
        d.needs_pin = False

    @property
    def server_id(self) -> str | None:
        return self.settings.get("server_id")

    def playback_allowed(self) -> tuple[bool, str]:
        d = self.discovery
        if not (d and d.chosen):
            return False, "Not connected to the server."
        if d.is_public and self.settings.get("network.never_stream_public", True):
            return False, "Connect Tailscale or be on the home network to play video."
        return True, ""

    # ------------------------------------------------------------------ login
    def login_client(self) -> JellyfinClient:
        """A throwaway client for a login attempt.

        Password and Quick Connect attempts authenticate this one; only a
        successful result is applied to the real client (complete_login), so a
        cancelled or failed attempt never leaves the session half signed in.
        """
        c = JellyfinClient(self.client.base_url, device_id=self.client.device_id,
                           device_name=self.client.device_name)
        c.server_info = self.client.server_info
        return c

    def has_stored_token(self, user_id: str) -> bool:
        sid = self.server_id
        return bool(sid and self.tokens.get(sid, user_id))

    def try_stored_token(self, user_id: str) -> User | None:
        """Sign in as user_id with their stored token, if it's still valid."""
        sid = self.server_id
        tok = self.tokens.get(sid, user_id) if sid else None
        if not tok:
            return None
        probe = self.login_client()
        probe.token, probe.user_id = tok, user_id
        try:
            user = auth.current_user(probe)
        except Unauthorized:
            self.tokens.clear(sid, user_id)
            return None
        except JellyfinError as e:
            log.warning("could not validate stored token: %s", e)
            return None
        self.client.token, self.client.user_id = tok, user.id
        self.user = user
        self.settings.set("last_user", {"id": user.id, "name": user.name})
        return user

    def restore_login(self) -> User | None:
        """Startup: use the stored token for the last user, if it's still valid."""
        last = self.settings.get("last_user")
        if not last or not last.get("id"):
            return None
        return self.try_stored_token(last["id"])

    def complete_login(self, result: auth.AuthResult) -> User:
        sid = self.server_id or result.server_id
        auth.apply_auth(self.client, result)
        self.user = result.user
        if sid:
            self.tokens.set(sid, result.user.id, result.token)
        self.settings.set("last_user", {"id": result.user.id, "name": result.user.name})
        return result.user

    def switch_user(self) -> None:
        """Back to the profile picker; the token is kept so switching back is instant."""
        self.client.token = self.client.user_id = None
        self.user = None

    def logout(self, forget_user: bool = False) -> None:
        sid, uid = self.server_id, self.client.user_id
        auth.logout(self.client)
        if sid and uid:
            self.tokens.clear(sid, uid)
        if forget_user:
            self.settings.set("last_user", None)
        self.user = None

    def handle_unauthorized(self) -> None:
        """Wire to client.on_unauthorized: drop the dead token, keep the last user name."""
        sid, uid = self.server_id, self.client.user_id
        if sid and uid:
            self.tokens.clear(sid, uid)
        self.client.token = self.client.user_id = None
        self.user = None
