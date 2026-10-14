"""One playback of one item: URLs, track mapping and Jellyfin progress reports.

No Qt here. The controller drives it from mpv's IPC events and calls the
report methods from worker threads. Reports that fail (network blip) are
queued and re-sent before the next one, so the server still ends up with
the final position.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field

from ..api import library
from ..api.client import JellyfinClient, JellyfinError
from ..api.models import Item, MediaSource, MediaStream, seconds_to_ticks
from ..api.tracks import OFF

log = logging.getLogger(__name__)

SUB_EXT = {"subrip": "srt", "srt": "srt", "ass": "ass", "ssa": "ssa", "webvtt": "vtt", "vtt": "vtt",
           "pgssub": "sup", "pgs": "sup", "dvdsub": "sub", "mov_text": "srt"}


@dataclass
class Resolved:
    item: Item
    source: MediaSource
    audio_index: int | None
    subtitle_index: int            # OFF = none
    start_seconds: float = 0.0
    seasons: list = field(default_factory=list)     # for the in-player episode browser
    episodes: list = field(default_factory=list)


# --------------------------------------------------------------------------- urls

def stream_url(client: JellyfinClient, item_id: str, source_id: str) -> str:
    """The one and only playback URL shape: the original file, byte for byte."""
    return client.url(f"/Videos/{item_id}/stream", {"static": "true", "MediaSourceId": source_id})


def subtitle_url(client: JellyfinClient, item_id: str, source_id: str, s: MediaStream) -> str:
    if s.delivery_url:
        return client.url(s.delivery_url) if s.delivery_url.startswith("/") else s.delivery_url
    ext = SUB_EXT.get((s.codec or "").lower(), "srt")
    return client.url(f"/Videos/{item_id}/{source_id}/Subtitles/{s.index}/Stream.{ext}")


# --------------------------------------------------------------------------- track mapping
# Jellyfin's MediaStream.Index is ffprobe's stream index, which mpv exposes as
# "ff-index" on embedded tracks. External subtitles are matched by their URL.

def _kind(t: dict) -> str:
    return {"audio": "audio", "sub": "sub", "video": "video"}.get(t.get("type", ""), "")


def mpv_track_for(track_list: list[dict], kind: str, jf_index: int | None,
                  external_urls: dict[int, str] | None = None) -> int | None:
    if jf_index is None or jf_index == OFF:
        return None
    for t in track_list:
        if _kind(t) == kind and not t.get("external") and t.get("ff-index") == jf_index:
            return t.get("id")
    url = (external_urls or {}).get(jf_index)
    if url:
        for t in track_list:
            if _kind(t) == kind and t.get("external") and t.get("external-filename") == url:
                return t.get("id")
    return None


def jellyfin_index_for(track_list: list[dict], kind: str, mpv_id, external_urls: dict[int, str] | None = None) -> int | None:
    if mpv_id in (None, False, "no"):
        return OFF if kind == "sub" else None
    t = next((t for t in track_list if _kind(t) == kind and t.get("id") == mpv_id), None)
    if t is None:
        return None
    if not t.get("external"):
        return t.get("ff-index")
    for idx, url in (external_urls or {}).items():
        if t.get("external-filename") == url:
            return idx
    return None


# --------------------------------------------------------------------------- session

class PlaybackSession:
    def __init__(self, client: JellyfinClient, r: Resolved, played_threshold_pct: float = 90):
        self.client = client
        self.item = r.item
        self.source = r.source
        self.audio_index = r.audio_index
        self.subtitle_index = r.subtitle_index
        self.start_seconds = r.start_seconds
        self.play_session_id = uuid.uuid4().hex
        self.threshold = played_threshold_pct
        self.position = r.start_seconds
        self.duration = r.item.runtime_seconds or 0.0
        self.paused = False
        self.started = False
        self.stopped = False
        self.stop_requested = False      # set on the UI thread before the Stopped report goes out
        self.external_urls: dict[int, str] = {
            s.index: subtitle_url(client, self.item.id, self.source.id, s)
            for s in self.source.subtitle_streams if s.is_external
        }
        self._lock = threading.Lock()
        self._queue: list[tuple[str, dict]] = []

    # -- urls
    @property
    def url(self) -> str:
        return stream_url(self.client, self.item.id, self.source.id)

    # -- payloads
    def _base(self) -> dict:
        return {
            "ItemId": self.item.id,
            "MediaSourceId": self.source.id,
            "PlaySessionId": self.play_session_id,
            "PositionTicks": seconds_to_ticks(self.position),
            "PlayMethod": "DirectPlay",
        }

    def playing_payload(self, event: str | None = None) -> dict:
        p = {**self._base(), "IsPaused": self.paused, "IsMuted": False, "CanSeek": True,
             "AudioStreamIndex": self.audio_index,
             "SubtitleStreamIndex": self.subtitle_index if self.subtitle_index != OFF else -1,
             "RepeatMode": "RepeatNone"}
        if event:
            p["EventName"] = event
        return p

    def stopped_payload(self) -> dict:
        return {**self._base(), "Failed": False}

    # -- reports (blocking; call from workers)
    def _post(self, path: str | None, body: dict | None) -> bool:
        with self._lock:
            pending = self._queue[:]
            self._queue.clear()
        batch = pending + ([(path, body)] if path and body is not None else [])
        for qpath, qbody in batch:
            try:
                self.client.request("POST", qpath, None, qbody, timeout=8, retries=0)
            except JellyfinError as e:
                log.info("report %s failed (%s); queued", qpath, e)
                with self._lock:
                    # keep order; drop stale progress if a newer one is queued anyway
                    self._queue.append((qpath, qbody))
                    self._queue = self._queue[-20:]
        with self._lock:
            return not self._queue

    def report_start(self) -> bool:
        self.started = True
        return self._post("/Sessions/Playing", self.playing_payload())

    def report_progress(self, event: str = "timeupdate") -> bool:
        if not self.started or self.stopped:
            return False
        return self._post("/Sessions/Playing/Progress", self.playing_payload(event))

    def report_stop(self, ended: bool = False) -> bool:
        """Final report. `ended` = played to the end (position snaps to the runtime)."""
        if self.stopped:
            return True
        self.stopped = True
        if ended and self.duration:
            self.position = self.duration
        ok = self._post("/Sessions/Playing/Stopped", self.stopped_payload())
        if self.should_mark_played():
            try:
                library.set_played(self.client, self.item.id, True)
            except JellyfinError as e:
                log.info("explicit mark-played failed: %s", e)
        return ok

    def should_mark_played(self) -> bool:
        """Belt and braces: Jellyfin marks played on Stopped past its own threshold,
        but we also mark it once past ours, in case the server's differs."""
        return bool(self.duration) and self.position >= self.duration * self.threshold / 100

    def flush(self) -> bool:
        """Re-send anything that failed earlier (call when the connection is back)."""
        return self._post(None, None)

    @property
    def queued(self) -> int:
        with self._lock:
            return len(self._queue)
