import io
import unittest

from helpers import UNREACHABLE, IsolatedTest

from bloomsarchive import selfcheck
from bloomsarchive.api import auth
from bloomsarchive.session import Session


def no_transcode_requests(srv):
    bad = [r for r in srv.log
           if "m3u8" in r.path or (r.path.startswith("/Videos/") and "/stream" in r.path
                                   and r.query.get("static") != ["true"])
           or r.path.endswith("/PlaybackInfo")]
    return bad


class SessionFlow(IsolatedTest):
    def test_first_run_pin_login_and_restore(self):
        srv = self.serve(server_id="ours")
        self.set_addresses((UNREACHABLE, False), (srv.url, False))
        s = Session(self.settings, self.tokens)
        d = s.connect()
        self.assertTrue(d.needs_pin)
        s.pin_server()
        self.assertEqual(self.settings.get("server_id"), "ours")
        self.assertEqual(self.settings.get("last_address"), srv.url)

        res = auth.login_password(s.client, "Alice", "hunter2")
        s.complete_login(res)

        # New launch: fast path via last address, token restored.
        s2 = Session(self.settings, self.tokens)
        d2 = s2.connect()
        self.assertTrue(s2.used_fast_path)
        self.assertEqual(d2.base_url, srv.url)
        self.assertEqual(s2.restore_login().name, "Alice")

    def test_revoked_token_is_cleared(self):
        srv = self.serve(server_id="ours")
        self.set_addresses((srv.url, False))
        self.settings.set("server_id", "ours")
        s = Session(self.settings, self.tokens)
        s.connect()
        s.complete_login(auth.login_password(s.client, "Alice", "hunter2"))
        srv.state.tokens.clear()                     # server forgets every session
        s2 = Session(self.settings, self.tokens)
        s2.connect()
        self.assertIsNone(s2.restore_login())
        self.assertIsNone(self.tokens.get("ours", "user-alice"))
        self.assertEqual(self.settings.get("last_user")["name"], "Alice")   # still remembered

    def test_reprobe_switches_to_better_address(self):
        lan = self.serve(server_id="ours")
        pub = self.serve(server_id="ours")
        self.set_addresses((lan.url, False), (pub.url, True))
        self.settings.set("server_id", "ours")
        self.settings.set("last_address", pub.url)    # e.g. last used away from home
        s = Session(self.settings, self.tokens)
        s.connect()
        self.assertEqual(s.client.base_url, pub.url)
        self.assertFalse(s.playback_allowed()[0])     # never stream via public by default
        self.assertTrue(s.reprobe())
        self.assertEqual(s.client.base_url, lan.url)
        self.assertTrue(s.playback_allowed()[0])

    def test_impostor_device_never_used(self):
        impostor = self.serve(server_id="not-ours")
        ts = self.serve(server_id="ours")
        self.set_addresses((impostor.url, False), (ts.url, False))
        self.settings.set("server_id", "ours")
        self.settings.set("last_address", impostor.url)   # even if it was "last good" at home
        s = Session(self.settings, self.tokens)
        s.connect()
        self.assertEqual(s.client.base_url, ts.url)


class SelfCheck(IsolatedTest):
    def run_check(self, **kw):
        out = io.StringIO()
        code = selfcheck.run(self.settings, out=out, interactive=False, **kw)
        return code, out.getvalue()

    def test_full_report(self):
        srv = self.serve(server_id="ours", version="10.10.7")
        self.set_addresses((UNREACHABLE, False), (srv.url, False), ("https://example.invalid", True))
        code, text = self.run_check(user="Alice", password="hunter2", pin=True)
        print(text)
        self.assertEqual(code, 0, text)
        for needle in ("pinned ServerId ours", "Quick Connect enabled", "2 public user(s)",
                       "libraries via /UserViews: Movies, Anime", "media-segments: works",
                       "byte-range direct play works: 206, 65536 bytes", "All checks passed"):
            self.assertIn(needle, text)
        self.assertEqual(self.settings.get("server_id"), "ours")
        self.assertEqual(no_transcode_requests(srv), [])

    def test_legacy_server_and_unpinned(self):
        srv = self.serve(version="10.8.13", segments="none")
        self.set_addresses((srv.url, False))
        code, text = self.run_check(user="Guest", password="")
        self.assertEqual(code, 1)
        self.assertIn("ServerId not pinned", text)
        self.assertIn("libraries via /Users/user-guest/Views", text)
        self.assertIn("chapters: works", text)
        self.assertIn("byte-range direct play works", text)

    def test_wrong_pinned_server(self):
        srv = self.serve(server_id="impostor")
        self.set_addresses((srv.url, False))
        self.settings.set("server_id", "ours")
        code, text = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("REJECTED", text)

    def test_range_ignored_is_flagged(self):
        srv = self.serve(server_id="ours", honor_range=False)
        self.set_addresses((srv.url, False))
        code, text = self.run_check(user="Alice", password="hunter2", pin=True)
        self.assertIn("server ignored Range", text)


if __name__ == "__main__":
    unittest.main()
