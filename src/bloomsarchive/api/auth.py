"""Login: public users, password, Quick Connect.

Quick Connect flow:
  1. GET  /QuickConnect/Enabled                 -> bool
  2. POST /QuickConnect/Initiate                -> {Secret, Code}; show Code big
  3. GET  /QuickConnect/Connect?secret=...      poll every 3 s until Authenticated
  4. POST /Users/AuthenticateWithQuickConnect   {Secret} -> token + user
The user approves by typing the Code in an already signed-in Jellyfin session.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .client import HttpError, JellyfinClient, NotFound, encode_params
from .models import User

POLL_INTERVAL = 3.0
QUICK_CONNECT_TIMEOUT = 300.0


class AuthError(Exception):
    pass


@dataclass
class AuthResult:
    token: str
    user: User
    server_id: str

    @classmethod
    def from_json(cls, d: dict) -> "AuthResult":
        if not isinstance(d, dict) or not d.get("AccessToken"):
            raise AuthError("Server did not return an access token")
        return cls(token=d["AccessToken"], user=User.from_json(d.get("User") or {}),
                   server_id=d.get("ServerId", ""))


def apply_auth(client: JellyfinClient, result: AuthResult) -> None:
    client.token = result.token
    client.user_id = result.user.id


# --------------------------------------------------------------------------- users

def public_users(client: JellyfinClient) -> list[User]:
    data = client.get("/Users/Public") or []
    return [User.from_json(u) for u in data]


def user_image_path(user: User, max_width: int = 256) -> str | None:
    """Server-relative path (with query) for the avatar; None means draw a letter avatar.

    Relative, so the image cache is shared between the LAN/Tailscale/public addresses.
    """
    if not user.primary_image_tag:
        return None
    return f"/Users/{user.id}/Images/Primary?" + encode_params(
        {"tag": user.primary_image_tag, "maxWidth": max_width, "quality": 90})


def user_image_url(client: JellyfinClient, user: User, max_width: int = 256) -> str | None:
    path = user_image_path(user, max_width)
    return client.url(path) if path else None


def current_user(client: JellyfinClient) -> User:
    """Validates a stored token (raises Unauthorized if it's dead)."""
    return User.from_json(client.get("/Users/Me"))


# --------------------------------------------------------------------------- password

def login_password(client: JellyfinClient, username: str, password: str = "") -> AuthResult:
    try:
        data = client.post("/Users/AuthenticateByName", {"Username": username, "Pw": password or ""})
    except HttpError as e:
        if e.status in (400, 401):
            raise AuthError("Wrong username or password") from None
        raise
    result = AuthResult.from_json(data)
    apply_auth(client, result)
    return result


def logout(client: JellyfinClient) -> None:
    """Revoke this device's session server-side; failures are ignored."""
    if client.token:
        try:
            client.post("/Sessions/Logout")
        except Exception:
            pass
    client.token = None
    client.user_id = None


# --------------------------------------------------------------------------- quick connect

def quick_connect_enabled(client: JellyfinClient) -> bool:
    try:
        v = client.get("/QuickConnect/Enabled")
    except (NotFound, HttpError):
        return False
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


class QuickConnectSession:
    """Non-blocking state holder: the UI calls poll() from a timer/worker.

    Blocking use (selfcheck, tests): run(cancelled=lambda: False).
    """

    def __init__(self, client: JellyfinClient, timeout: float = QUICK_CONNECT_TIMEOUT):
        self.client = client
        self.timeout = timeout
        self.secret: str = ""
        self.code: str = ""
        self.started_at = 0.0
        self.result: AuthResult | None = None

    def initiate(self) -> str:
        try:
            data = self.client.post("/QuickConnect/Initiate")
        except NotFound:
            # Some versions use GET for Initiate.
            data = self.client.get("/QuickConnect/Initiate")
        if not isinstance(data, dict) or not data.get("Secret"):
            raise AuthError("Quick Connect is not available on this server")
        self.secret = data["Secret"]
        self.code = str(data.get("Code", ""))
        self.started_at = time.monotonic()
        return self.code

    @property
    def expired(self) -> bool:
        return bool(self.started_at) and time.monotonic() - self.started_at > self.timeout

    def poll(self) -> AuthResult | None:
        """One check. Returns the AuthResult once approved, else None."""
        if not self.secret:
            raise AuthError("Quick Connect not initiated")
        if self.expired:
            raise AuthError("Quick Connect code expired — try again")
        try:
            state = self.client.get("/QuickConnect/Connect", secret=self.secret)
        except NotFound:
            raise AuthError("Quick Connect request expired on the server") from None
        if not (isinstance(state, dict) and state.get("Authenticated")):
            return None
        data = self.client.post("/Users/AuthenticateWithQuickConnect", {"Secret": self.secret})
        self.result = AuthResult.from_json(data)
        apply_auth(self.client, self.result)
        return self.result

    def run(self, cancelled: Callable[[], bool] = lambda: False,
            interval: float = POLL_INTERVAL) -> AuthResult | None:
        while not cancelled():
            r = self.poll()
            if r:
                return r
            end = time.monotonic() + interval
            while time.monotonic() < end and not cancelled():
                time.sleep(0.05)
        return None


def authorize_code(client: JellyfinClient, code: str) -> bool:
    """Approve another device's code from this signed-in session."""
    v = client.post("/QuickConnect/Authorize", code=code, userId=client.user_id)
    return v is not False
