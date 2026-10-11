import unittest

from helpers import IsolatedTest

from bloomsarchive.__main__ import _normalize_dashes
from bloomsarchive.api import auth, browse
from bloomsarchive.api.browse import LibraryQuery
from bloomsarchive.api.client import JellyfinClient

N = 130   # generated movies (+ "2001 Numbers" + "Test Movie")


class Query(unittest.TestCase):
    def test_params(self):
        q = LibraryQuery("lib", "Movie", sort="added", descending=True, status="unplayed",
                         favourites=True, genres=["Action", "Sci-Fi"], years=[1999, 2001])
        p = q.params()
        self.assertEqual(p["sortBy"], "DateCreated,SortName")
        self.assertEqual(p["sortOrder"], "Descending")
        self.assertEqual(p["filters"], "IsUnplayed,IsFavorite")
        self.assertEqual(p["genres"], "Action|Sci-Fi")
        self.assertEqual(p["years"], "1999,2001")
        self.assertTrue(q.is_filtered)
        self.assertFalse(q.cleared().is_filtered)
        self.assertNotIn("filters", LibraryQuery("lib", "Movie").params())

    def test_state_roundtrip_and_validation(self):
        q = LibraryQuery("lib", "Series", sort="rating", descending=True, genres=["Drama"])
        q2 = LibraryQuery("lib", "Series").with_state(q.state())
        self.assertEqual((q2.sort, q2.descending, q2.genres), ("rating", True, ["Drama"]))
        bad = LibraryQuery("lib", "Series").with_state({"sort": "nonsense", "status": "x", "parent_id": "evil"})
        self.assertEqual((bad.sort, bad.status, bad.parent_id), ("name", "all", "lib"))

    def test_types_and_favourites_page(self):
        self.assertEqual(browse.types_for("tvshows"), "Series")
        self.assertEqual(browse.types_for(None), "Movie,Series,BoxSet")
        fav = LibraryQuery(None, "Movie,Series,Episode", fixed_filters=["IsFavorite"], favourites=True)
        self.assertEqual(fav.params()["filters"], "IsFavorite")
        self.assertNotIn("parentId", fav.params())


class Browsing(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.srv = self.serve(extra_movies=N)
        self.c = JellyfinClient(self.srv.url, device_id="dev")
        auth.login_password(self.c, "Alice", "hunter2")
        self.q = LibraryQuery("view-movies", "Movie")

    def names(self, q, start=0, limit=500):
        return [i.name for i in browse.fetch_page(self.c, q, start, limit).items]

    def test_paging(self):
        p0 = browse.fetch_page(self.c, self.q, 0, 60)
        p1 = browse.fetch_page(self.c, self.q, 60, 60)
        self.assertEqual(p0.total, N + 2)
        self.assertEqual(len(p0.items), 60)
        self.assertFalse({i.id for i in p0.items} & {i.id for i in p1.items})
        self.assertEqual(p0.items[0].name, "2001 Numbers")

    def test_letter_jump_ascending_and_descending(self):
        all_names = self.names(self.q)
        for letter in ("#", "A", "M", "T", "Z"):
            idx = browse.letter_index(self.c, self.q, letter)
            first = all_names[idx]
            if letter == "#":
                self.assertEqual(idx, 0)
            else:
                self.assertTrue(first.upper().startswith(letter), (letter, idx, first))
                self.assertFalse(any(n.upper().startswith(letter) for n in all_names[:idx]))
        qd = LibraryQuery("view-movies", "Movie", descending=True)
        desc = self.names(qd)
        for letter in ("Z", "M", "A"):
            idx = browse.letter_index(self.c, qd, letter)
            self.assertTrue(desc[idx].upper().startswith(letter), (letter, idx, desc[idx]))
        self.assertTrue(desc[browse.letter_index(self.c, qd, "#")][0].isdigit())

    def test_letter_jump_respects_filters(self):
        q = LibraryQuery("view-movies", "Movie", genres=["Comedy"])
        names = self.names(q)
        idx = browse.letter_index(self.c, q, "M")
        self.assertTrue(names[idx].startswith("M"))
        with self.assertRaises(ValueError):
            browse.letter_index(self.c, LibraryQuery("view-movies", "Movie", sort="added"), "M")

    def test_filters_and_options(self):
        opts = browse.filter_options(self.c, self.q)
        self.assertEqual(opts.genres, ["Action", "Comedy", "Drama"])
        self.assertEqual(opts.years[0], 2021)
        q = LibraryQuery("view-movies", "Movie", genres=["Action"], years=[1990])
        for it in browse.fetch_page(self.c, q).items:
            self.assertEqual(it.production_year, 1990)
        self.assertEqual(browse.count(self.c, LibraryQuery("view-movies", "Movie", status="played")), 1)

    def test_random_is_one_page(self):
        p = browse.fetch_page(self.c, LibraryQuery("view-movies", "Movie", sort="random"), start=60)
        self.assertEqual(p.start, 0)
        self.assertEqual(p.total, N + 2)

    def test_search(self):
        self.assertEqual([i.name for i in browse.search(self.c, "test", "Movie")], ["Test Movie"])
        self.assertEqual([i.name for i in browse.search(self.c, "anime", "Series")], ["Test Anime"])
        self.assertEqual(len(browse.search(self.c, "episode", "Episode")), 3)


class Cli(unittest.TestCase):
    def test_inner_hyphen(self):
        self.assertEqual(_normalize_dashes(["--install‑desktop"]), ["--install-desktop"])
        self.assertEqual(_normalize_dashes(["​--pin"]), ["--pin"])


if __name__ == "__main__":
    unittest.main()


class YearsFix(IsolatedTest):
    def test_years_come_from_legacy_filters_and_filter_works(self):
        srv = self.serve(extra_movies=40)
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, "Alice", "hunter2")
        q = LibraryQuery("view-movies", "Movie")
        opts = browse.filter_options(c, q)
        self.assertTrue(opts.years, "years must not be empty even though Filters2 has none")
        self.assertIn("Action", opts.genres)
        y = opts.years[3]
        items = browse.fetch_page(c, LibraryQuery("view-movies", "Movie", years=[y])).items
        self.assertTrue(items)
        self.assertTrue(all(i.production_year == y for i in items))

    def test_year_sort(self):
        srv = self.serve(extra_movies=40)
        c = JellyfinClient(srv.url, device_id="dev")
        auth.login_password(c, "Alice", "hunter2")
        q = LibraryQuery("view-movies", "Movie", sort="year", descending=True)
        self.assertTrue(q.params()["sortBy"].startswith("ProductionYear"))
        ys = [i.production_year for i in browse.fetch_page(c, q).items]
        self.assertEqual(ys, sorted(ys, reverse=True))
