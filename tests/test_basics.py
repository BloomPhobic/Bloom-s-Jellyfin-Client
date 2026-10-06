import json
import os
import stat
import sys
import unittest

from helpers import IsolatedTest

from bloomsarchive.api.client import auth_header, encode_params, is_private_host, normalize_base_url
from bloomsarchive.api.compat import parse_version
from bloomsarchive.api.models import Item, seconds_to_ticks, ticks_to_seconds
from bloomsarchive.config import Settings


class Ticks(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(seconds_to_ticks(1), 10_000_000)
        self.assertEqual(ticks_to_seconds(15_000_000), 1.5)
        self.assertEqual(ticks_to_seconds(None), 0)
        self.assertEqual(seconds_to_ticks(ticks_to_seconds(123_456_789_0)), 123_456_789_0)

    def test_item_helpers(self):
        it = Item.from_json({"Id": "e", "Name": "x", "Type": "Episode", "IndexNumber": 5,
                             "ParentIndexNumber": 1, "RunTimeTicks": 14_400_000_000})
        self.assertEqual(it.episode_label, "S01E05")
        self.assertEqual(it.runtime_seconds, 1440)
        self.assertEqual(Item.from_json({"Id": "m", "Type": "Movie"}).episode_label, "")


class AuthHeader(unittest.TestCase):
    def test_format(self):
        h = auth_header("cachy", "dev123", token="tok")
        self.assertEqual(h, 'MediaBrowser Client="Bloom\'s Archive", Device="cachy", '
                            'DeviceId="dev123", Version="0.1.0", Token="tok"')

    def test_no_token_and_sanitised(self):
        h = auth_header('evil",Token="x', "d")
        self.assertNotIn(', Token="', h)        # injected quote can't open a new field
        self.assertIn('Device="evilToken=x"', h)
        auth_header("Alice’s laptop", "d").encode("latin-1")   # must be header-safe


class Helpers(unittest.TestCase):
    def test_params(self):
        q = encode_params({"a": True, "b": None, "t": ["Intro", "Outro"], "s": "x,y"})
        self.assertEqual(q, "a=true&t=Intro&t=Outro&s=x%2Cy")

    def test_private_hosts(self):
        for u in ("http://192.168.1.50:8096", "http://100.64.0.10:8096", "http://localhost:1",
                  "http://127.0.0.1:9"):
            self.assertTrue(is_private_host(u), u)
        self.assertFalse(is_private_host("https://jellyfin.example.com"))

    def test_normalize(self):
        self.assertEqual(normalize_base_url(" 192.168.1.50:8096/ "), "http://192.168.1.50:8096")

    def test_versions(self):
        self.assertEqual(parse_version("10.10.7"), (10, 10, 7))
        self.assertGreater(parse_version("10.10.0"), parse_version("10.9.11"))
        self.assertEqual(parse_version(None), ())


class Config(IsolatedTest):
    def test_defaults_and_persist(self):
        s = self.settings
        self.assertEqual(s.get("addresses")[0]["url"], "http://192.168.1.50:8096")
        self.assertTrue(s.get("mpv.inherit_user_config"))
        s.set("playback.autoplay", False)
        did = s.device_id()
        s2 = Settings(s.path)
        self.assertFalse(s2.get("playback.autoplay"))
        self.assertEqual(s2.device_id(), did)
        self.assertEqual(s2.get("playback.up_next_seconds"), 20)   # defaults still merged in
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(os.stat(s.path).st_mode), 0o600)

    def test_corrupt_file_falls_back(self):
        self.settings.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.path.write_text("{not json")
        self.assertIsNone(Settings(self.settings.path).get("server_id"))

    def test_token_fallback_store(self):
        self.tokens.set("srv", "u1", "secret")
        self.assertEqual(self.tokens.get("srv", "u1"), "secret")
        self.assertEqual(json.loads(self.settings.path.read_text())["tokens"]["srv:u1"], "secret")
        self.tokens.clear("srv", "u1")
        self.assertIsNone(self.tokens.get("srv", "u1"))


if __name__ == "__main__":
    unittest.main()
