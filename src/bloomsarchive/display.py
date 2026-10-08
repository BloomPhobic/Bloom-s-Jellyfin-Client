"""Text shown on cards and pages, kept out of Qt so it's testable."""
from __future__ import annotations

from .api.models import Item


def year_of(item: Item) -> str:
    if item.production_year:
        return str(item.production_year)
    if item.premiere_date and item.premiere_date[:4].isdigit():
        return item.premiere_date[:4]
    return ""


def card_text(item: Item) -> tuple[str, str]:
    """(title, subtitle) for a card."""
    t = item.type
    if t == "Episode":
        label = item.episode_label
        sub = f"{label} · {item.name}" if label else item.name
        return item.series_name or item.name, sub
    if t == "Season":
        return item.series_name or item.name, item.name
    if t in ("CollectionFolder", "UserView", "Folder"):
        return item.name, ""
    if t == "Series":
        unplayed = item.user_data.unplayed_item_count or 0
        bits = [b for b in (year_of(item), f"{unplayed} unwatched" if unplayed and not item.user_data.played else "") if b]
        return item.name, " · ".join(bits)
    return item.name, year_of(item)


def progress_fraction(item: Item) -> float:
    """0..1 watched fraction for in-progress items (0 when untouched or finished)."""
    ud = item.user_data
    if ud.played:
        return 0.0
    if ud.played_percentage:
        return max(0.0, min(1.0, ud.played_percentage / 100))
    if ud.playback_position_ticks and item.run_time_ticks:
        return max(0.0, min(1.0, ud.playback_position_ticks / item.run_time_ticks))
    return 0.0


def unplayed_badge(item: Item) -> str:
    n = item.user_data.unplayed_item_count or 0
    if item.type in ("Series", "Season") and n and not item.user_data.played:
        return "99+" if n > 99 else str(n)
    return ""
