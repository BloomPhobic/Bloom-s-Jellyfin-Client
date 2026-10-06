"""HTTP wrapper for the Jellyfin API (standard library only).

Blocking by design: the UI calls it from QThreadPool workers, and --selfcheck
and the tests call it directly.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from .. import APP_NAME, __version__

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0


# --------------------------------------------------------------------------- errors

class JellyfinError(Exception):
    """Base class for API errors."""


class ConnectionFailed(JellyfinError):
    """The server could not be reached (DNS, refused, timeout, TLS)."""


class HttpError(JellyfinError):
    def __init__(self, status: int, method: str, path: str, body: str = ""):
        self.status = status
        self.method = method
        self.path = path
        self.body = body[:500]
        super().__init__(f"HTTP {status} for {method} {path}" + (f": {self.body}" if self.body else ""))


class Unauthorized(HttpError):
    """401: token missing, expired or revoked."""


class NotFound(HttpError):
    """404: also the signal for compat-route fallback."""


def _http_error(status: int, method: str, path: str, body: str) -> HttpError:
    if status == 401:
        return Unauthorized(status, method, path, body)
    if status == 404:
        return NotFound(status, method, path, body)
    return HttpError(status, method, path, body)


# --------------------------------------------------------------------------- auth header

_UNSAFE = re.compile(r'[",\r\n]')


def _clean(value: str) -> str:
    # Header values must be latin-1 and must not break the quoted list.
    value = _UNSAFE.sub("", value)
    return value.encode("ascii", "replace").decode("ascii")


def auth_header(device: str, device_id: str, token: str | None = None,
                client: str = APP_NAME, version: str = __version__) -> str:
    parts = [
        f'Client="{_clean(client)}"',
        f'Device="{_clean(device)}"',
        f'DeviceId="{_clean(device_id)}"',
        f'Version="{_clean(version)}"',
    ]
    if token:
        parts.append(f'Token="{_clean(token)}"')
    return "MediaBrowser " + ", ".join(parts)


def default_device_name() -> str:
    return socket.gethostname() or "desktop"


# --------------------------------------------------------------------------- helpers

def encode_params(params: Mapping[str, Any] | None) -> str:
    """None is dropped, bools become true/false, lists become repeated keys.

    Pass a comma-joined string when an endpoint wants `a,b,c` instead of `x=a&x=b`.
    """
    if not params:
        return ""
    pairs: list[tuple[str, str]] = []
    for k, v in params.items():
        if v is None:
            continue
        values = v if isinstance(v, (list, tuple)) else [v]
        for item in values:
            if isinstance(item, bool):
                item = "true" if item else "false"
            pairs.append((k, str(item)))
    return urllib.parse.urlencode(pairs)


def is_private_host(url: str) -> bool:
    """LAN, Tailscale (CGNAT 100.64/10), loopback or link-local: never use a proxy."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in ("localhost",) or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(".ts.net")
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10")


def normalize_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


def build_opener(base_url: str) -> urllib.request.OpenerDirector:
    handlers: list[Any] = [urllib.request.HTTPSHandler(context=ssl.create_default_context())]
    if is_private_host(base_url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


# --------------------------------------------------------------------------- client

class JellyfinClient:
    def __init__(self, base_url: str, device_id: str, device_name: str | None = None,
                 token: str | None = None, user_id: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.device_id = device_id
        self.device_name = device_name or default_device_name()
        self.token = token
        self.user_id = user_id
        self.timeout = timeout
        self.server_info: dict | None = None
        # Hooks the app wires up: on 401 go back to login; on connection loss re-probe.
        self.on_unauthorized: Callable[[], None] | None = None
        self.on_connection_error: Callable[[ConnectionFailed], None] | None = None
        self._base_url = ""
        self._opener: urllib.request.OpenerDirector | None = None
        self.base_url = base_url
        from .compat import Compat  # local import: compat imports this module
        self.compat = Compat(self)

    # -- base url
    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, value: str) -> None:
        self._base_url = normalize_base_url(value)
        self._opener = build_opener(self._base_url)

    # -- urls and headers
    def url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        if not path.startswith("/"):
            path = "/" + path
        q = encode_params(params)
        return f"{self._base_url}{path}" + (f"?{q}" if q else "")

    def headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        h = auth_header(self.device_name, self.device_id, self.token)
        out = {
            "Authorization": h,
            "X-Emby-Authorization": h,   # pre-10.8 servers read this one
            "Accept": "application/json",
            "User-Agent": f"BloomsArchive/{__version__}",
        }
        if extra:
            out.update(extra)
        return out

    def auth_header_value(self) -> str:
        """For handing to mpv over IPC (never on the command line)."""
        return auth_header(self.device_name, self.device_id, self.token)

    # -- requests
    def open(self, method: str, path: str, params: Mapping[str, Any] | None = None,
             json_body: Any = None, headers: Mapping[str, str] | None = None,
             timeout: float | None = None):
        """Low-level: returns the open response. Caller must close it."""
        data = None
        hdrs = self.headers(headers)
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif method in ("POST", "PUT"):
            data = b""
        req = urllib.request.Request(self.url(path, params), data=data, headers=hdrs, method=method)
        assert self._opener is not None
        try:
            return self._opener.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            err = _http_error(e.code, method, path, body)
            if isinstance(err, Unauthorized) and self.token and self.on_unauthorized:
                self.on_unauthorized()
            raise err from None
        except (urllib.error.URLError, OSError, ssl.SSLError) as e:
            reason = getattr(e, "reason", e)
            err = ConnectionFailed(f"{method} {self._base_url}{path}: {reason}")
            raise err from None

    def request(self, method: str, path: str, params: Mapping[str, Any] | None = None,
                json_body: Any = None, timeout: float | None = None,
                retries: int | None = None) -> Any:
        """JSON in, JSON out. Returns None for empty bodies. GETs retry once on connection errors."""
        if retries is None:
            retries = 1 if method == "GET" else 0
        attempt = 0
        while True:
            try:
                with self.open(method, path, params, json_body, timeout=timeout) as resp:
                    raw = resp.read()
                break
            except ConnectionFailed as e:
                if attempt >= retries:
                    if self.on_connection_error:
                        self.on_connection_error(e)
                    raise
                attempt += 1
                time.sleep(0.3 * attempt)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode("utf-8", "replace")

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, json_body: Any = None, **params: Any) -> Any:
        return self.request("POST", path, params, json_body)

    def delete(self, path: str, **params: Any) -> Any:
        return self.request("DELETE", path, params)

    # -- common
    def public_info(self, timeout: float | None = None) -> dict:
        info = self.request("GET", "/System/Info/Public", timeout=timeout, retries=0)
        if not isinstance(info, dict) or not info.get("Id"):
            raise JellyfinError("Not a Jellyfin server (no Id in /System/Info/Public)")
        self.server_info = info
        return info
