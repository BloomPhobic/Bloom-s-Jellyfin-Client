"""Playback controller: Play button → mpv window → progress back to Jellyfin.

States: idle → resolving → launching → loading → playing ⇄ paused → idle.

Per item (plan §5.4): fetch streams if needed, pick the source and tracks,
`loadfile` the static URL *paused*, map the chosen Jellyfin streams to mpv
tracks by ff-index when the file is loaded, add external subtitles, then
unpause and report /Sessions/Playing. Progress every 10 s and on
pause/seek/track change; a final Stopped report on end, quit or crash.
One mpv process is reused for the session so fullscreen stays put.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ..api import details, tracks
from ..api.models import Item
from ..api.tracks import OFF, PlayRequest
from ..session import Session
from ..ui.workers import run_async
from . import launcher
from .session import PlaybackSession, Resolved, jellyfin_index_for, mpv_track_for

log = logging.getLogger(__name__)

PROGRESS_MS = 10_000
OBSERVED = ("time-pos", "duration", "pause", "aid", "sid", "track-list", "eof-reached")


class PlaybackController(QObject):
    state_changed = Signal(str)
    error = Signal(str)                     # user-facing message
    started = Signal(object)                # Item
    finished = Signal(object)               # Item (stopped, ended or crashed)

    def __init__(self, session: Session, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session
        self.state = "idle"
        self.proc: QProcess | None = None
        self.ipc = None                     # MpvIpc, created on first launch
        self.play: PlaybackSession | None = None
        self._pending: Resolved | None = None
        self._track_list: list[dict] = []
        self._ignore_end = 0                # end-file events caused by our own loadfile replace
        self._last_progress = 0.0
        self._seeking = False
        self._file_open = False
        self._launches = 0
        self._gen = 0

        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(PROGRESS_MS)
        self.progress_timer.timeout.connect(lambda: self._report("timeupdate"))

    # ================================================================== entry point
    def request(self, req: PlayRequest) -> None:
        ok, why = self.session.playback_allowed()
        if not ok:
            self.error.emit(why)
            return
        if launcher.find_mpv(self.session.settings.get("mpv.path", "mpv")) is None:
            self.error.emit("mpv wasn't found. Install it (sudo pacman -S mpv) or set its path in settings.")
            return
        self._gen += 1
        gen = self._gen
        self._set_state("resolving")
        settings, client = self.session.settings, self.session.client

        def resolve() -> Resolved:
            item = req.item
            if not item.media_sources or not all(s.streams for s in item.media_sources):
                item = details.get_item(client, item.id)
                details.ensure_streams(client, item)
            if not item.media_sources:
                raise RuntimeError("This item has no playable file.")
            src = next((s for s in item.media_sources if s.id == req.source_id), item.media_sources[0])
            audio, sub = req.audio_index, req.subtitle_index
            if audio is None or sub is None:
                langs = settings.get("languages", {}) or {}
                remembered = settings.get(f"series_tracks.{item.series_id}") if item.series_id else None
                choice = tracks.default_choice(item, list(langs.get("audio") or []),
                                               list(langs.get("subtitle") or []), remembered, src.id)
                audio = choice.audio_index if audio is None else audio
                sub = choice.subtitle_index if sub is None else sub
            return Resolved(item, src, audio, sub, req.start_seconds)

        run_async(resolve, lambda r: gen == self._gen and self._resolved(r),
                  lambda e: gen == self._gen and self._fail(f"Couldn't prepare playback: {e}"))

    def _resolved(self, r: Resolved) -> None:
        if self.play is not None and self.play.started and not self.play.stop_requested:
            self._stop_current(ended=False)            # switching items: close the old session
        threshold = float(self.session.settings.get("playback.played_threshold_pct", 90) or 90)
        self.play = PlaybackSession(self.session.client, r, threshold)
        self._pending = r
        if self.proc is not None and self.ipc is not None and self.ipc.is_connected:
            self._load()
        else:
            self._launch()

    # ================================================================== mpv process
    def _launch(self) -> None:
        from .ipc import MpvIpc

        self._set_state("launching")
        self._shutdown_process()
        self._launches += 1
        sock = launcher.socket_path(str(self._launches))
        mpv = launcher.find_mpv(self.session.settings.get("mpv.path", "mpv")) or "mpv"
        args = launcher.build_args(self.session.settings.get("mpv", {}) or {}, sock, script=None)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._drain_output)
        self.proc.errorOccurred.connect(self._proc_error)
        self.proc.finished.connect(self._proc_finished)
        log.info("starting %s %s", mpv, " ".join(args))
        self.proc.start(mpv, args)

        self.ipc = MpvIpc(self)
        self.ipc.connected.connect(self._ipc_ready)
        self.ipc.connect_failed.connect(lambda msg: self._fail(msg))
        self.ipc.disconnected.connect(self._ipc_lost)
        self.ipc.property_changed.connect(self._prop)
        self.ipc.event.connect(self._event)
        self.ipc.connect_to(sock)

    def _drain_output(self) -> None:
        if self.proc is not None:
            out = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace").strip()
            if out:
                log.debug("mpv: %s", out[-2000:])

    def _proc_error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self._fail("mpv failed to start. Check the mpv path in settings.")

    def _proc_finished(self, *_a) -> None:
        self._ipc_lost()
        self.proc = None

    def _shutdown_process(self) -> None:
        if self.ipc is not None:
            try:
                self.ipc.disconnected.disconnect(self._ipc_lost)
            except (RuntimeError, TypeError):
                pass
            self.ipc.close()
            self.ipc.deleteLater()
            self.ipc = None
        if self.proc is not None:
            try:
                self.proc.finished.disconnect(self._proc_finished)
            except (RuntimeError, TypeError):
                pass
            self.proc.terminate()
            if not self.proc.waitForFinished(1500):
                self.proc.kill()
            self.proc.deleteLater()
            self.proc = None

    def _ipc_ready(self) -> None:
        assert self.ipc is not None
        self.ipc.observe(*OBSERVED)
        self._load()

    # ================================================================== per item
    def _load(self) -> None:
        r, ps, ipc = self._pending, self.play, self.ipc
        if r is None or ps is None or ipc is None:
            return
        self._set_state("loading")
        self._track_list = []
        c = self.session.client
        header = c.auth_header_value()
        ipc.set_property("http-header-fields", [f"Authorization: {header}",
                                                 f"X-Emby-Authorization: {header}"])
        it = r.item
        title = f"{it.series_name} — {it.episode_label} · {it.name}" if it.type == "Episode" and it.series_name \
            else it.name
        ipc.set_property("force-media-title", title)
        ipc.set_property("start", f"{max(0.0, r.start_seconds):.3f}" if r.start_seconds > 0 else "none")
        ipc.set_property("pause", True)            # pick tracks before anything is heard
        ass = self.session.settings.get("subtitles.ass_override", "scale") or "scale"
        ipc.set_property("sub-ass-override", ass)
        if self._file_open:               # replacing a file: mpv will report its end; not ours to handle
            self._ignore_end += 1
        self._file_open = True
        ipc.command("loadfile", ps.url, "replace",
                    reply=lambda err, _d: err and self._fail(f"mpv couldn't open the file: {err}"))

    def _file_loaded(self) -> None:
        ipc, ps = self.ipc, self.play
        if ipc is None or ps is None:
            return

        def with_tracks(err, data) -> None:
            self._track_list = data if isinstance(data, list) else []
            # external subtitles first, so the chosen one can be selected
            for idx, url in ps.external_urls.items():
                st = next((s for s in ps.source.subtitle_streams if s.index == idx), None)
                flag = "select" if idx == ps.subtitle_index else "auto"
                title = tracks.subtitle_label(st) if st else "External"
                lang = (st.language if st else "") or ""
                ipc.command("sub-add", url, flag, title, lang)
            aid = mpv_track_for(self._track_list, "audio", ps.audio_index)
            if aid is not None:
                ipc.set_property("aid", aid)
            if ps.subtitle_index == OFF:
                ipc.set_property("sid", "no")
            elif ps.subtitle_index not in ps.external_urls:
                sid = mpv_track_for(self._track_list, "sub", ps.subtitle_index)
                if sid is not None:
                    ipc.set_property("sid", sid)
            ipc.set_property("pause", False)
            self._set_state("playing")
            self.started.emit(ps.item)
            ps.started = True
            run_async(ps.report_start)
            self.progress_timer.start()

        ipc.get_property("track-list", with_tracks)

    # ================================================================== events
    def _prop(self, name: str, value) -> None:
        ps = self.play
        if ps is None:
            return
        if name == "time-pos" and isinstance(value, (int, float)):
            ps.position = float(value)
        elif name == "duration" and isinstance(value, (int, float)) and value > 0:
            ps.duration = float(value)
        elif name == "track-list" and isinstance(value, list):
            self._track_list = value
        elif name == "pause" and isinstance(value, bool) and ps.started and not ps.stop_requested:
            if value != ps.paused:
                ps.paused = value
                self._set_state("paused" if value else "playing")
                self._report("pause" if value else "unpause")
        elif name in ("aid", "sid") and ps.started and not ps.stop_requested:
            kind = "audio" if name == "aid" else "sub"
            idx = jellyfin_index_for(self._track_list, kind, value, ps.external_urls)
            if kind == "audio" and idx is not None and idx != ps.audio_index:
                ps.audio_index = idx
                self._report("AudioTrackChange")
            elif kind == "sub" and idx is not None and idx != ps.subtitle_index:
                ps.subtitle_index = idx
                self._report("SubtitleTrackChange")

    def _event(self, name: str, msg: dict) -> None:
        if name == "file-loaded":
            self._file_loaded()
        elif name == "seek":
            self._seeking = True
        elif name == "playback-restart" and self._seeking:
            self._seeking = False
            self._report("timeupdate")
        elif name == "end-file":
            if self._ignore_end > 0:
                self._ignore_end -= 1
                return
            self._file_open = False
            reason = msg.get("reason", "")
            if reason == "error":
                detail = msg.get("file_error") or "unknown error"
                self._fail(f"mpv couldn't play this file ({detail}). Direct play only — nothing was transcoded.")
                return
            self._finish(ended=(reason == "eof"), quit_player=(reason == "eof"))
        elif name == "shutdown":
            self._ipc_lost()

    def _ipc_lost(self) -> None:
        """mpv closed (user quit, crash, window closed): report the last position."""
        self.progress_timer.stop()
        self._file_open = False
        if self.play is not None and self.play.started and not self.play.stop_requested:
            self._finish(ended=False, quit_player=False)
        self._set_state("idle")

    # ================================================================== finishing
    def _report(self, event: str) -> None:
        ps = self.play
        if ps is None or not ps.started or ps.stop_requested:
            return
        now = time.monotonic()
        if event == "timeupdate" and now - self._last_progress < 2:
            return
        self._last_progress = now
        run_async(lambda: ps.report_progress(event))

    def _stop_current(self, ended: bool) -> None:
        ps = self.play
        if ps is None or not ps.started or ps.stop_requested:
            return
        ps.stop_requested = True             # synchronously, so a second end/quit/crash signal can't double-report
        self.progress_timer.stop()
        item = ps.item
        run_async(lambda: ps.report_stop(ended), lambda _ok: self.finished.emit(item),
                  lambda _e: self.finished.emit(item))

    def _finish(self, ended: bool, quit_player: bool) -> None:
        self._stop_current(ended)
        if quit_player and self.ipc is not None and self.ipc.is_connected:
            self.ipc.command("quit")          # phase 8 replaces this with "up next"
        self._set_state("idle")

    def _fail(self, message: str) -> None:
        log.warning("playback: %s", message)
        self.error.emit(message)
        self._stop_current(ended=False)
        if self.ipc is not None and self.ipc.is_connected:
            self.ipc.command("quit")
        self._set_state("idle")

    def _set_state(self, s: str) -> None:
        if s != self.state:
            self.state = s
            self.state_changed.emit(s)

    # ================================================================== app exit
    def shutdown(self) -> None:
        """Called when the app quits: final report (blocking, short) and close mpv."""
        ps = self.play
        if ps is not None and ps.started and not ps.stop_requested:
            ps.stop_requested = True
            try:
                ps.report_stop(ended=False)
            except Exception:
                pass
        self._shutdown_process()

    @property
    def now_playing(self) -> Item | None:
        return self.play.item if self.play is not None and not self.play.stop_requested else None
