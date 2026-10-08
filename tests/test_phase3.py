import unittest

from helpers import IsolatedTest

from bloomsarchive import wm
from bloomsarchive.api import auth, images, library
from bloomsarchive.api.client import JellyfinClient
from bloomsarchive.api.models import Item


class HomeData(IsolatedTest):
    def client(self, **cfg):
        srv = self.serve(**cfg)
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, "Alice", "hunter2")
        return srv, c

    def test_rows(self):
        srv, c = self.client()
        views = library.views(c)
        self.assertEqual([v.name for v in views], ["Movies", "Anime", "Collections"])
        self.assertEqual([library.wants_latest_row(v) for v in views], [True, True, False])
        self.assertEqual([i.id for i in library.resume(c)], ["ep-1"])
        nu = library.next_up(c)
        self.assertEqual([i.id for i in nu], ["ep-2"], "half-watched ep-1 stays in Continue Watching")
        self.assertEqual([i.id for i in library.latest(c, "view-anime")], ["ep-3", "ep-2", "ep-1"])
        self.assertEqual([i.id for i in library.history(c)], ["movie-1"])
        self.assertEqual([i.id for i in library.favourites(c)], ["series-1"])
        req = [r for r in srv.log if r.path == "/UserItems/Resume"][0]
        self.assertEqual(req.query["enableImageTypes"], ["Primary,Backdrop,Thumb"])

    def test_rows_on_legacy_server(self):
        _srv, c = self.client(version="10.8.13")
        self.assertEqual(len(library.views(c)), 3)
        self.assertEqual([i.id for i in library.resume(c)], ["ep-1"])
        self.assertEqual([i.id for i in library.history(c)], ["movie-1"])

    def test_toggles(self):
        srv, c = self.client()
        ud = library.set_played(c, "ep-2", True)
        self.assertTrue(ud.played)
        self.assertEqual([i.id for i in library.next_up(c)], ["ep-3"])
        library.set_played(c, "ep-2", False)
        ud = library.set_favorite(c, "movie-1", True)
        self.assertTrue(ud.is_favorite)
        self.assertEqual({i.id for i in library.favourites(c)}, {"movie-1", "series-1"})
        library.set_favorite(c, "movie-1", False)
        self.assertFalse(srv.state.items["movie-1"]["UserData"]["IsFavorite"])


class Images(unittest.TestCase):
    def test_episode_poster_uses_series(self):
        ep = Item.from_json({"Id": "e", "Type": "Episode", "SeriesId": "s", "SeriesPrimaryImageTag": "st",
                             "ImageTags": {"Primary": "et"}})
        self.assertTrue(images.poster(ep).startswith("/Items/s/Images/Primary?tag=st"))
        self.assertTrue(images.landscape(ep).startswith("/Items/e/Images/Primary?tag=et"))

    def test_landscape_fallbacks(self):
        mv = Item.from_json({"Id": "m", "Type": "Movie", "ImageTags": {"Primary": "p"},
                             "BackdropImageTags": ["b0"]})
        self.assertTrue(images.landscape(mv).startswith("/Items/m/Images/Backdrop/0?tag=b0"))
        thumb = Item.from_json({"Id": "m", "Type": "Movie", "ImageTags": {"Thumb": "t", "Primary": "p"}})
        self.assertIn("/Images/Thumb?tag=t", images.landscape(thumb))
        parent = Item.from_json({"Id": "e", "Type": "Episode", "ParentBackdropItemId": "s",
                                 "ParentBackdropImageTags": ["pb"]})
        self.assertTrue(images.landscape(parent).startswith("/Items/s/Images/Backdrop/0?tag=pb"))
        self.assertIsNone(images.landscape(Item.from_json({"Id": "x", "Type": "Movie"})))
        self.assertIsNone(images.poster(Item.from_json({"Id": "x", "Type": "Movie"})))


MON = {"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
       "transform": 0, "reserved": [56, 0, 0, 0]}


class Hyprland(unittest.TestCase):
    def test_geometry_is_wide_and_centred(self):
        g = wm.target_geometry(MON)
        self.assertGreater(g.w, g.h)
        self.assertAlmostEqual(g.w / g.h, 16 / 9, places=2)
        usable_w = 1920 - 56
        self.assertAlmostEqual(g.x - 56, (usable_w - g.w) / 2, delta=1)
        self.assertLessEqual(g.h, 1080 * wm.HEIGHT_FRACTION + 1)

    def test_scaled_and_rotated_monitor(self):
        g = wm.target_geometry({**MON, "width": 3840, "height": 2160, "scale": 2.0, "reserved": [0] * 4})
        self.assertLessEqual(g.w, 1920)
        rot = wm.target_geometry({**MON, "transform": 1, "reserved": [0] * 4})
        self.assertLessEqual(rot.w, 1080)     # portrait monitor: still 16:9, fits the width

    def test_needs_fix_and_commands(self):
        self.assertTrue(wm.needs_fix({"floating": False, "size": [1800, 900]}))
        self.assertTrue(wm.needs_fix({"floating": True, "size": [925, 1050]}))
        self.assertFalse(wm.needs_fix({"floating": True, "size": [1600, 900]}))
        cmds = wm.batch_commands("0xabc", wm.Geometry(10, 20, 1600, 900), floating=False)
        self.assertEqual(cmds, ["dispatch setfloating address:0xabc",
                                "dispatch resizewindowpixel exact 1600 900,address:0xabc",
                                "dispatch movewindowpixel exact 10 20,address:0xabc"])
        self.assertEqual(len(wm.batch_commands("0x1", wm.Geometry(0, 0, 1, 1), floating=True)), 2)

    def test_find_own_client(self):
        clients = [{"pid": 5, "class": "bloomsarchive-player", "address": "0x2"},
                   {"pid": 5, "class": "bloomsarchive", "address": "0x1"},
                   {"pid": 9, "class": "kitty", "address": "0x3"}]
        self.assertEqual(wm.find_own_client(clients, 5, "bloomsarchive")["address"], "0x1")
        self.assertIsNone(wm.find_own_client(clients, 42))


if __name__ == "__main__":
    unittest.main()


class Display(unittest.TestCase):
    def test_card_text_and_progress(self):
        from bloomsarchive.display import card_text, progress_fraction, unplayed_badge
        ep = Item.from_json({"Id": "e", "Type": "Episode", "Name": "The Fall", "SeriesName": "Mushoku",
                             "IndexNumber": 22, "ParentIndexNumber": 2, "RunTimeTicks": 1000,
                             "UserData": {"PlaybackPositionTicks": 250}})
        self.assertEqual(card_text(ep), ("Mushoku", "S02E22 · The Fall"))
        self.assertAlmostEqual(progress_fraction(ep), 0.25)
        mv = Item.from_json({"Id": "m", "Type": "Movie", "Name": "Perfect Blue", "ProductionYear": 1997,
                             "UserData": {"Played": True, "PlayedPercentage": 50}})
        self.assertEqual(card_text(mv), ("Perfect Blue", "1997"))
        self.assertEqual(progress_fraction(mv), 0.0, "finished items show a tick, not a bar")
        sr = Item.from_json({"Id": "s", "Type": "Series", "Name": "Frieren", "PremiereDate": "2023-09-29",
                             "UserData": {"UnplayedItemCount": 140}})
        self.assertEqual(card_text(sr), ("Frieren", "2023 · 140 unwatched"))
        self.assertEqual(unplayed_badge(sr), "99+")
