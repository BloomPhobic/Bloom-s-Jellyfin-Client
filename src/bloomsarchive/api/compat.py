"""Version detection and new-vs-legacy route fallbacks.

Jellyfin 10.9 moved user-scoped routes (/Users/{id}/Items/Resume ...) to
top-level ones with a userId query parameter (/UserItems/Resume ...). We try
the route the version suggests, fall back on 404, and cache what worked.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from .client import NotFound

if TYPE_CHECKING:
    from .client import JellyfinClient

log = logging.getLogger(__name__)

NEW_ROUTES_SINCE = (10, 9, 0)


def parse_version(v: str | None) -> tuple[int, ...]:
    if not v:
        return ()
    nums = re.findall(r"\d+", v)
    return tuple(int(n) for n in nums[:3])


@dataclass(frozen=True)
class Route:
    new: str        # top-level route; userId passed as a query parameter
    legacy: str     # /Users/{uid}/... form


ROUTES: dict[str, Route] = {
    "views":    Route("/UserViews", "/Users/{uid}/Views"),
    "resume":   Route("/UserItems/Resume", "/Users/{uid}/Items/Resume"),
    "latest":   Route("/Items/Latest", "/Users/{uid}/Items/Latest"),
    "items":    Route("/Items", "/Users/{uid}/Items"),
    "item":     Route("/Items/{id}", "/Users/{uid}/Items/{id}"),
    "played":   Route("/UserPlayedItems/{id}", "/Users/{uid}/PlayedItems/{id}"),
    "favorite": Route("/UserFavoriteItems/{id}", "/Users/{uid}/FavoriteItems/{id}"),
}


class Compat:
    def __init__(self, client: "JellyfinClient"):
        self.client = client
        self._lock = threading.Lock()
        self._choice: dict[str, str] = {}   # route name -> "new" | "legacy"
        self._version: tuple[int, ...] | None = None

    # -- version
    @property
    def version(self) -> tuple[int, ...]:
        if self._version is None:
            info = self.client.server_info
            if info is None:
                try:
                    info = self.client.public_info()
                except Exception as e:
                    log.debug("version detection failed: %s", e)
                    info = {}
            self._version = parse_version(info.get("Version"))
        return self._version

    def set_version(self, version: str) -> None:
        self._version = parse_version(version)

    def prefers_new(self) -> bool:
        v = self.version
        return not v or v >= NEW_ROUTES_SINCE   # unknown -> assume modern

    def choices(self) -> dict[str, str]:
        with self._lock:
            return dict(self._choice)

    # -- calls
    def _order(self, name: str) -> list[str]:
        with self._lock:
            cached = self._choice.get(name)
        if cached:
            return [cached]
        return ["new", "legacy"] if self.prefers_new() else ["legacy", "new"]

    def _build(self, name: str, kind: str, item_id: str | None,
               params: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
        route = ROUTES[name]
        uid = self.client.user_id or ""
        p = dict(params or {})
        if kind == "new":
            path = route.new.format(id=item_id or "")
            p.setdefault("userId", uid)
        else:
            path = route.legacy.format(uid=uid, id=item_id or "")
            p.pop("userId", None)
        return path, p

    def call(self, name: str, method: str = "GET", item_id: str | None = None,
             params: Mapping[str, Any] | None = None, json_body: Any = None) -> Any:
        order = self._order(name)
        first_err: NotFound | None = None
        for kind in order:
            path, p = self._build(name, kind, item_id, params)
            try:
                result = self.client.request(method, path, p, json_body)
            except NotFound as e:
                first_err = first_err or e
                continue
            with self._lock:
                if self._choice.get(name) != kind:
                    log.debug("compat: %s -> %s route", name, kind)
                self._choice[name] = kind
            return result
        assert first_err is not None
        raise first_err

    def resolve_path(self, name: str, item_id: str | None = None) -> str:
        """The path that would be used first (for logging / selfcheck)."""
        return self._build(name, self._order(name)[0], item_id, None)[0]
