"""What the in-player overlay (bloom.lua) shows, built from Jellyfin data. No Qt."""
from __future__ import annotations

from pathlib import Path

from ..api import tracks
from ..api.models import Item, MediaSource

SCRIPT = Path(__file__).resolve().parent.parent / "mpv_assets" / "bloom.lua"


def script_path() -> str | None:
    return str(SCRIPT) if SCRIPT.exists() else None


def playable_order(episodes: list[Item]) -> list[Item]:
    """Episodes in watch order for prev/next: specials (season 0) left out."""
    regular = [e for e in episodes if (e.parent_index_number or 0) > 0]
    return regular or list(episodes)


def neighbours(episodes: list[Item], current_id: str) -> tuple[Item | None, Item | None]:
    order = playable_order(episodes)
    for i, e in enumerate(order):
        if e.id == current_id:
            return (order[i - 1] if i > 0 else None, order[i + 1] if i + 1 < len(order) else None)
    return None, None


def _short_version(src: MediaSource) -> str:
    video = next((s for s in src.streams if s.type == "Video"), None)
    h = (video.raw.get("Height") if video else None) or 0
    if h >= 2000:
        return "4K"
    if h:
        return f"{h}p"
    return (src.container or "file").upper()


class AutoplayChain:
    """Counts episodes that started on their own; asks "Still watching?" at the limit.

    Any real interaction (a click in the player, pausing, seeking) resets it, so
    only someone who has genuinely dozed off gets asked.
    """

    def __init__(self, limit: int = 5):
        self.limit = max(0, int(limit if limit is not None else 5))
        self.count = 0

    def interaction(self) -> None:
        self.count = 0

    def auto_started(self) -> None:
        self.count += 1

    @property
    def should_ask(self) -> bool:
        """True when the *next* automatic start would exceed the limit (never if limit is 0)."""
        return self.limit > 0 and self.count + 1 >= self.limit


def segments_for_lua(segments) -> list[dict]:
    return [s.to_lua() for s in segments]


def build_info(item: Item, source: MediaSource | None, seasons: list[Item], episodes: list[Item],
               autoplay: bool, skip_intro: str = "button", skip_credits: str = "button",
               up_next_seconds: int = 20, still_watching: bool = False) -> dict:
    is_ep = item.type == "Episode"
    if is_ep:
        bits = [b for b in (item.series_name, item.episode_label) if b]
        subtitle = " · ".join(bits)
    else:
        bits = [str(item.production_year) if item.production_year else "",
                item.official_rating or ""]
        subtitle = " · ".join(b for b in bits if b)
    prev, nxt = neighbours(episodes, item.id) if is_ep else (None, None)

    grouped: list[dict] = []
    if is_ep and episodes:
        names = {s.id: s.name for s in seasons}
        order: list[str] = [s.id for s in seasons]
        by_season: dict[str, list[Item]] = {}
        for e in episodes:
            sid = e.season_id or "?"
            by_season.setdefault(sid, []).append(e)
            if sid not in order:
                order.append(sid)
        for sid in order:
            eps = by_season.get(sid)
            if not eps:
                continue
            first = eps[0]
            name = names.get(sid) or (f"Season {first.parent_index_number}"
                                      if first.parent_index_number else "Specials")
            grouped.append({"name": name, "episodes": [
                {"id": e.id, "number": e.index_number, "name": e.name, "played": bool(e.user_data.played)}
                for e in eps]})

    versions = []
    if source is not None and len(item.media_sources) > 1:
        for i, s in enumerate(item.media_sources):
            versions.append({"id": s.id, "label": tracks.source_label(s, i), "short": _short_version(s),
                             "current": s.id == source.id})

    nxt_info = None
    if nxt is not None:
        nxt_info = {"id": nxt.id, "label": nxt.episode_label, "title": nxt.name}

    return {
        "next": nxt_info,
        "skip_intro": skip_intro if skip_intro in ("button", "auto", "off") else "button",
        "skip_credits": skip_credits if skip_credits in ("button", "auto", "off") else "button",
        "up_next_seconds": int(up_next_seconds or 20),
        "still_watching": bool(still_watching),
        "title": item.name,
        "subtitle": subtitle,
        "is_episode": is_ep,
        "current_id": item.id,
        "has_prev": prev is not None,
        "has_next": nxt is not None,
        "favorite": bool(item.user_data.is_favorite),
        "autoplay": bool(autoplay),
        "seasons": grouped,
        "versions": versions,
    }
