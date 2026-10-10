"""Library browsing: queries, paging, filter options, letter jump, search.

Pure API code; the grid page calls these from workers.
"""
from __future__ import annotations

import string
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .client import HttpError, JellyfinClient
from .library import CARD_FIELDS, IMAGE_PARAMS
from .models import Item

PAGE_SIZE = 60
RANDOM_LIMIT = 1000        # Random can't be paged consistently, so it's one big page

SORTS: dict[str, tuple[str, str]] = {
    # key: (label, Jellyfin sortBy)
    "name": ("Name", "SortName"),
    "added": ("Date added", "DateCreated,SortName"),
    "premiere": ("Release date", "PremiereDate,SortName"),
    "rating": ("Rating", "CommunityRating,SortName"),
    "played": ("Date played", "DatePlayed,SortName"),
    "runtime": ("Runtime", "Runtime,SortName"),
    "random": ("Random", "Random"),
}
# Sensible direction when the user picks a sort (they can flip it).
DEFAULT_DESC = {"added", "premiere", "rating", "played"}

LETTERS = ["#"] + list(string.ascii_uppercase)


def types_for(collection_type: str | None) -> str:
    ct = (collection_type or "").lower()
    if ct == "movies":
        return "Movie"
    if ct == "tvshows":
        return "Series"
    if ct == "boxsets":
        return "BoxSet"
    return "Movie,Series,BoxSet"


@dataclass
class LibraryQuery:
    parent_id: str | None
    include_types: str
    sort: str = "name"
    descending: bool = False
    status: str = "all"                 # all | unplayed | played
    favourites: bool = False
    genres: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    fixed_filters: list[str] = field(default_factory=list)   # e.g. ["IsFavorite"] for the Favourites page

    # -- persistence (per library)
    def state(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("parent_id", "include_types", "fixed_filters"):
            d.pop(k)
        return d

    def with_state(self, state: dict[str, Any] | None) -> "LibraryQuery":
        if not state:
            return self
        allowed = {k: v for k, v in state.items() if k in self.state()}
        q = replace(self, **allowed)
        if q.sort not in SORTS:
            q.sort = "name"
        if q.status not in ("all", "unplayed", "played"):
            q.status = "all"
        return q

    @property
    def is_filtered(self) -> bool:
        return self.status != "all" or self.favourites or bool(self.genres) or bool(self.years)

    def cleared(self) -> "LibraryQuery":
        return replace(self, status="all", favourites=False, genres=[], years=[])

    @property
    def is_random(self) -> bool:
        return self.sort == "random"

    @property
    def letter_jump_ok(self) -> bool:
        return self.sort == "name"

    # -- request
    def params(self) -> dict[str, Any]:
        filters = list(self.fixed_filters)
        if self.status == "played":
            filters.append("IsPlayed")
        elif self.status == "unplayed":
            filters.append("IsUnplayed")
        if self.favourites and "IsFavorite" not in filters:
            filters.append("IsFavorite")
        p: dict[str, Any] = {
            "parentId": self.parent_id,
            "recursive": True,
            "includeItemTypes": self.include_types,
            "sortBy": SORTS[self.sort][1],
            "sortOrder": "Descending" if (self.descending and not self.is_random) else "Ascending",
            "filters": ",".join(filters) or None,
            "genres": "|".join(self.genres) or None,
            "years": ",".join(str(y) for y in self.years) or None,
            "fields": CARD_FIELDS,
            **IMAGE_PARAMS,
        }
        return {k: v for k, v in p.items() if v is not None}


@dataclass
class Page:
    items: list[Item]
    total: int
    start: int


def fetch_page(client: JellyfinClient, q: LibraryQuery, start: int = 0,
               limit: int = PAGE_SIZE) -> Page:
    if q.is_random:
        start, limit = 0, RANDOM_LIMIT
    data = client.compat.call("items", params={**q.params(), "startIndex": start, "limit": limit,
                                               "enableTotalRecordCount": True})
    data = data or {}
    items = [Item.from_json(x) for x in data.get("Items", [])]
    total = int(data.get("TotalRecordCount", len(items)))
    if q.is_random:
        total = len(items)      # one big page is all we show
    return Page(items, total, start)


def count(client: JellyfinClient, q: LibraryQuery, **extra: Any) -> int:
    params = {**q.params(), "limit": 0, "enableTotalRecordCount": True, **extra}
    params.pop("fields", None)
    data = client.compat.call("items", params=params) or {}
    return int(data.get("TotalRecordCount", 0))


def letter_index(client: JellyfinClient, q: LibraryQuery, letter: str, total: int | None = None) -> int:
    """Row of the first title under `letter` in the current (name-sorted) order.

    Counts the titles that sort before it with nameLessThan, so it needs no
    full listing. '#' means titles before 'A' (digits, symbols).
    """
    if not q.letter_jump_ok:
        raise ValueError("letter jump needs the Name sort")
    letter = letter.upper()
    if total is None:
        total = count(client, q)
    if not q.descending:
        return 0 if letter == "#" else count(client, q, nameLessThan=letter)
    # Z→A: the letter's block starts after everything that sorts after it.
    nxt = LETTERS[LETTERS.index(letter) + 1] if letter != "Z" else None
    before_next = total if nxt is None else count(client, q, nameLessThan=nxt)
    return max(0, total - before_next)


@dataclass
class FilterOptions:
    genres: list[str]
    years: list[int]


def filter_options(client: JellyfinClient, q: LibraryQuery) -> FilterOptions:
    params = {"userId": client.user_id, "parentId": q.parent_id, "includeItemTypes": q.include_types,
              "recursive": True}
    try:
        data = client.get("/Items/Filters2", **params) or {}
        genres = [g.get("Name", "") for g in data.get("Genres", []) if isinstance(g, dict)]
        years = [int(y) for y in data.get("Years", []) if str(y).isdigit()] if "Years" in data else []
    except HttpError:
        data = client.get("/Items/Filters", **params) or {}       # pre-10.x shape
        genres = [g for g in data.get("Genres", []) if isinstance(g, str)]
        years = [int(y) for y in data.get("Years", []) if str(y).isdigit()]
    return FilterOptions(sorted({g for g in genres if g}, key=str.casefold),
                         sorted(set(years), reverse=True))


# --------------------------------------------------------------------------- search

SEARCH_GROUPS = (("Movies", "Movie"), ("Shows", "Series"), ("Episodes", "Episode"))


def search(client: JellyfinClient, term: str, item_type: str, limit: int = 24) -> list[Item]:
    data = client.compat.call("items", params={
        "searchTerm": term, "recursive": True, "includeItemTypes": item_type, "limit": limit,
        "fields": CARD_FIELDS, **IMAGE_PARAMS})
    return [Item.from_json(x) for x in (data or {}).get("Items", [])]
