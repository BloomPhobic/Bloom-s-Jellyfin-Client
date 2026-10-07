"""Async image loading: memory LRU (QPixmap) over the disk cache over the network.

Usage: pm = loader.get(key); if None, repaint when `loaded` fires with that key.
Decoding happens in the worker (QImage is thread-safe); only QPixmap
conversion happens on the UI thread.
"""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage, QPixmap

from ...imagecache import ImageCache
from ...session import Session
from ..workers import run_async


class ImageLoader(QObject):
    loaded = Signal(str)          # key that just became available

    def __init__(self, session: Session, cache: ImageCache | None = None,
                 capacity: int = 400, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session
        self.cache = cache or ImageCache()
        self.capacity = capacity
        self._mem: OrderedDict[str, QPixmap] = OrderedDict()
        self._inflight: set[str] = set()
        self._failed: set[str] = set()

    def get(self, key: str | None) -> QPixmap | None:
        if not key:
            return None
        pm = self._mem.get(key)
        if pm is not None:
            self._mem.move_to_end(key)
            return pm
        if key not in self._inflight and key not in self._failed:
            self._inflight.add(key)
            run_async(lambda: self._load(key),
                      lambda img: self._done(key, img),
                      lambda _e: self._done(key, None))
        return None

    def _load(self, key: str) -> QImage | None:          # worker thread
        data = self.cache.fetch(self.session.client, key)
        if not data:
            return None
        img = QImage()
        if not img.loadFromData(data) or img.isNull():
            return None
        return img

    def _done(self, key: str, img: QImage | None) -> None:   # UI thread
        self._inflight.discard(key)
        if img is None:
            self._failed.add(key)
            return
        self._mem[key] = QPixmap.fromImage(img)
        while len(self._mem) > self.capacity:
            self._mem.popitem(last=False)
        self.loaded.emit(key)

    def forget_failures(self) -> None:
        """Call after reconnecting so images that failed offline are retried."""
        self._failed.clear()

    def clear(self) -> None:
        self._mem.clear()
        self._failed.clear()
