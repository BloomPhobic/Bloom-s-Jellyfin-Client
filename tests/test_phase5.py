import unittest

from helpers import IsolatedTest
from test_session_selfcheck import no_transcode_requests

from bloomsarchive.api import auth, details, tracks
from bloomsarchive.api.client import JellyfinClient
from bloomsarchive.api.models import Item, MediaSource
from bloomsarchive.api.tracks import OFF


def src(streams, **kw):
    return MediaSource.from_json({"Id": "s", "MediaStreams": streams, **kw})


A_JPN = {"Index": 1, "Type": "Audio", "Language": "jpn", "Codec": "flac", "Channels": 2, "IsDefault": True}
A_ENG = {"Index": 2, "Type": "Audio", "Language": "eng", "Codec": "aac", "Channels": 6}
S_ENG_SIGNS = {"Index": 3, "Type": "Subtitle", "Language": "eng", "Codec": "ass", "Title": "Signs", "IsForced": True}
S_ENG_FULL = {"Index": 4, "Type": "Subtitle", "Language": "eng", "Codec": "ass", "Title": "Full"}
S_TUR = {"Index": 5, "Type": "Subtitle", "Language": "tur", "Codec": "subrip"}


class Tracks(unittest.TestCase):
    def test_anime_defaults(self):
        s = src([A_JPN, A_ENG, S_ENG_SIGNS, S_ENG_FULL, S_TUR])
        a = tracks.pick_audio(s, ["jpn", "eng"], None)
        self.assertEqual(a, 1)
        self.assertEqual(tracks.pick_subtitle(s, "jpn", ["eng"], None), 4, "full subs, not signs")

    def test_dub_gets_forced_only(self):
        s = src([A_JPN, A_ENG, S_ENG_SIGNS, S_ENG_FULL])
        a = tracks.pick_audio(s, ["eng"], None)
        self.assertEqual(a, 2)
        self.assertEqual(tracks.pick_subtitle(s, "eng", ["eng"], None), 3)
        self.assertEqual(tracks.pick_subtitle(src([A_ENG, S_ENG_FULL]), "eng", ["eng"], None), OFF)

    def test_memory_wins_and_roundtrips(self):
        s = src([A_JPN, A_ENG, S_ENG_FULL, S_TUR])
        mem = tracks.memory_for(s, 1, 5)
        self.assertEqual(mem, {"audio_lang": "jpn", "subtitle": {"lang": "tur", "forced": False}})
        item = Item.from_json({"Id": "e", "Type": "Episode", "MediaSources": [
            {"Id": "x", "MediaStreams": [A_ENG, A_JPN, S_ENG_FULL, S_TUR]}]})
        c = tracks.default_choice(item, ["eng"], ["eng"], mem)
        self.assertEqual((c.audio_index, c.subtitle_index), (1, 5))
        off = tracks.memory_for(s, 2, OFF)
        self.assertEqual(tracks.default_choice(item, ["jpn"], ["eng"], off).subtitle_index, OFF)

    def test_no_preferences_match(self):
        s = src([{"Index": 0, "Type": "Audio", "Language": "kor"}], DefaultAudioStreamIndex=0)
        self.assertEqual(tracks.pick_audio(s, ["jpn"], None), 0)
        self.assertEqual(tracks.pick_subtitle(s, "kor", ["eng"], None), OFF)

    def test_labels(self):
        self.assertEqual(tracks.audio_label(src([A_JPN]).audio_streams[0]), "Japanese · FLAC 2.0  (default)")
        self.assertEqual(tracks.subtitle_label(src([S_ENG_SIGNS]).subtitle_streams[0]),
                         "English · Signs · ASS  (forced)")
        self.assertEqual(tracks.subtitle_label(src([S_TUR]).subtitle_streams[0]), "Turkish · SRT")

    def test_resume(self):
        it = Item.from_json({"Id": "e", "Type": "Movie", "UserData": {"PlaybackPositionTicks": 600 * 10_000_000}})
        self.assertEqual(tracks.resume_seconds(it, 5), 595)
        self.assertEqual(tracks.resume_seconds(Item.from_json({"Id": "x", "Type": "Movie"})), 0)


class DetailData(IsolatedTest):
    def client(self, **cfg):
        srv = self.serve(**cfg)
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, "Alice", "hunter2")
        return srv, c

    def test_movie_detail(self):
        _srv, c = self.client()
        m = details.get_item(c, "movie-1")
        self.assertEqual(len(m.media_sources[0].streams), 5)
        directors, cast = details.people(m)
        self.assertEqual((directors, cast), (["Satoshi Kon"], ["Junko Iwao"]))

    def test_stream_fallback_is_direct_play_only(self):
        srv, c = self.client(streams_in_detail=False)
        m = details.get_item(c, "movie-1")
        self.assertFalse(m.media_sources[0].streams)
        details.ensure_streams(c, m)
        self.assertEqual(len(m.media_sources[0].streams), 5)
        pi = [r for r in srv.log if r.path.endswith("/PlaybackInfo")][0]
        self.assertFalse(pi.body["EnableTranscoding"])
        self.assertFalse(pi.body["DeviceProfile"]["TranscodingProfiles"])
        self.assertEqual(no_transcode_requests(srv), [])

    def test_seasons_episodes_and_pick(self):
        _srv, c = self.client(second_season=True)
        ss = details.seasons(c, "series-1")
        self.assertEqual([s.name for s in ss], ["Season 1", "Season 2"])
        eps = details.episodes(c, "series-1", "season-2")
        self.assertEqual([e.episode_label for e in eps], ["S02E01", "S02E02"])
        nu = details.next_up_for_series(c, "series-1")
        self.assertEqual(nu.id, "ep-1")                       # half-watched episode
        self.assertEqual(details.pick_season(ss, nu).name, "Season 1")
        for e in ("ep-1", "ep-2", "ep-3"):
            c.compat.call("played", "POST", item_id=e)
        ss = details.seasons(c, "series-1")
        nu = details.next_up_for_series(c, "series-1")
        self.assertEqual(details.pick_season(ss, nu).name, "Season 2")
        self.assertEqual(details.pick_season(ss, None).name, "Season 2")


if __name__ == "__main__":
    unittest.main()


class Overviews(unittest.TestCase):
    def test_html_cleanup(self):
        from bloomsarchive.display import overview_html, overview_plain
        t = ("Teikou won.\n<br><br>\nAt Seirin, <i>Kuroko no Basket</i> &amp; friends"
             "<script>alert(1)</script> <a href='x'>more</a> 1 < 2")
        self.assertEqual(overview_html(t),
                         "Teikou won.<br><br>At Seirin, <i>Kuroko no Basket</i> &amp; friends more 1 &lt; 2")
        self.assertEqual(overview_plain(t), "Teikou won. At Seirin, Kuroko no Basket & friends more 1 < 2")
        self.assertEqual(overview_html("<p>One</p><p>Two</p>"), "One<br><br>Two")
        self.assertEqual(overview_html(None), "")


class GenreLinks(IsolatedTest):
    def test_library_for(self):
        srv = self.serve()
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, "Alice", "hunter2")
        views = ["view-movies", "view-anime"]
        ep = details.get_item(c, "ep-2")
        self.assertEqual(details.library_for(c, ep, views), "view-anime")
        self.assertEqual(details.library_for(c, details.get_item(c, "movie-1"), views), "view-movies")
        self.assertIsNone(details.library_for(c, details.get_item(c, "movie-1"), ["elsewhere"]))
