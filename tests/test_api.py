import time
import unittest

from helpers import UNREACHABLE, IsolatedTest

from bloomsarchive.api import auth
from bloomsarchive.api.client import JellyfinClient, NotFound, Unauthorized
from bloomsarchive.api.discovery import Candidate, discover
from bloomsarchive.api.models import Item
from bloomsarchive.player import segments


def C(url, prio, public=False):
    return Candidate(url=url, label=f"p{prio}", public=public, priority=prio)


class Discovery(IsolatedTest):
    def test_lan_wins_without_waiting_for_slow_public(self):
        good = self.serve(server_id="ours")
        slow = self.serve(server_id="ours", delay=2.0)
        t0 = time.monotonic()
        d = discover([C(good.url, 0), C(slow.url, 2, public=True)], "ours", "dev",
                     private_timeout=1.0, public_timeout=3.0)
        self.assertEqual(d.base_url, good.url)
        self.assertLess(time.monotonic() - t0, 1.0, "should not wait for the public probe")

    def test_wrong_server_on_lan_is_rejected(self):
        impostor = self.serve(server_id="someone-else", name="ImpostorBox")
        ts = self.serve(server_id="ours")
        d = discover([C(impostor.url, 0), C(ts.url, 1)], "ours", "dev")
        self.assertEqual(d.base_url, ts.url)
        self.assertIn("wrong server", d.reason)
        self.assertFalse(d.results[0].usable)

    def test_unreachable_lan_falls_through_and_marks_public(self):
        pub = self.serve(server_id="ours")
        d = discover([C(UNREACHABLE, 0), C(UNREACHABLE.replace(":9", ":7"), 1), C(pub.url, 2, public=True)],
                     "ours", "dev", private_timeout=0.5)
        self.assertEqual(d.base_url, pub.url)
        self.assertTrue(d.is_public)

    def test_timeout_respected(self):
        slow = self.serve(server_id="ours", delay=1.5)
        t0 = time.monotonic()
        d = discover([C(slow.url, 0)], "ours", "dev", private_timeout=0.3)
        self.assertIsNone(d.chosen)
        self.assertLess(time.monotonic() - t0, 1.2)

    def test_first_run_needs_pin(self):
        srv = self.serve(server_id="fresh")
        d = discover([C(srv.url, 0)], None, "dev")
        self.assertEqual(d.chosen.info.id, "fresh")
        self.assertTrue(d.needs_pin)

    def test_last_good_fast_path(self):
        lan = self.serve(server_id="ours")
        ts = self.serve(server_id="ours")
        d = discover([C(lan.url, 0), C(ts.url, 1)], "ours", "dev", last_good=ts.url)
        self.assertEqual(d.base_url, ts.url)
        self.assertEqual(len(d.results), 1)
        self.assertEqual([r.path for r in lan.log], [], "LAN must not be probed on the fast path")

    def test_non_jellyfin_device(self):
        # Something answering HTTP but not Jellyfin (no Id) must not be chosen.
        other = self.serve(server_id="")
        d = discover([C(other.url, 0)], None, "dev")
        self.assertIsNone(d.chosen)


class Compat(IsolatedTest):
    def login(self, srv, user="Alice", pw="hunter2"):
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, user, pw)
        return c

    def test_new_routes_on_new_server(self):
        srv = self.serve(version="10.10.7", route_style="new")
        c = self.login(srv)
        views = c.compat.call("views")
        self.assertEqual([v["Name"] for v in views["Items"]], ["Movies", "Anime", "Collections"])
        self.assertEqual(c.compat.choices()["views"], "new")

    def test_fallback_to_legacy_and_cache(self):
        srv = self.serve(version="10.10.7", route_style="legacy")   # version lies; only legacy works
        c = self.login(srv)
        c.compat.call("views")
        self.assertEqual(c.compat.choices()["views"], "legacy")
        n = len(srv.log)
        c.compat.call("views")
        self.assertEqual(len(srv.log), n + 1, "cached choice: no second 404")
        self.assertTrue(srv.log[-1].path.startswith("/Users/user-alice/Views"))

    def test_old_server_tries_legacy_first(self):
        srv = self.serve(version="10.8.13")
        c = self.login(srv)
        c.compat.call("resume", params={"mediaTypes": "Video"})
        self.assertEqual(c.compat.choices()["resume"], "legacy")
        self.assertFalse(any(r.status == 404 for r in srv.log))

    def test_played_and_favourite_toggles(self):
        srv = self.serve(route_style="legacy")
        c = self.login(srv)
        ud = c.compat.call("played", "POST", item_id="ep-1")
        self.assertTrue(ud["Played"])
        c.compat.call("favorite", "POST", item_id="movie-1")
        self.assertTrue(srv.state.items["movie-1"]["UserData"]["IsFavorite"])
        c.compat.call("favorite", "DELETE", item_id="movie-1")
        self.assertFalse(srv.state.items["movie-1"]["UserData"]["IsFavorite"])

    def test_both_routes_404_raises(self):
        srv = self.serve()
        c = self.login(srv)
        with self.assertRaises(NotFound):
            c.compat.call("item", item_id="does-not-exist")


class Auth(IsolatedTest):
    def test_public_users_and_password(self):
        srv = self.serve()
        c = JellyfinClient(srv.url, device_id="dev")
        users = auth.public_users(c)
        self.assertEqual([u.name for u in users], ["Alice", "Guest"])
        self.assertIn("tag=avatar1", auth.user_image_url(c, users[0]))
        self.assertIsNone(auth.user_image_url(c, users[1]))   # letter-avatar fallback
        with self.assertRaises(auth.AuthError):
            auth.login_password(c, "Alice", "wrong")
        res = auth.login_password(c, "Guest", "")              # blank password allowed
        self.assertEqual(res.user.name, "Guest")
        self.assertEqual(auth.current_user(c).id, "user-guest")
        hdr = srv.log[-1].headers["Authorization"]
        self.assertIn(f'Token="{res.token}"', hdr)
        self.assertIn('DeviceId="dev"', hdr)

    def test_hidden_user_manual_login(self):
        srv = self.serve()
        c = JellyfinClient(srv.url, device_id="dev")
        self.assertEqual(auth.login_password(c, "hidden", "secret").user.name, "Hidden")

    def test_quick_connect_approved_from_other_session(self):
        srv = self.serve()
        approver = JellyfinClient(srv.url, device_id="phone")
        auth.login_password(approver, "Alice", "hunter2")
        c = JellyfinClient(srv.url, device_id="dev")
        self.assertTrue(auth.quick_connect_enabled(c))
        qc = auth.QuickConnectSession(c)
        code = qc.initiate()
        self.assertEqual(len(code), 6)
        self.assertIsNone(qc.poll())
        self.assertTrue(auth.authorize_code(approver, code))
        res = qc.run(interval=0.05)
        self.assertEqual(res.user.name, "Alice")
        self.assertEqual(c.token, res.token)

    def test_quick_connect_cancel_and_disabled(self):
        srv = self.serve()
        qc = auth.QuickConnectSession(JellyfinClient(srv.url, device_id="dev"))
        qc.initiate()
        self.assertIsNone(qc.run(cancelled=lambda: True))
        srv.cfg.quick_connect = False
        self.assertFalse(auth.quick_connect_enabled(JellyfinClient(srv.url, device_id="dev")))

    def test_quick_connect_timeout(self):
        srv = self.serve()
        qc = auth.QuickConnectSession(JellyfinClient(srv.url, device_id="dev"), timeout=0.1)
        qc.initiate()
        time.sleep(0.15)
        with self.assertRaises(auth.AuthError):
            qc.poll()

    def test_401_hook(self):
        srv = self.serve()
        c = JellyfinClient(srv.url, device_id="dev", token="dead", user_id="user-alice")
        hits = []
        c.on_unauthorized = lambda: hits.append(1)
        with self.assertRaises(Unauthorized):
            c.get("/Users/Me")
        self.assertEqual(hits, [1])


class Segments(IsolatedTest):
    def episode(self, c):
        return Item.from_json(c.compat.call("item", item_id="ep-1"))

    def test_media_segments(self):
        srv = self.serve(segments="media-segments")
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, "Alice", "hunter2")
        segs = segments.fetch_segments(c, self.episode(c))
        self.assertEqual([(s.type, s.start, s.end, s.source) for s in segs],
                         [("Intro", 90, 180, "media-segments"), ("Outro", 1320, 1410, "media-segments")])
        req = [r for r in srv.log if r.path.startswith("/MediaSegments")][0]
        self.assertEqual(req.query["includeSegmentTypes"], ["Intro", "Outro"])

    def test_intro_skipper_on_older_server(self):
        srv = self.serve(version="10.9.11", segments="intro-skipper")
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, "Alice", "hunter2")
        segs = segments.fetch_segments(c, self.episode(c))
        self.assertEqual({s.type for s in segs}, {"Intro", "Outro"})
        self.assertTrue(all(s.source == "intro-skipper" for s in segs))

    def test_chapter_fallback(self):
        srv = self.serve(segments="none")
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, "Alice", "hunter2")
        segs = segments.fetch_segments(c, self.episode(c))
        self.assertEqual([(s.type, s.start, s.end) for s in segs], [("Intro", 90, 180), ("Outro", 1320, 1440)])


if __name__ == "__main__":
    unittest.main()
