import os
import unittest

from helpers import IsolatedTest

from bloomsarchive import desktop
from bloomsarchive.api import auth
from bloomsarchive.api.models import Item, User
from bloomsarchive.imagecache import ImageCache
from bloomsarchive.session import Session


class LoginFlows(IsolatedTest):
    def session(self, srv):
        self.set_addresses((srv.url, False))
        self.settings.set("server_id", srv.cfg.server_id)
        s = Session(self.settings, self.tokens)
        s.connect()
        return s

    def test_failed_attempt_leaves_session_clean(self):
        srv = self.serve()
        s = self.session(srv)
        lc = s.login_client()
        with self.assertRaises(auth.AuthError):
            auth.login_password(lc, "Alice", "nope")
        self.assertIsNone(s.client.token)
        res = auth.login_password(s.login_client(), "Alice", "hunter2")
        self.assertIsNone(s.client.token, "only complete_login touches the real client")
        s.complete_login(res)
        self.assertEqual(s.client.token, res.token)
        self.assertEqual(self.settings.get("last_user"), {"id": "user-alice", "name": "Alice"})

    def test_switch_user_keeps_tokens_for_instant_switch_back(self):
        srv = self.serve()
        s = self.session(srv)
        s.complete_login(auth.login_password(s.login_client(), "Alice", "hunter2"))
        s.switch_user()
        self.assertIsNone(s.client.token)
        s.complete_login(auth.login_password(s.login_client(), "Guest", ""))
        s.switch_user()
        self.assertTrue(s.has_stored_token("user-alice"))
        n = len(srv.log)
        self.assertEqual(s.try_stored_token("user-alice").name, "Alice")
        self.assertEqual([r.path for r in srv.log[n:]], ["/Users/Me"], "no password round-trip")
        self.assertEqual(self.settings.get("last_user")["name"], "Alice")

    def test_revoked_stored_token_falls_back(self):
        srv = self.serve()
        s = self.session(srv)
        s.complete_login(auth.login_password(s.login_client(), "Alice", "hunter2"))
        s.switch_user()
        srv.state.tokens.clear()
        self.assertIsNone(s.try_stored_token("user-alice"))
        self.assertFalse(s.has_stored_token("user-alice"))
        self.assertIsNone(s.client.token)

    def test_logout_revokes_and_forgets_token(self):
        srv = self.serve()
        s = self.session(srv)
        res = auth.login_password(s.login_client(), "Alice", "hunter2")
        s.complete_login(res)
        s.logout()
        self.assertNotIn(res.token, srv.state.tokens)
        self.assertFalse(s.has_stored_token("user-alice"))
        self.assertEqual(self.settings.get("last_user")["name"], "Alice")   # still preselected

    def test_quick_connect_on_login_client(self):
        srv = self.serve(qc_auto_approve_after=2)
        s = self.session(srv)
        qc = auth.QuickConnectSession(s.login_client())
        qc.initiate()
        res = qc.run(interval=0.02)
        self.assertIsNone(s.client.token)
        s.complete_login(res)
        self.assertEqual(s.user.name, "Alice")

    def test_unauthorized_handler(self):
        srv = self.serve()
        s = self.session(srv)
        s.complete_login(auth.login_password(s.login_client(), "Alice", "hunter2"))
        s.handle_unauthorized()
        self.assertIsNone(s.client.token)
        self.assertFalse(s.has_stored_token("user-alice"))


class Images(IsolatedTest):
    def test_avatar_path_and_cache(self):
        srv = self.serve()
        s_users = auth.public_users(auth.JellyfinClient(srv.url, device_id="d"))
        path = auth.user_image_path(s_users[0])
        self.assertTrue(path.startswith("/Users/user-alice/Images/Primary?tag=avatar1"))
        self.assertIsNone(auth.user_image_path(User(id="x", name="Nobody")))

        cache = ImageCache(self.tmp / "img")
        client = auth.JellyfinClient(srv.url, device_id="d")
        data = cache.fetch(client, path)
        self.assertTrue(data.startswith(b"\x89PNG"))
        n = len(srv.log)
        self.assertEqual(cache.fetch(client, path), data)
        self.assertEqual(len(srv.log), n, "second fetch served from disk")
        self.assertGreater(cache.size_bytes(), 0)
        self.assertEqual(cache.clear(), 1)

    def test_missing_image(self):
        srv = self.serve()
        cache = ImageCache(self.tmp / "img")
        self.assertIsNone(cache.fetch(auth.JellyfinClient(srv.url, device_id="d"), "/nope?tag=1"))


class Models(unittest.TestCase):
    def test_top_level_streams_fill_single_source(self):
        it = Item.from_json({"Id": "e", "Type": "Episode", "MediaSources": [{"Id": "e"}],
                             "MediaStreams": [{"Index": 0, "Type": "Video"}, {"Index": 1, "Type": "Audio"}]})
        self.assertEqual(len(it.media_sources[0].streams), 2)


class Desktop(IsolatedTest):
    def test_install(self):
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")
        try:
            path = desktop.install("/home/bloom/.venv/bin/python")
            text = path.read_text()
            self.assertEqual(path.name, "bloomsarchive.desktop")
            self.assertIn("Exec=/home/bloom/.venv/bin/python -m bloomsarchive", text)
            self.assertIn("Name=Bloom's Archive", text)
            self.assertTrue(desktop.uninstall())
        finally:
            del os.environ["XDG_DATA_HOME"]


if __name__ == "__main__":
    unittest.main()
