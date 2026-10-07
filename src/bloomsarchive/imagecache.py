"""Disk cache for server images (avatars now, posters later). No Qt here.

Keys are server-relative paths with their query (which carries the image tag),
so a changed image gets a new key and the cache never serves stale art, and
the same file is reused whichever address (LAN/Tailscale/public) fetched it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from .api.client import JellyfinClient, JellyfinError
from .config import image_cache_dir

log = logging.getLogger(__name__)

MAX_BYTES = 8 * 1024 * 1024   # refuse absurd responses


class ImageCache:
    def __init__(self, root: Path | None = None):
        self.root = root or image_cache_dir()

    def path_for(self, key: str) -> Path:
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self.root / h[:2] / f"{h}.img"

    def read(self, key: str) -> bytes | None:
        try:
            return self.path_for(key).read_bytes()
        except OSError:
            return None

    def write(self, key: str, data: bytes) -> None:
        p = self.path_for(key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".dl-")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
        except OSError as e:
            log.debug("image cache write failed: %s", e)

    def fetch(self, client: JellyfinClient, key: str) -> bytes | None:
        """Disk first, then network (written back). None if unavailable."""
        data = self.read(key)
        if data:
            return data
        try:
            with client.open("GET", key, headers={"Accept": "image/*"}, timeout=10) as resp:
                data = resp.read(MAX_BYTES + 1)
        except JellyfinError as e:
            log.debug("image fetch failed for %s: %s", key, e)
            return None
        if not data or len(data) > MAX_BYTES:
            return None
        self.write(key, data)
        return data

    def clear(self) -> int:
        """Delete everything; returns the number of files removed."""
        n = 0
        if not self.root.exists():
            return 0
        for p in self.root.rglob("*.img"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n

    def size_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(p.stat().st_size for p in self.root.rglob("*.img") if p.is_file())
