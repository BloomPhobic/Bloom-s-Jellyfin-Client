"""Phase 9: settings plumbing (migration, reset, new options) and the Settings page helpers."""
import json
import unittest

from helpers import UNREACHABLE, IsolatedTest
from test_phase5 import A_ENG, A_JPN, S_ENG_FULL, S_ENG_SIGNS, src

from bloomsarchive import prefs
from bloomsarchive.api import auth, details, tracks
from bloomsarchive.api.client import JellyfinClient
from bloomsarchive.api.models import Item
from bloomsarchive.api.tracks import OFF
from bloomsarchive.config import Settings
from bloomsarchive.player import launcher, overlay_info
from bloomsarchive.session import Session


class Migration(IsolatedTest):
    def test_legacy_skip_mode_becomes_two_settings(self):
        self.settings.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.path.write_text(json.dumps({"playback": {"skip_mode": "auto", "autoplay": False}}))
        s = Settings(self.settings.path)
        self.assertEqual((s.get("playback.skip_intro"), s.get("playback.skip_credits")), ("auto", "auto"))
        self.assertIsNone(s.get("playback.skip_mode"))
        self.assertFalse(s.get("playback.autoplay"), "other values survive")
        self.assertEqual(s.get("ui.smooth_scroll"), True, "new defaults appear")

    def test_explicit_new_keys_win(self):
        self.settings.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.path.write_text(json.dumps({"playback": {"skip_mode": "auto", "skip_credits": "off"}}))
        s = Settings(self.settings.path)
        self.assertEqual((s.get("playback.skip_intro"), s.get("playback.skip_credits")), ("auto", "off"))

    def test_reset_keeps_connection_and_sign_in(self):
        st = self.settings
        st.set("server_id", "srv")
        st.set("addresses", [{"label": "x", "url": "http://10.0.0.2:8096", "public": False}])
        st.set("last_user", {"id": "u", "name": "Alice"})
        st.set("tokens", {"srv:u": "tok"})
        st.set("playback.autoplay", False)
        st.set("subtitles.saved", {"sub-scale": 1.4})
        st.set("series_tracks", {"s": {"audio_lang": "eng"}})
        st.set("library_state", {"lib": {"sort": "year"}})
        st.reset_preferences()
        again = Settings(st.path)
        for s in (st, again):
            self.assertEqual(s.get("server_id"), "srv")
            self.assertEqual(s.get("addresses")[0]["url"], "http://10.0.0.2:8096")
            self.assertEqual(s.get("last_user")["name"], "Alice")
            self.assertEqual(s.get("tokens"), {"srv:u": "tok"})
            self.assertTrue(s.get("playback.autoplay"))
            self.assertEqual(s.get("subtitles.saved", {}), {})
            self.assertEqual(s.get("series_tracks"), {})
            self.assertEqual(s.get("library_state"), {})


class SubtitleWhenAudioMatches(unittest.TestCase):
    def test_three_modes(self):
        s = src([A_JPN, A_ENG, S_ENG_SIGNS, S_ENG_FULL])
        self.assertEqual(tracks.pick_subtitle(s, "eng", ["eng"], None, "forced"), 3)
        self.assertEqual(tracks.pick_subtitle(s, "eng", ["eng"], None, "off"), OFF)
        self.assertEqual(tracks.pick_subtitle(s, "eng", ["eng"], None, "always"), 4)
        # doesn't affect a language you don't read
        self.assertEqual(tracks.pick_subtitle(s, "jpn", ["eng"], None, "off"), 4)

    def test_choice_from_settings(self):
        st = IsolatedTest("run")
        st.setUp()
        try:
            item = Item.from_json({"Id": "e", "Type": "Episode", "SeriesId": "ser", "MediaSources": [
                {"Id": "m", "MediaStreams": [A_JPN, A_ENG, S_ENG_SIGNS, S_ENG_FULL]}]})
            settings = st.settings
            settings.set("languages.audio", ["eng"])
            settings.set("languages.subs_when_audio_matches", "always")
            ch = tracks.choice_from_settings(item, settings)
            self.assertEqual((ch.audio_index, ch.subtitle_index), (2, 4))
            settings.set("series_tracks.ser", {"audio_lang": "jpn", "subtitle": {"off": True}})
            ch = tracks.choice_from_settings(item, settings)
            self.assertEqual((ch.audio_index, ch.subtitle_index), (1, OFF), "series memory still wins")
        finally:
            st.tearDown()

    def test_memory_keeps_series_name(self):
        s = src([A_JPN, S_ENG_FULL])
        self.assertEqual(tracks.memory_for(s, 1, 4, "Frieren")["name"], "Frieren")
        self.assertNotIn("name", tracks.memory_for(s, 1, 4))


class PlayerOptions(unittest.TestCase):
    def test_fullscreen_flag(self):
        self.assertIn("--fs", launcher.build_args({}, "s"))
        self.assertIn("--fs", launcher.build_args({"fullscreen": True}, "s"))
        self.assertNotIn("--fs", launcher.build_args({"fullscreen": False}, "s"))

    def test_extra_args_as_list_or_text(self):
        a = launcher.build_args({"extra_args": ["--volume=80", "--title=a b"]}, "s")
        self.assertEqual(a[-2:], ["--volume=80", "--title=a b"])

    def test_mpv_version_missing(self):
        self.assertIsNone(launcher.mpv_version("/definitely/not/mpv"))

    def test_never_ask_still_watching(self):
        chain = overlay_info.AutoplayChain(0)
        for _ in range(20):
            chain.auto_started()
            self.assertFalse(chain.should_ask)
        chain = overlay_info.AutoplayChain(2)
        self.assertFalse(chain.should_ask)
        chain.auto_started()
        self.assertTrue(chain.should_ask)


class SkipInfo(IsolatedTest):
    def test_separate_modes_in_info(self):
        srv = self.serve()
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, "Alice", "hunter2")
        ep = details.get_item(c, "ep-1")
        info = overlay_info.build_info(ep, ep.media_sources[0], [], [], True,
                                       skip_intro="auto", skip_credits="off")
        self.assertEqual((info["skip_intro"], info["skip_credits"]), ("auto", "off"))
        self.assertNotIn("skip_mode", info)
        json.dumps(info)


class FirstRun(IsolatedTest):
    def test_no_addresses_until_you_add_one(self):
        s = Session(self.settings, self.tokens)
        self.assertEqual(s.candidates(), [])
        d = s.connect()
        self.assertIsNone(d.chosen)
        srv = self.serve()
        addr, _ = prefs.first_address(srv.url)
        s.set_addresses([addr])
        d = s.connect()
        self.assertTrue(d.chosen and d.needs_pin, "found; asks to pin")

    def test_selfcheck_without_addresses(self):
        import io

        from bloomsarchive import selfcheck
        out = io.StringIO()
        code = selfcheck.run(self.settings, out=out, interactive=False)
        self.assertNotEqual(code, 0)
        self.assertIn("no server addresses yet", out.getvalue())


class SessionHelpers(IsolatedTest):
    def test_forget_server(self):
        srv = self.serve()
        self.set_addresses((srv.url, False))
        s = Session(self.settings, self.tokens)
        d = s.connect()
        self.assertTrue(d.needs_pin)
        s.pin_server()
        res = auth.login_password(s.login_client(), "Alice", "hunter2")
        s.complete_login(res)
        s.forget_server()
        self.assertIsNone(self.settings.get("server_id"))
        self.assertIsNone(s.user)
        self.assertIsNone(s.client.token)
        d = s.connect()
        self.assertTrue(d.needs_pin, "asks again")
        s.pin_server()
        self.assertIsNotNone(s.restore_login(), "same server again: the kept token signs straight in")

    def test_set_addresses_drops_stale_last_address(self):
        s = Session(self.settings, self.tokens)
        self.settings.set("last_address", "http://192.168.1.50:8096")
        s.set_addresses([{"label": "TS", "url": "http://100.64.0.10:8096", "public": False}])
        self.assertIsNone(self.settings.get("last_address"))
        self.settings.set("last_address", "http://100.64.0.10:8096")
        s.set_addresses([{"label": "TS", "url": "http://100.64.0.10:8096", "public": False},
                         {"label": "LAN", "url": UNREACHABLE, "public": False}])
        self.assertEqual(self.settings.get("last_address"), "http://100.64.0.10:8096")

    def test_quick_connect_approval_from_settings(self):
        srv = self.serve()
        approver = JellyfinClient(srv.url, device_id="me")
        auth.login_password(approver, "Alice", "hunter2")
        qc = auth.QuickConnectSession(JellyfinClient(srv.url, device_id="tv"))
        code = qc.initiate()
        self.assertTrue(auth.authorize_code(approver, code))
        from bloomsarchive.api.client import NotFound
        with self.assertRaises(NotFound):
            auth.authorize_code(approver, "000000")


class Prefs(unittest.TestCase):
    def test_languages(self):
        self.assertEqual(prefs.clean_language("Japanese (jpn)"), "jpn")
        self.assertEqual(prefs.clean_language(" PT_br "), "pt-br")
        self.assertIsNone(prefs.clean_language("not a code!"))
        self.assertEqual(prefs.add_language(["jpn"], "eng"), ["jpn", "eng"])
        self.assertEqual(prefs.add_language(["jpn"], "JPN"), ["jpn"], "no duplicates")
        self.assertEqual(prefs.move(["a", "b", "c"], 2, -1), ["a", "c", "b"])
        self.assertEqual(prefs.move(["a", "b"], 0, -1), ["a", "b"], "can't move past the top")
        self.assertEqual(prefs.language_label("jpn"), "Japanese (jpn)")
        self.assertEqual(prefs.language_label("xx"), "xx")

    def test_addresses(self):
        R = prefs.AddressRow
        rows, err = prefs.validate_addresses([R("LAN", "192.168.1.50:8096/", False), R("", "", False),
                                              R("", "https://jellyfin.example.com", True)])
        self.assertEqual(err, "")
        self.assertEqual([r["url"] for r in rows], ["http://192.168.1.50:8096", "https://jellyfin.example.com"])
        self.assertEqual(rows[1]["label"], "jellyfin.example.com", "blank name → host")
        self.assertIn("twice", prefs.validate_addresses([R("a", "http://x:1", False), R("b", "x:1", False)])[1])
        self.assertIn("valid", prefs.validate_addresses([R("a", "ftp://x", False)])[1])
        self.assertIn("at least one", prefs.validate_addresses([R("a", " ", False)])[1])
        self.assertFalse(prefs.guess_public("100.64.0.10:8096"))
        self.assertFalse(prefs.guess_public("http://192.168.1.50:8096"))
        self.assertTrue(prefs.guess_public("https://jellyfin.example.com"))

    def test_remembered(self):
        rows = prefs.remembered_series({
            "b": {"audio_lang": "jpn", "subtitle": {"lang": "eng", "forced": False}, "name": "Bocchi"},
            "x": {"audio_lang": "eng", "subtitle": {"off": True}},
            "a": {"audio_lang": "eng", "subtitle": {"lang": "eng", "forced": True}, "name": "aria"},
            "junk": "not a dict"})
        self.assertEqual([r[0] for r in rows], ["a", "b", "x"], "by name, unnamed last")
        self.assertEqual(rows[1][2], "Japanese audio · English subtitles")
        self.assertEqual(rows[0][2], "English audio · English forced subtitles")
        self.assertEqual(rows[2][2], "English audio · no subtitles")

    def test_mpv_args(self):
        self.assertEqual(prefs.args_to_text(["--a", "--title=a b"]), "--a '--title=a b'")
        self.assertEqual(prefs.text_to_args("--a '--title=a b'"), (["--a", "--title=a b"], ""))
        args, err = prefs.text_to_args("--title='oops")
        self.assertEqual(args, [])
        self.assertIn("Couldn't", err)

    def test_style(self):
        self.assertEqual(prefs.set_style({}, "sub-scale", 1.0), {}, "untouched default isn't stored")
        saved = prefs.set_style({}, "sub-scale", 1.3)
        self.assertEqual(saved, {"sub-scale": 1.3})
        self.assertEqual(prefs.set_style(saved, "sub-scale", 1.0), {"sub-scale": 1.0},
                         "an explicit choice stays, even if it equals mpv's default")
        self.assertEqual(prefs.style_value({}, "sub-pos"), 100)
        # the page edits exactly what the player re-applies (controller imports Qt, so read its source)
        import re
        from helpers import ROOT
        text = (ROOT / "src/bloomsarchive/player/controller.py").read_text()
        props = re.search(r"STYLE_PROPS = \((.*?)\)", text).group(1)
        self.assertEqual(set(re.findall(r'"([^"]+)"', props)), set(prefs.STYLE_DEFAULTS))

    def test_first_run_address(self):
        a, err = prefs.first_address("192.168.1.50:8096")
        self.assertEqual((a, err), ({"label": "Home", "url": "http://192.168.1.50:8096", "public": False}, ""))
        a, _ = prefs.first_address("https://jellyfin.example.com/")
        self.assertEqual((a["label"], a["public"]), ("Public", True))
        a, err = prefs.first_address("  ")
        self.assertIsNone(a)
        self.assertIn("e.g.", err)
        self.assertIsNone(prefs.first_address("ftp://nope")[0])

    def test_bytes(self):
        self.assertEqual(prefs.human_bytes(512), "512 B")
        self.assertEqual(prefs.human_bytes(3 * 1024 * 1024), "3.0 MB")


if __name__ == "__main__":
    unittest.main()
