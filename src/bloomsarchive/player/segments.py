"""Skip-intro/outro data. Pure API code (no Qt), shared with --selfcheck.

Source order:
  1. Jellyfin Media Segments  GET /MediaSegments/{id}           (10.10+)
  2. Intro Skipper plugin     GET /Episode/{id}/IntroSkipperSegments, /IntroTimestamps[/v1]
  3. Chapter names            intro / opening / op / ending / ed / outro / credits
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..api.client import HttpError, JellyfinClient, JellyfinError
from ..api.models import Item, Segment, ticks_to_seconds

log = logging.getLogger(__name__)

SOURCES = ("media-segments", "intro-skipper", "chapters")

_TYPE_ALIASES = {
    "intro": "Intro", "introduction": "Intro", "opening": "Intro",
    "outro": "Outro", "credits": "Outro", "ending": "Outro",
    "recap": "Recap", "preview": "Preview", "commercial": "Commercial",
}
# Jellyfin's MediaSegmentType enum, for servers that serialise it as a number.
_TYPE_ENUM = {0: "Unknown", 1: "Commercial", 2: "Preview", 3: "Recap", 4: "Outro", 5: "Intro"}

_CHAPTER_INTRO = re.compile(r"\b(intro|opening|op)\b", re.I)
_CHAPTER_OUTRO = re.compile(r"\b(outro|ending|ed|credits|end credits)\b", re.I)


def _norm_type(t: Any) -> str | None:
    if isinstance(t, int):
        t = _TYPE_ENUM.get(t, "")
    return _TYPE_ALIASES.get(str(t).strip().lower())


# --------------------------------------------------------------------------- sources

def from_media_segments(client: JellyfinClient, item_id: str) -> list[Segment]:
    data = client.request("GET", f"/MediaSegments/{item_id}",
                          {"includeSegmentTypes": ["Intro", "Outro"]}, retries=0)
    items = data.get("Items", []) if isinstance(data, dict) else (data or [])
    out = []
    for s in items:
        t = _norm_type(s.get("Type"))
        start, end = ticks_to_seconds(s.get("StartTicks")), ticks_to_seconds(s.get("EndTicks"))
        if t in ("Intro", "Outro") and end > start:
            out.append(Segment(t, start, end, "media-segments"))
    return out


def _parse_skipper_entry(kind: str, d: Any) -> Segment | None:
    if not isinstance(d, dict) or d.get("Valid") is False:
        return None
    start = d.get("IntroStart", d.get("Start"))
    end = d.get("IntroEnd", d.get("End"))
    t = _norm_type(kind)
    if t is None or start is None or end is None or float(end) <= float(start):
        return None
    return Segment(t, float(start), float(end), "intro-skipper")


def from_intro_skipper(client: JellyfinClient, item_id: str) -> list[Segment]:
    last_err: Exception | None = None
    for path in (f"/Episode/{item_id}/IntroSkipperSegments",
                 f"/Episode/{item_id}/IntroTimestamps/v1",
                 f"/Episode/{item_id}/IntroTimestamps"):
        try:
            data = client.request("GET", path, retries=0)
        except HttpError as e:
            last_err = e
            continue
        if not isinstance(data, dict):
            continue
        segs: list[Segment] = []
        if "IntroStart" in data:                       # single intro (old plugin)
            s = _parse_skipper_entry("Intro", data)
            segs = [s] if s else []
        else:                                          # {"Introduction": {...}, "Credits": {...}}
            for kind, entry in data.items():
                s = _parse_skipper_entry(kind, entry)
                if s:
                    segs.append(s)
        return segs
    if last_err:
        raise last_err
    return []


def from_chapters(item: Item) -> list[Segment]:
    chapters = sorted(item.chapters, key=lambda c: c.get("StartPositionTicks", 0))
    out = []
    for i, ch in enumerate(chapters):
        name = ch.get("Name") or ""
        t = "Intro" if _CHAPTER_INTRO.search(name) else "Outro" if _CHAPTER_OUTRO.search(name) else None
        if not t:
            continue
        start = ticks_to_seconds(ch.get("StartPositionTicks"))
        end = (ticks_to_seconds(chapters[i + 1].get("StartPositionTicks"))
               if i + 1 < len(chapters) else item.runtime_seconds)
        if end > start:
            out.append(Segment(t, start, end, "chapters"))
    return out


# --------------------------------------------------------------------------- public api

def fetch_segments(client: JellyfinClient, item: Item, prefer: str | None = None) -> list[Segment]:
    """First source that yields anything wins. `prefer` (a remembered source) is tried first."""
    order = list(SOURCES)
    if prefer in order:
        order.remove(prefer)
        order.insert(0, prefer)
    for src in order:
        try:
            if src == "media-segments":
                segs = from_media_segments(client, item.id)
            elif src == "intro-skipper":
                segs = from_intro_skipper(client, item.id)
            else:
                segs = from_chapters(item)
        except JellyfinError as e:
            log.debug("segments: %s unavailable for %s: %s", src, item.id, e)
            continue
        if segs:
            return segs
    return []


def probe_sources(client: JellyfinClient, items: list[Item]) -> dict[str, str]:
    """For --selfcheck: per source, 'works (N segments)', 'endpoint present, no data' or 'unavailable: ...'."""
    report: dict[str, str] = {}
    for src in SOURCES:
        found, reachable, err = 0, False, ""
        for item in items:
            try:
                if src == "media-segments":
                    segs = from_media_segments(client, item.id)
                elif src == "intro-skipper":
                    segs = from_intro_skipper(client, item.id)
                else:
                    segs = from_chapters(item)
                reachable = True
                found += len(segs)
            except JellyfinError as e:
                err = str(e)
        if found:
            report[src] = f"works ({found} segments across {len(items)} episodes)"
        elif src == "chapters" and items:
            report[src] = f"no intro/credits chapter names on the {len(items)} sampled episodes"
        elif reachable:
            report[src] = f"endpoint present, no segments on the {len(items)} sampled episodes"
        else:
            report[src] = f"unavailable ({err[:120] or 'no episodes to test'})"
    return report
