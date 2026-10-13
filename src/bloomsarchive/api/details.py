"""Detail-page data: full item, streams, seasons, episodes, next up for a series."""
from __future__ import annotations

import logging

from .client import HttpError, JellyfinClient
from .library import IMAGE_PARAMS
from .models import Item, MediaSource

log = logging.getLogger(__name__)

DETAIL_FIELDS = ("Overview,Genres,People,Studios,Taglines,MediaSources,MediaStreams,Chapters,"
                 "PrimaryImageAspectRatio,ProductionYear,PremiereDate,DateCreated,ParentId,"
                 "OfficialRating,CommunityRating,RunTimeTicks")
EPISODE_FIELDS = "Overview,PrimaryImageAspectRatio,MediaSources,MediaStreams,RunTimeTicks"


def get_item(client: JellyfinClient, item_id: str) -> Item:
    data = client.compat.call("item", item_id=item_id,
                              params={"fields": DETAIL_FIELDS, "enableImageTypes": "Primary,Backdrop,Thumb,Logo"})
    return Item.from_json(data or {})


def _direct_play_profile_body() -> dict:
    """PlaybackInfo body that can't produce a transcode.

    PlaybackInfo is only *information*: it lists the media sources with their
    streams. Transcoding and stream copy are explicitly disabled so the
    server never prepares a transcode URL for us.
    """
    return {
        "EnableDirectPlay": True,
        "EnableDirectStream": False,
        "EnableTranscoding": False,
        "AllowVideoStreamCopy": False,
        "AllowAudioStreamCopy": False,
        "AutoOpenLiveStream": False,
        "DeviceProfile": {
            "Name": "Bloom's Archive (direct play only)",
            "MaxStreamingBitrate": 1_000_000_000,
            "DirectPlayProfiles": [{"Type": "Video"}, {"Type": "Audio"}],
            "TranscodingProfiles": [],
            "SubtitleProfiles": [{"Format": f, "Method": "External"} for f in
                                 ("srt", "ass", "ssa", "vtt", "sub", "pgssub", "dvdsub")] +
                                [{"Format": f, "Method": "Embed"} for f in
                                 ("srt", "ass", "ssa", "vtt", "sub", "pgssub", "dvdsub")],
        },
    }


def playback_sources(client: JellyfinClient, item_id: str) -> list[MediaSource]:
    data = client.request("POST", f"/Items/{item_id}/PlaybackInfo",
                          {"userId": client.user_id}, _direct_play_profile_body()) or {}
    return [MediaSource.from_json(s) for s in data.get("MediaSources", [])]


def ensure_streams(client: JellyfinClient, item: Item) -> Item:
    """Make sure item.media_sources carry their audio/subtitle streams.

    Some servers leave MediaStreams out of the item response; then ask
    PlaybackInfo (information only, transcoding disabled).
    """
    if item.type not in ("Movie", "Episode", "Video", "MusicVideo"):
        return item
    if item.media_sources and all(s.streams for s in item.media_sources):
        return item
    try:
        sources = playback_sources(client, item.id)
    except HttpError as e:
        log.info("PlaybackInfo unavailable for %s: %s", item.id, e)
        return item
    if sources:
        item.media_sources = sources
    return item


def seasons(client: JellyfinClient, series_id: str) -> list[Item]:
    data = client.get(f"/Shows/{series_id}/Seasons", userId=client.user_id,
                      fields="PrimaryImageAspectRatio,ChildCount", **IMAGE_PARAMS) or {}
    return [Item.from_json(x) for x in data.get("Items", [])]


def episodes(client: JellyfinClient, series_id: str, season_id: str | None = None) -> list[Item]:
    data = client.get(f"/Shows/{series_id}/Episodes", userId=client.user_id, seasonId=season_id,
                      fields=EPISODE_FIELDS, **IMAGE_PARAMS) or {}
    return [Item.from_json(x) for x in data.get("Items", [])]


def next_up_for_series(client: JellyfinClient, series_id: str) -> Item | None:
    data = client.get("/Shows/NextUp", userId=client.user_id, seriesId=series_id, limit=1,
                      fields=EPISODE_FIELDS, enableResumable=True, **IMAGE_PARAMS) or {}
    items = data.get("Items", [])
    return Item.from_json(items[0]) if items else None


def pick_season(season_list: list[Item], next_up: Item | None) -> Item | None:
    """Season to open: the Next Up episode's, else the first with unwatched episodes, else the first.

    Specials (season 0) are only chosen if Next Up points there or nothing else exists.
    """
    if not season_list:
        return None
    if next_up is not None:
        for s in season_list:
            if s.id == next_up.season_id:
                return s
    regular = [s for s in season_list if (s.index_number or 0) > 0] or season_list
    for s in regular:
        if (s.user_data.unplayed_item_count or 0) > 0:
            return s
    return regular[0]


def people(item: Item, limit: int = 8) -> tuple[list[str], list[str]]:
    """(directors, cast names) from the People field."""
    ppl = item.raw.get("People") or []
    directors = [p.get("Name", "") for p in ppl if p.get("Type") == "Director"]
    cast = [p.get("Name", "") for p in ppl if p.get("Type") in ("Actor", "GuestStar")]
    return [d for d in directors if d][:3], [c for c in cast if c][:limit]


def library_for(client: JellyfinClient, item: Item, view_ids: list[str]) -> str | None:
    """Which of the user's libraries an item lives in (for 'genre → library' links)."""
    if item.parent_id in view_ids:
        return item.parent_id
    target = item.series_id or item.id
    try:
        ancestors = client.get(f"/Items/{target}/Ancestors", userId=client.user_id) or []
    except HttpError:
        return None
    for a in ancestors if isinstance(ancestors, list) else []:
        if a.get("Id") in view_ids:
            return a["Id"]
    return None
