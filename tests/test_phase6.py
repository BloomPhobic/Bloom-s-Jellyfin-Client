import os
import unittest

from helpers import UNREACHABLE, IsolatedTest
from test_session_selfcheck import no_transcode_requests

from bloomsarchive.api import auth, details
from bloomsarchive.api.client import JellyfinClient
from bloomsarchive.api.tracks import OFF
from bloomsarchive.player import launcher
from bloomsarchive.player.session import (PlaybackSession, Resolved, jellyfin_index_for,
                                          mpv_track_for, stream_url)

TRACKS = [
    {"id": 1, "type": "video", "ff-index": 0},
    {"id": 1, "type": "audio", "ff-index": 1, "lang": "jpn"},
    {"id": 2, "type": "audio", "ff-index": 2, "lang": "eng"},
    {"id": 1, "type": "sub", "ff-index": 3, "lang": "eng"},
    {"id": 2, "type": "sub", "external": True, "external-filename": "http://x/Subtitles/4/Stream.srt"},
]


class Launcher(unittest.TestCase):
    def test_args(self):
        a = launcher.build_args({"inherit_user_config": True, "extra_args": "--hwdec=auto --volume=80"},
                                "/run/user/1000/ba.sock")
        self.assertIn("--input-ipc-server=/run/user/1000/ba.sock", a)
        self.assertIn("--wayland-app-id=bloomsarchive-player", a)
        self.assertIn("--idle=yes", a)
        self.assertIn("--fs", a)
        self.assertNotIn("--no-config", a)
        self.assertEqual(a[-2:], ["--hwdec=auto", "--volume=80"])
        self.assertFalse(any("Token" in x or "api_key" in x for x in a), "no secrets on the command line")
        b = launcher.build_args({"inherit_user_config": False, "use_bloom_ui": True}, "s", script="/x/bloom.lua")
        self.assertIn("--no-config", b)
        self.assertIn("--osc=no", b)

    def test_socket_path(self):
        os.environ["XDG_RUNTIME_DIR"] = "/run/user/1234"
        self.assertTrue(launcher.socket_path().startswith("/run/user/1234/bloomsarchive-mpv-"))


class Mapping(unittest.TestCase):
    def test_ff_index(self):
        self.assertEqual(mpv_track_for(TRACKS, "audio", 2), 2)
        self.assertEqual(mpv_track_for(TRACKS, "sub", 3), 1)
        self.assertIsNone(mpv_track_for(TRACKS, "sub", OFF))
        ext = {4: "http://x/Subtitles/4/Stream.srt"}
        self.assertEqual(mpv_track_for(TRACKS, "sub", 4, ext), 2)
        self.assertEqual(jellyfin_index_for(TRACKS, "audio", 1), 1)
        self.assertEqual(jellyfin_index_for(TRACKS, "sub", 2, ext), 4)
        self.assertEqual(jellyfin_index_for(TRACKS, "sub", False), OFF)


class Reporting(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.srv = self.serve()
        self.c = JellyfinClient(self.srv.url, device_id="dev")
        auth.login_password(self.c, "Alice", "hunter2")
        self.movie = details.get_item(self.c, "movie-1")

    def session(self, start=0.0, sub=OFF):
        r = Resolved(self.movie, self.movie.media_sources[0], 2, sub, start)
        return PlaybackSession(self.c, r, played_threshold_pct=90)

    def test_urls(self):
        url = stream_url(self.c, "movie-1", "movie-1")
        self.assertIn("/Videos/movie-1/stream?static=true&MediaSourceId=movie-1", url)
        ps = self.session()
        self.assertEqual(list(ps.external_urls), [4])
        self.assertTrue(ps.external_urls[4].endswith("/Videos/movie-1/movie-1/Subtitles/4/Stream.srt"))

    def test_report_cycle(self):
        ps = self.session(start=595.0, sub=4)
        ps.report_start()
        ps.position, ps.paused = 700.0, True
        ps.report_progress("pause")
        ps.position = 1200.0
        ps.report_stop()
        paths = [p for p, _ in self.srv.state.reports]
        self.assertEqual(paths, ["/Sessions/Playing", "/Sessions/Playing/Progress", "/Sessions/Playing/Stopped"])
        start, prog, stop = [b for _, b in self.srv.state.reports]
        self.assertEqual(start["PositionTicks"], 5950000000)
        self.assertEqual(start["PlayMethod"], "DirectPlay")
        self.assertEqual(start["AudioStreamIndex"], 2)
        self.assertEqual(start["SubtitleStreamIndex"], 4)
        self.assertTrue(prog["IsPaused"])
        self.assertEqual(prog["EventName"], "pause")
        self.assertEqual(stop["PositionTicks"], 12000000000)
        self.assertEqual(len({start["PlaySessionId"], prog["PlaySessionId"], stop["PlaySessionId"]}), 1)
        self.assertFalse(ps.should_mark_played(), "1200 s of 5400 s is below the 90% threshold")
        self.assertEqual(no_transcode_requests(self.srv), [])

    def test_subs_off_is_minus_one_and_eof_marks_played(self):
        self.srv.state.items["movie-1"]["UserData"]["Played"] = False
        ps = self.session()
        ps.report_start()
        self.assertEqual(self.srv.state.reports[0][1]["SubtitleStreamIndex"], -1)
        ps.report_stop(ended=True)
        self.assertEqual(self.srv.state.reports[-1][1]["PositionTicks"], 5400 * 10_000_000)
        self.assertTrue(self.srv.state.items["movie-1"]["UserData"]["Played"])

    def test_early_stop_does_not_mark_played(self):
        self.srv.state.items["movie-1"]["UserData"]["Played"] = False
        ps = self.session()
        ps.report_start()
        ps.position = 600
        ps.report_stop()
        self.assertFalse(self.srv.state.items["movie-1"]["UserData"]["Played"])

    def test_failed_reports_are_queued_and_resent(self):
        ps = self.session()
        ps.report_start()
        good = self.c.base_url
        self.c.base_url = UNREACHABLE
        ps.position = 100
        self.assertFalse(ps.report_progress())
        ps.position = 200
        ps.report_stop()
        self.assertEqual(ps.queued, 2)
        self.c.base_url = good
        self.assertTrue(ps.flush())
        paths = [p for p, _ in self.srv.state.reports]
        self.assertEqual(paths[-2:], ["/Sessions/Playing/Progress", "/Sessions/Playing/Stopped"])
        self.assertEqual(self.srv.state.reports[-1][1]["PositionTicks"], 2000000000)
        self.assertEqual(ps.queued, 0)

    def test_no_reports_after_stop(self):
        ps = self.session()
        ps.report_start()
        ps.report_stop()
        n = len(self.srv.state.reports)
        self.assertFalse(ps.report_progress())
        self.assertTrue(ps.report_stop())
        self.assertEqual(len(self.srv.state.reports), n)


if __name__ == "__main__":
    unittest.main()
