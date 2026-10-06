"""Typed views over Jellyfin JSON, plus the one place ticks are converted.

Jellyfin time values are ticks: 10,000,000 per second.
Each model keeps the raw dict in `.raw` so later phases can reach fields not modelled yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TICKS_PER_SECOND = 10_000_000


def ticks_to_seconds(ticks: int | float | None) -> float:
    return (ticks or 0) / TICKS_PER_SECOND


def seconds_to_ticks(seconds: float | None) -> int:
    return int(round((seconds or 0) * TICKS_PER_SECOND))


def _g(d: dict, key: str, default: Any = None) -> Any:
    v = d.get(key)
    return default if v is None else v


@dataclass
class ServerInfo:
    id: str
    name: str
    version: str
    product: str
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: dict) -> "ServerInfo":
        return cls(
            id=_g(d, "Id", ""),
            name=_g(d, "ServerName", ""),
            version=_g(d, "Version", ""),
            product=_g(d, "ProductName", ""),
            raw=d,
        )


@dataclass
class User:
    id: str
    name: str
    has_password: bool = True
    primary_image_tag: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: dict) -> "User":
        return cls(
            id=_g(d, "Id", ""),
            name=_g(d, "Name", ""),
            has_password=bool(_g(d, "HasPassword", True)),
            primary_image_tag=d.get("PrimaryImageTag"),
            raw=d,
        )


@dataclass
class UserData:
    played: bool = False
    is_favorite: bool = False
    playback_position_ticks: int = 0
    played_percentage: float | None = None
    play_count: int = 0
    unplayed_item_count: int | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def position_seconds(self) -> float:
        return ticks_to_seconds(self.playback_position_ticks)

    @classmethod
    def from_json(cls, d: dict | None) -> "UserData":
        d = d or {}
        return cls(
            played=bool(_g(d, "Played", False)),
            is_favorite=bool(_g(d, "IsFavorite", False)),
            playback_position_ticks=int(_g(d, "PlaybackPositionTicks", 0)),
            played_percentage=d.get("PlayedPercentage"),
            play_count=int(_g(d, "PlayCount", 0)),
            unplayed_item_count=d.get("UnplayedItemCount"),
            raw=d,
        )


@dataclass
class MediaStream:
    index: int
    type: str                    # Video | Audio | Subtitle | Attachment | ...
    codec: str = ""
    language: str = ""
    title: str = ""
    display_title: str = ""
    channels: int | None = None
    is_default: bool = False
    is_forced: bool = False
    is_external: bool = False
    delivery_url: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: dict) -> "MediaStream":
        return cls(
            index=int(_g(d, "Index", -1)),
            type=_g(d, "Type", ""),
            codec=_g(d, "Codec", ""),
            language=_g(d, "Language", ""),
            title=_g(d, "Title", ""),
            display_title=_g(d, "DisplayTitle", ""),
            channels=d.get("Channels"),
            is_default=bool(_g(d, "IsDefault", False)),
            is_forced=bool(_g(d, "IsForced", False)),
            is_external=bool(_g(d, "IsExternal", False)),
            delivery_url=d.get("DeliveryUrl"),
            raw=d,
        )


@dataclass
class MediaSource:
    id: str
    name: str = ""
    container: str = ""
    size: int | None = None
    bitrate: int | None = None
    streams: list[MediaStream] = field(default_factory=list)
    default_audio_index: int | None = None
    default_subtitle_index: int | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def audio_streams(self) -> list[MediaStream]:
        return [s for s in self.streams if s.type == "Audio"]

    @property
    def subtitle_streams(self) -> list[MediaStream]:
        return [s for s in self.streams if s.type == "Subtitle"]

    @classmethod
    def from_json(cls, d: dict) -> "MediaSource":
        return cls(
            id=_g(d, "Id", ""),
            name=_g(d, "Name", ""),
            container=_g(d, "Container", ""),
            size=d.get("Size"),
            bitrate=d.get("Bitrate"),
            streams=[MediaStream.from_json(s) for s in _g(d, "MediaStreams", [])],
            default_audio_index=d.get("DefaultAudioStreamIndex"),
            default_subtitle_index=d.get("DefaultSubtitleStreamIndex"),
            raw=d,
        )


@dataclass
class Item:
    id: str
    name: str
    type: str                     # Movie | Series | Season | Episode | CollectionFolder | ...
    collection_type: str | None = None
    series_id: str | None = None
    series_name: str | None = None
    season_id: str | None = None
    index_number: int | None = None
    parent_index_number: int | None = None
    production_year: int | None = None
    premiere_date: str | None = None
    overview: str = ""
    genres: list[str] = field(default_factory=list)
    community_rating: float | None = None
    official_rating: str | None = None
    run_time_ticks: int | None = None
    image_tags: dict[str, str] = field(default_factory=dict)
    backdrop_image_tags: list[str] = field(default_factory=list)
    user_data: UserData = field(default_factory=UserData)
    media_sources: list[MediaSource] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def runtime_seconds(self) -> float:
        return ticks_to_seconds(self.run_time_ticks)

    @property
    def episode_label(self) -> str:
        """S01E05 style label, or '' if not an episode."""
        if self.type != "Episode" or self.index_number is None:
            return ""
        season = self.parent_index_number if self.parent_index_number is not None else 0
        return f"S{season:02d}E{self.index_number:02d}"

    @classmethod
    def from_json(cls, d: dict) -> "Item":
        return cls(
            id=_g(d, "Id", ""),
            name=_g(d, "Name", ""),
            type=_g(d, "Type", ""),
            collection_type=d.get("CollectionType"),
            series_id=d.get("SeriesId"),
            series_name=d.get("SeriesName"),
            season_id=d.get("SeasonId"),
            index_number=d.get("IndexNumber"),
            parent_index_number=d.get("ParentIndexNumber"),
            production_year=d.get("ProductionYear"),
            premiere_date=d.get("PremiereDate"),
            overview=_g(d, "Overview", ""),
            genres=list(_g(d, "Genres", [])),
            community_rating=d.get("CommunityRating"),
            official_rating=d.get("OfficialRating"),
            run_time_ticks=d.get("RunTimeTicks"),
            image_tags=dict(_g(d, "ImageTags", {})),
            backdrop_image_tags=list(_g(d, "BackdropImageTags", [])),
            user_data=UserData.from_json(d.get("UserData")),
            media_sources=[MediaSource.from_json(s) for s in _g(d, "MediaSources", [])],
            chapters=list(_g(d, "Chapters", [])),
            raw=d,
        )


@dataclass
class Segment:
    """An intro/outro/recap span, in seconds."""
    type: str          # Intro | Outro | Recap | Preview | Commercial
    start: float
    end: float
    source: str = ""   # media-segments | intro-skipper | chapters

    def to_lua(self) -> dict:
        return {"type": self.type, "start": round(self.start, 3), "end": round(self.end, 3)}
