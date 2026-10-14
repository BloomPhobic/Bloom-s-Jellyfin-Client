import json
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import IsolatedTest

from bloomsarchive.api import auth, details
from bloomsarchive.api.client import JellyfinClient
from bloomsarchive.api.models import Item
from bloomsarchive.player import overlay_info

ROOT = Path(__file__).resolve().parents[1]


class OverlayInfo(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.srv = self.serve(second_season=True)
        self.c = JellyfinClient(self.srv.url, device_id="dev")
        auth.login_password(self.c, "Alice", "hunter2")
        self.seasons = details.seasons(self.c, "series-1")
        self.eps = details.episodes(self.c, "series-1")

    def test_episode_info(self):
        ep = details.get_item(self.c, "ep-3")
        info = overlay_info.build_info(ep, ep.media_sources[0], self.seasons, self.eps, autoplay=True)
        self.assertEqual(info["title"], "Episode 3")
        self.assertEqual(info["subtitle"], "Test Anime · S01E03")
        self.assertTrue(info["has_prev"])
        self.assertTrue(info["has_next"], "S01E03 → S02E01 crosses the season")
        self.assertEqual([s["name"] for s in info["seasons"]], ["Season 1", "Season 2"])
        self.assertEqual([e["number"] for e in info["seasons"][1]["episodes"]], [1, 2])
        self.assertEqual(info["versions"], [], "single version: no picker")
        json.dumps(info)   # must serialise

    def test_neighbours_across_seasons(self):
        prev, nxt = overlay_info.neighbours(self.eps, "ep-3")
        self.assertEqual((prev.id, nxt.id), ("ep-2", "s2-ep-1"))
        prev, nxt = overlay_info.neighbours(self.eps, "s2-ep-2")
        self.assertEqual((prev.id, nxt), ("s2-ep-1", None))

    def test_specials_skipped_for_next(self):
        eps = [Item.from_json({"Id": "sp", "Type": "Episode", "ParentIndexNumber": 0, "IndexNumber": 1}),
               Item.from_json({"Id": "a", "Type": "Episode", "ParentIndexNumber": 1, "IndexNumber": 1}),
               Item.from_json({"Id": "b", "Type": "Episode", "ParentIndexNumber": 1, "IndexNumber": 2})]
        self.assertEqual(overlay_info.neighbours(eps, "a"), (None, eps[2]))

    def test_movie_and_versions(self):
        m = details.get_item(self.c, "movie-1")
        src2 = dict(m.media_sources[0].raw, Id="alt", Name="4K",
                    MediaStreams=[{"Index": 0, "Type": "Video", "Height": 2160}])
        m.media_sources.append(type(m.media_sources[0]).from_json(src2))
        info = overlay_info.build_info(m, m.media_sources[1], [], [], autoplay=False)
        self.assertFalse(info["is_episode"])
        self.assertEqual(info["subtitle"], "2021 · R")
        self.assertEqual([v["short"] for v in info["versions"]], ["MKV", "4K"])
        self.assertEqual([v["current"] for v in info["versions"]], [False, True])

    def test_script_bundled(self):
        self.assertTrue(Path(overlay_info.script_path()).name == "bloom.lua")


@unittest.skipUnless(shutil.which("texlua") or shutil.which("lua") or shutil.which("luajit"),
                     "no Lua interpreter")
class OverlayScript(unittest.TestCase):
    def test_lua_overlay(self):
        lua = shutil.which("luajit") or shutil.which("lua") or shutil.which("texlua")
        r = subprocess.run([lua, str(ROOT / "tests" / "lua" / "test_overlay.lua")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 failed", r.stdout)


if __name__ == "__main__":
    unittest.main()
