"""Shared test setup: isolated settings dir, keyring disabled, src on sys.path."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from bloomsarchive.config import Settings, TokenStore  # noqa: E402

UNREACHABLE = "http://127.0.0.1:9"   # discard port: connection refused immediately


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.environ["BLOOMSARCHIVE_CONFIG_DIR"] = str(self.tmp / "config")
        os.environ["BLOOMSARCHIVE_CACHE_DIR"] = str(self.tmp / "cache")
        self.settings = Settings()
        self.tokens = TokenStore(self.settings, use_keyring=False)
        self._servers = []

    def tearDown(self):
        for s in self._servers:
            s.stop()
        self._tmp.cleanup()

    def serve(self, **cfg):
        from mock_jellyfin import MockConfig, MockJellyfin
        srv = MockJellyfin(MockConfig(**cfg)).start()
        self._servers.append(srv)
        return srv

    def set_addresses(self, *urls_public: tuple[str, bool]):
        self.settings.set("addresses", [
            {"label": f"addr{i}", "url": u, "public": p} for i, (u, p) in enumerate(urls_public)
        ])
