"""Settings (settings.json under XDG config) and token storage.

Settings are a plain nested dict merged over DEFAULTS, so new keys added in
later versions appear automatically and unknown keys are preserved.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from . import APP_ID

log = logging.getLogger(__name__)

DEFAULT_ADDRESSES = [
    {"label": "Home LAN", "url": "http://192.168.1.50:8096", "public": False},
    {"label": "Tailscale", "url": "http://100.64.0.10:8096", "public": False},
    {"label": "Public", "url": "https://jellyfin.example.com", "public": True},
]

DEFAULTS: dict[str, Any] = {
    "addresses": DEFAULT_ADDRESSES,
    "server_id": None,          # pinned Jellyfin ServerId, set on first confirmed connect
    "last_address": None,       # last base URL that worked
    "device_id": None,          # generated once
    "last_user": None,          # {"id": ..., "name": ...}
    "network": {
        "never_stream_public": True,
        "probe_timeout_private": 1.5,
        "probe_timeout_public": 3.0,
    },
    "mpv": {
        "path": "mpv",
        "extra_args": [],
        "inherit_user_config": True,
        "use_bloom_ui": True,
    },
    "subtitles": {
        "scale": 1.0,
        "bold": False,
        "font": "",
        "border_size": 3.0,
        "pos": 100,
        "color": "",
        "ass_override": "scale",   # no | scale | force
    },
    "languages": {"audio": ["jpn", "eng"], "subtitle": ["eng"]},
    "playback": {
        "autoplay": True,
        "autoplay_limit": 5,
        "up_next_seconds": 20,
        "skip_mode": "button",       # button | auto
        "resume_rewind_seconds": 5,
        "played_threshold_pct": 90,
    },
    "library_state": {},   # per-library sort/filter
    "series_tracks": {},   # per-series audio/subtitle memory
    "tokens": {},          # fallback token store when keyring is unavailable
}


# --------------------------------------------------------------------------- paths

def _env_dir(var: str) -> Path | None:
    v = os.environ.get(var)
    return Path(v) if v else None


def config_dir() -> Path:
    override = _env_dir("BLOOMSARCHIVE_CONFIG_DIR")
    if override:
        return override
    if sys.platform == "win32":
        base = _env_dir("APPDATA") or Path.home() / "AppData" / "Roaming"
    else:
        base = _env_dir("XDG_CONFIG_HOME") or Path.home() / ".config"
    return base / APP_ID


def cache_dir() -> Path:
    override = _env_dir("BLOOMSARCHIVE_CACHE_DIR")
    if override:
        return override
    if sys.platform == "win32":
        base = _env_dir("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    else:
        base = _env_dir("XDG_CACHE_HOME") or Path.home() / ".cache"
    return base / APP_ID


def image_cache_dir() -> Path:
    return cache_dir() / "img"


# --------------------------------------------------------------------------- settings

def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Thread-safe settings with dotted-path access: s.get("playback.autoplay")."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "settings.json")
        self._lock = threading.RLock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    # -- io
    def load(self) -> None:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = _deep_merge(DEFAULTS, raw)
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                log.warning("Could not read %s (%s); using defaults", self.path, e)

    def save(self) -> None:
        """Atomic write with mode 0600 (the file may hold fallback tokens)."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=2, sort_keys=True)
                if sys.platform != "win32":
                    os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    # -- access
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            parts = key.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = copy.deepcopy(value)
            if save:
                self.save()

    @property
    def data(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    # -- identity
    def device_id(self) -> str:
        with self._lock:
            did = self._data.get("device_id")
            if not did:
                did = uuid.uuid4().hex
                self.set("device_id", did)
            return did


# --------------------------------------------------------------------------- tokens

_KEYRING_SERVICE = "bloomsarchive"


def _keyring():
    try:
        import keyring  # type: ignore
        from keyring.backends import fail  # type: ignore

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


class TokenStore:
    """Access tokens keyed by server+user. Secret Service via keyring, else settings.json."""

    def __init__(self, settings: Settings, use_keyring: bool = True):
        self.settings = settings
        self._kr = _keyring() if use_keyring else None

    @property
    def backend(self) -> str:
        return "keyring" if self._kr else "settings.json (0600)"

    @staticmethod
    def _key(server_id: str, user_id: str) -> str:
        return f"{server_id}:{user_id}"

    def get(self, server_id: str, user_id: str) -> str | None:
        key = self._key(server_id, user_id)
        if self._kr:
            try:
                tok = self._kr.get_password(_KEYRING_SERVICE, key)
                if tok:
                    return tok
            except Exception as e:
                log.warning("keyring read failed: %s", e)
        return self.settings.get("tokens", {}).get(key)

    def set(self, server_id: str, user_id: str, token: str) -> None:
        key = self._key(server_id, user_id)
        if self._kr:
            try:
                self._kr.set_password(_KEYRING_SERVICE, key, token)
                self._drop_fallback(key)
                return
            except Exception as e:
                log.warning("keyring write failed (%s); falling back to settings.json", e)
        tokens = self.settings.get("tokens", {})
        tokens[key] = token
        self.settings.set("tokens", tokens)

    def clear(self, server_id: str, user_id: str) -> None:
        key = self._key(server_id, user_id)
        if self._kr:
            try:
                self._kr.delete_password(_KEYRING_SERVICE, key)
            except Exception:
                pass
        self._drop_fallback(key)

    def _drop_fallback(self, key: str) -> None:
        tokens = self.settings.get("tokens", {})
        if key in tokens:
            del tokens[key]
            self.settings.set("tokens", tokens)
