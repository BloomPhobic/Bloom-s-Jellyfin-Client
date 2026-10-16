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

import json
import logging
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ..api import details, library, tracks
from ..api.models import Item
from ..api.tracks import OFF, PlayRequest
from ..session import Session
from ..ui.workers import run_async
from . import launcher, overlay_info, segments
from .session import PlaybackSession, Resolved, jellyfin_index_for, mpv_track_for

log = logging.getLogger(__name__)

PROGRESS_MS = 10_000
OBSERVED = ("time-pos", "duration", "pause", "aid", "sid", "track-list", "eof-reached")
STYLE_PROPS = ("sub-scale", "sub-bold", "sub-font", "sub-border-size", "sub-pos", "sub-ass-override")


class PlaybackController(QObject):
    state_changed = Signal(str)
    error = Signal(str)                     # user-facing message
    started = Signal(object)                # Item
    finished = Signal(object)               # Item (stopped, ended or crashed)
    library_changed = Signal()              # e.g. favourite toggled from the player

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

        self._seasons: list[Item] = []
        self._episodes: list[Item] = []
        self._segments: list = []
        self._chain = overlay_info.AutoplayChain(int(session.settings.get("playback.autoplay_limit", 5) or 5))
        self._no_autoplay_for: str | None = None     # "Cancel" on the Up Next card
        self._advancing = False
        self._file_started_at = 0.0
        self._quiet_until = 0.0                      # our own seeks/unpause aren't "interaction"
        self._style_pending: dict | None = None
        self._style_timer = QTimer(self)
        self._style_timer.setSingleShot(True)
        self._style_timer.setInterval(800)
        self._style_timer.timeout.connect(self._flush_style)

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
            item = details.get_item(client, req.item.id)      # fresh: chapters, resume point, streams
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
            r = Resolved(item, src, audio, sub, req.start_seconds)
            try:
                prefer = settings.get("playback.segment_source")
                r.segments = segments.fetch_segments(client, item, prefer=prefer)
                if r.segments and r.segments[0].source != prefer:
                    settings.set("playback.segment_source", r.segments[0].source)
            except Exception as e:
                log.info("no skip segments: %s", e)
            if item.type == "Episode" and item.series_id:
                try:
                    r.seasons = details.seasons(client, item.series_id)
                    r.episodes = details.episodes(client, item.series_id)
                except Exception as e:          # the overlay just shows less
                    log.info("couldn't load episode list: %s", e)
            return r

        def failed(e: Exception) -> None:
            if gen == self._gen:
                self._advancing = False
                self._fail(f"Couldn't prepare playback: {e}")

        run_async(resolve, lambda r: gen == self._gen and self._resolved(r), failed)

    def _resolved(self, r: Resolved) -> None:
        self._advancing = False
        if self.play is not None and self.play.started and not self.play.stop_requested:
            self._stop_current(ended=False)            # switching items: close the old session
        threshold = float(self.session.settings.get("playback.played_threshold_pct", 90) or 90)
        self.play = PlaybackSession(self.session.client, r, threshold)
        self._pending = r
        self._seasons = list(getattr(r, "seasons", []) or [])
        self._episodes = list(getattr(r, "episodes", []) or [])
        self._segments = list(getattr(r, "segments", []) or [])
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
        mpv_settings = self.session.settings.get("mpv", {}) or {}
        script = overlay_info.script_path() if mpv_settings.get("use_bloom_ui", True) else None
        args = launcher.build_args(mpv_settings, sock, script=script)
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
        self.ipc.observe(*OBSERVED, *STYLE_PROPS)
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
        ipc.set_property("sub-delay", 0)           # a subtitle offset fixes one file, not the next
        ass = self.session.settings.get("subtitles.ass_override", "scale") or "scale"
        ipc.set_property("sub-ass-override", ass)
        # Subtitle style you changed in the player before: re-apply it (only what you changed,
        # so everything else still comes from your mpv.conf).
        for prop, value in (self.session.settings.get("subtitles.saved", {}) or {}).items():
            if prop in STYLE_PROPS:
                ipc.set_property(prop, value)
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
            self._file_started_at = time.monotonic()
            self._quiet_until = time.monotonic() + 3.0
            if self._no_autoplay_for and self._no_autoplay_for != ps.item.id:
                self._no_autoplay_for = None
            run_async(ps.report_start)
            self.progress_timer.start()
            self._send_info()
            ipc.command("script-message-to", "bloom", "bloom-show")

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
                self._interaction()
        elif name in ("aid", "sid") and ps.started and not ps.stop_requested:
            kind = "audio" if name == "aid" else "sub"
            idx = jellyfin_index_for(self._track_list, kind, value, ps.external_urls)
            if kind == "audio" and idx is not None and idx != ps.audio_index:
                ps.audio_index = idx
                self._report("AudioTrackChange")
                self._remember_tracks()
            elif kind == "sub" and idx is not None and idx != ps.subtitle_index:
                ps.subtitle_index = idx
                self._report("SubtitleTrackChange")
                self._remember_tracks()
        elif name in STYLE_PROPS and ps.started and not ps.stop_requested and value is not None:
            self._remember_style(name, value)

    def _event(self, name: str, msg: dict) -> None:
        if name == "client-message":
            args = msg.get("args") or []
            if args:
                self._overlay_message(str(args[0]), [str(a) for a in args[1:]])
            return
        if name == "file-loaded":
            self._file_loaded()
        elif name == "seek":
            self._seeking = True
            self._interaction()
        elif name == "playback-restart" and self._seeking:
            self._seeking = False
            self._report("timeupdate")
        elif name == "end-file":
            if self._ignore_end > 0:
                self._ignore_end -= 1
                return
            self._file_open = False
            if self._advancing:
                return
            reason = msg.get("reason", "")
            if reason == "error":
                detail = msg.get("file_error") or "unknown error"
                self._fail(f"mpv couldn't play this file ({detail}). Direct play only — nothing was transcoded.")
                return
            if reason == "eof" and self._autoplay_next():
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

    # ================================================================== overlay
    def _send_info(self) -> None:
        ps, ipc = self.play, self.ipc
        if ps is None or ipc is None:
            return
        st = self.session.settings
        self._chain.limit = max(1, int(st.get("playback.autoplay_limit", 5) or 5))
        autoplay = bool(st.get("playback.autoplay", True)) and self._no_autoplay_for != ps.item.id
        info = overlay_info.build_info(
            ps.item, ps.source, self._seasons, self._episodes, autoplay,
            skip_mode=st.get("playback.skip_mode", "button") or "button",
            up_next_seconds=int(st.get("playback.up_next_seconds", 20) or 20),
            still_watching=self._chain.should_ask)
        ipc.command("script-message-to", "bloom", "bloom-info", json.dumps(info))
        ipc.command("script-message-to", "bloom", "bloom-segments",
                    json.dumps(overlay_info.segments_for_lua(self._segments)))

    def _episode(self, item_id: str) -> Item | None:
        return next((e for e in self._episodes if e.id == item_id), None)

    def _play_episode(self, ep: Item, start_over: bool = False) -> None:
        rewind = float(self.session.settings.get("playback.resume_rewind_seconds", 5) or 0)
        start = 0.0 if start_over else tracks.resume_seconds(ep, rewind)
        self.request(PlayRequest(ep, start_seconds=start))

    def _interaction(self) -> None:
        if time.monotonic() >= self._quiet_until:
            was_asking = self._chain.should_ask
            self._chain.interaction()
            if was_asking and not self._chain.should_ask:
                self._send_info()          # the overlay can go back to a normal countdown

    def _overlay_message(self, name: str, args: list[str]) -> None:
        ps = self.play
        if name == "bloom-autoskip":                    # the overlay is about to seek past a segment
            self._quiet_until = time.monotonic() + 2.0
            return
        if name == "bloom-play-next" and ps is not None:
            auto = bool(args) and args[0] == "auto"
            self._advance(auto=auto)
            return
        if name == "bloom-cancel-next" and ps is not None:
            self._no_autoplay_for = ps.item.id
            self._chain.interaction()
            return
        self._chain.interaction()                       # any click in the player
        if name in ("bloom-next", "bloom-prev") and ps is not None:
            prev, nxt = overlay_info.neighbours(self._episodes, ps.item.id)
            target = nxt if name == "bloom-next" else prev
            if target is not None:
                self._play_episode(target)
        elif name == "bloom-play-item" and args:
            ep = self._episode(args[0])
            if ep is not None:
                self._play_episode(ep)
        elif name == "bloom-favorite" and args and ps is not None:
            want = args[0] == "yes"
            item, client = ps.item, self.session.client

            def done(_r) -> None:
                item.user_data.is_favorite = want
                self.library_changed.emit()

            run_async(lambda: library.set_favorite(client, item.id, want), done,
                      lambda e: (self.error.emit(f"Couldn't update favourite: {e}"), self._send_info()))
        elif name == "bloom-autoplay" and args:
            self.session.settings.set("playback.autoplay", args[0] == "yes")
        elif name == "bloom-version" and args and ps is not None:
            self.request(PlayRequest(ps.item, source_id=args[0], start_seconds=max(0.0, ps.position)))

    def _advance(self, auto: bool) -> None:
        """Up Next: 'Play now' / countdown / 'Continue' (or the credits being skipped)."""
        ps = self.play
        if ps is None:
            return
        _prev, nxt = overlay_info.neighbours(self._episodes, ps.item.id)
        if nxt is None:
            return
        if auto:
            self._chain.auto_started()
        else:
            self._chain.interaction()
        self._advancing = True                          # an eof arriving meanwhile isn't a second "next"
        self._stop_current(ended=True)                  # reaching Up Next counts as finished
        self._play_episode(nxt)

    def _autoplay_next(self) -> bool:
        """At the very end of an episode. False → stop (and close the player)."""
        ps = self.play
        if ps is None or ps.item.type != "Episode" or not self.session.settings.get("playback.autoplay", True):
            return False
        if self._no_autoplay_for == ps.item.id:
            return False
        if self._chain.should_ask:      # the card asked "Still watching?" and nobody answered
            log.info("autoplay paused: %d episodes in a row without interaction", self._chain.count)
            self._chain.interaction()
            return False
        _prev, nxt = overlay_info.neighbours(self._episodes, ps.item.id)
        if nxt is None:
            return False
        self._chain.auto_started()
        self._stop_current(ended=True)
        self._file_open = False          # mpv is idle after eof; nothing to replace
        self._play_episode(nxt)
        return True

    def _remember_tracks(self) -> None:
        ps = self.play
        if ps is None or not ps.item.series_id:
            return
        self.session.settings.set(f"series_tracks.{ps.item.series_id}",
                                  tracks.memory_for(ps.source, ps.audio_index, ps.subtitle_index))

    def _remember_style(self, prop: str, value) -> None:
        saved = dict(self.session.settings.get("subtitles.saved", {}) or {})
        if saved.get(prop) == value:
            return
        saved[prop] = value
        self._style_pending = saved
        self._style_timer.start()          # debounce: menus fire several changes quickly

    def _flush_style(self) -> None:
        if self._style_pending is not None:
            self.session.settings.set("subtitles.saved", self._style_pending)
            self._style_pending = None

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
