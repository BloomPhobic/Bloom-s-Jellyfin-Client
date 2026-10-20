"""Detail pages for movies, series and episodes.

Header: backdrop, poster (or episode still), title, meta, genres, tagline,
overview, people; Play/Resume, Start over, Watched, Favourite. Movies and
episodes get Version/Audio/Subtitle pickers (defaults: what you chose last
time for this series, then your preferred languages, then the file's flags).
Series get a season dropdown and the episode list.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ..api import details, images, tracks
from ..api.models import Item, MediaSource
from ..api.tracks import OFF, PlayRequest, TrackChoice
from ..display import has_resume, is_watched, overview_html, year_of
from ..session import Session
from .actions import ItemActions
from .nav import Page
from .widgets import smooth
from .widgets.episodes import EpisodeListView, runtime_text
from .widgets.image_loader import ImageLoader
from .widgets.media import BackdropHeader, ImageBox
from .workers import run_async

log = logging.getLogger(__name__)

PLAYABLE = ("Movie", "Episode", "Video", "MusicVideo")
ACTION_H = 46
ACTION_MIN_W = 168


def season_label(s: Item) -> str:
    """'Season 1  ·  12 episodes  ·  3 unwatched' (or '· all watched')."""
    total = s.child_count
    n = s.user_data.unplayed_item_count or 0
    bits = [s.name]
    if total:
        bits.append(f"{total} episode{'s' if total != 1 else ''}")
    if n:
        bits.append(f"{n} unwatched")
    elif total:
        bits.append("all watched ✓")
    return "  ·  ".join(bits)


def clock(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


@dataclass
class Bundle:
    item: Item
    seasons: list[Item] = field(default_factory=list)
    season: Item | None = None
    episodes: list[Item] = field(default_factory=list)
    next_up: Item | None = None


def _button(text: str, role: str | None = None, large: bool = False, checkable: bool = False) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    if large:
        b.setProperty("size", "large")
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class DetailPage(Page):
    open_item = Signal(object)          # Item
    play = Signal(object)               # PlayRequest
    open_genre = Signal(object, str)    # item, genre

    def __init__(self, session: Session, loader: ImageLoader, actions: ItemActions, item: Item,
                 season_id: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.session = session
        self.loader = loader
        self.actions = actions
        self.item = item
        self.title = item.name
        self.nav_key = ""
        self._want_season = season_id
        self._bundle: Bundle | None = None
        self.playable: Item | None = None
        self.choice: TrackChoice | None = None
        self._gen = 0
        self._ep_gen = 0
        self._stale = False

        self._build()
        self._show_partial()
        self.load()

        actions.item_changed.connect(self._item_changed)
        actions.data_changed.connect(self._server_changed)

    # ================================================================== layout
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self._smooth = smooth.install(self.scroll)

        content = QWidget()
        content.setObjectName("root")
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.scroll.setWidget(content)

        # ---- header
        self.header = BackdropHeader(self.loader)
        hl = QHBoxLayout(self.header)
        hl.addStrut(440)           # at least 440 tall, but always tall enough for its content
        hl.setContentsMargins(40, 40, 40, 34)
        hl.setSpacing(34)
        episode = self.item.type == "Episode"
        self.poster = ImageBox(self.loader, QSize(352, 198) if episode else QSize(220, 330))
        hl.addWidget(self.poster, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(10)
        self.context_btn = _button("", "link")
        self.context_btn.clicked.connect(self._open_series)
        self.context_btn.hide()
        info.addWidget(self.context_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.title_lbl = QLabel(self.item.name)
        self.title_lbl.setProperty("role", "detailtitle")
        self.title_lbl.setWordWrap(True)
        self.meta_lbl = QLabel("")
        self.meta_lbl.setProperty("role", "muted")
        self.genres_box = QHBoxLayout()
        self.genres_box.setSpacing(6)
        self.tagline_lbl = QLabel("")
        self.tagline_lbl.setProperty("role", "tagline")
        self.tagline_lbl.setWordWrap(True)
        self.overview_lbl = QLabel("")
        self.overview_lbl.setWordWrap(True)
        self.overview_lbl.setMaximumWidth(860)
        self.overview_lbl.setProperty("role", "overview")
        self.overview_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.overview_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.people_lbl = QLabel("")
        self.people_lbl.setProperty("role", "muted")
        self.people_lbl.setWordWrap(True)
        self.people_lbl.setMaximumWidth(860)
        info.addWidget(self.title_lbl)
        info.addWidget(self.meta_lbl)
        info.addLayout(self.genres_box)
        info.addWidget(self.tagline_lbl)
        info.addWidget(self.overview_lbl)
        info.addWidget(self.people_lbl)
        info.addSpacing(8)

        acts = QHBoxLayout()
        acts.setSpacing(10)
        self.play_btn = _button("▶  Play", "primary", large=True)
        self.play_btn.clicked.connect(lambda: self._play(restart=False))
        self.restart_btn = _button("↺  Start over", large=True)
        self.restart_btn.clicked.connect(lambda: self._play(restart=True))
        self.watched_btn = _button("✓  Watched", "toggle", checkable=True)
        self.watched_btn.clicked.connect(self._toggle_watched)
        self.watched_btn.installEventFilter(self)          # hover shows what a click will do
        self.fav_btn = _button("♡  Favourite", "toggle", checkable=True)
        self.fav_btn.clicked.connect(lambda: self.actions.toggle_favorite(self.item))
        for b in (self.play_btn, self.restart_btn, self.watched_btn, self.fav_btn):
            b.setFixedHeight(ACTION_H)
            acts.addWidget(b)
        # Toggles keep one width whatever their label says, so clicking never resizes them.
        for b, texts in ((self.watched_btn, ("✓  Mark watched", "✓  Watched", "↺  Mark unwatched")),
                         (self.fav_btn, ("♡  Favourite", "♥  Favourite"))):
            fm = b.fontMetrics()
            b.setFixedWidth(max(ACTION_MIN_W, max(fm.horizontalAdvance(t) for t in texts) + 48))
        self.play_btn.setMinimumWidth(ACTION_MIN_W)
        self.restart_btn.setMinimumWidth(ACTION_MIN_W)
        acts.addStretch()
        info.addLayout(acts)

        # ---- track pickers (movies / episodes)
        self.pickers = QWidget()
        grid = QGridLayout(self.pickers)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.version_lbl = QLabel("Version")
        self.version_cb = QComboBox()
        self.audio_cb = QComboBox()
        self.subs_cb = QComboBox()
        for cb in (self.version_cb, self.audio_cb, self.subs_cb):
            cb.setMinimumWidth(240)
            cb.setMaximumWidth(460)
            cb.setMinimumHeight(38)
            cb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for col_i, (lbl, cb) in enumerate(((self.version_lbl, self.version_cb),
                                            (QLabel("Audio"), self.audio_cb),
                                            (QLabel("Subtitles"), self.subs_cb))):
            lbl.setProperty("role", "fieldlabel")
            grid.addWidget(lbl, 0, col_i)
            grid.addWidget(cb, 1, col_i)
        grid.setColumnStretch(3, 1)
        self.tracks_note = QLabel("")
        self.tracks_note.setProperty("role", "muted")
        self.tracks_note.setWordWrap(True)
        grid.addWidget(self.tracks_note, 2, 0, 1, 4)
        self.version_cb.currentIndexChanged.connect(self._version_changed)
        self.audio_cb.currentIndexChanged.connect(self._tracks_changed)
        self.subs_cb.currentIndexChanged.connect(self._tracks_changed)
        self.pickers.hide()
        info.addWidget(self.pickers)
        info.addStretch()
        hl.addLayout(info, 1)
        col.addWidget(self.header)

        # ---- seasons + episodes (series)
        self.series_box = QWidget()
        sv = QVBoxLayout(self.series_box)
        sv.setContentsMargins(36, 6, 36, 40)
        sv.setSpacing(12)
        bar = QHBoxLayout()
        eps_title = QLabel("Episodes")
        eps_title.setProperty("role", "rowtitle")
        self.season_cb = QComboBox()
        self.season_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.season_cb.setMinimumHeight(38)
        self.season_cb.currentIndexChanged.connect(self._season_changed)
        self.ep_count = QLabel("")
        self.ep_count.setProperty("role", "muted")
        bar.addWidget(eps_title)
        bar.addSpacing(12)
        bar.addWidget(self.season_cb)
        bar.addSpacing(8)
        bar.addWidget(self.ep_count)
        bar.addStretch()
        sv.addLayout(bar)
        self.episodes = EpisodeListView(self.loader)
        self.episodes.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.episodes.item_clicked.connect(self.open_item)
        self.episodes.toggle_played.connect(self.actions.toggle_played)
        self.episodes.play_clicked.connect(lambda ep: self._emit_play(ep, restart=False))
        self.episodes.item_menu.connect(lambda it, pos: self.actions.context_menu(it, pos, self))
        sv.addWidget(self.episodes)
        self.series_box.setVisible(self.item.type == "Series")
        col.addWidget(self.series_box)
        col.addStretch()

    # ================================================================== loading
    def _show_partial(self) -> None:
        """Paint what the card already knew while the full item loads."""
        self._apply_header(self.item, loading=True)

    def load(self) -> None:
        self._gen += 1
        gen = self._gen
        c, item_id, kind = self.session.client, self.item.id, self.item.type
        want_season = self._want_season or (self._bundle.season.id if self._bundle and self._bundle.season else None)

        def work() -> Bundle:
            it = details.get_item(c, item_id)
            b = Bundle(it)
            if (it.type or kind) in PLAYABLE:
                details.ensure_streams(c, it)
            if (it.type or kind) == "Series":
                b.seasons = details.seasons(c, item_id)
                b.next_up = details.next_up_for_series(c, item_id)
                b.season = next((s for s in b.seasons if s.id == want_season), None) \
                    or details.pick_season(b.seasons, b.next_up)
                if b.season is not None:
                    b.episodes = details.episodes(c, item_id, b.season.id)
                if b.next_up is None:
                    unwatched = next((e for e in b.episodes if not e.user_data.played), None)
                    if unwatched is not None:
                        b.next_up = unwatched
                    else:              # all watched: "Watch again" from the first real episode
                        everything = details.episodes(c, item_id)
                        regular = [e for e in everything if (e.parent_index_number or 0) > 0]
                        b.next_up = (regular or everything or [None])[0]
            return b

        run_async(work, lambda b: gen == self._gen and self._fill(b),
                  lambda e: gen == self._gen and self._failed(e))

    def _failed(self, e: Exception) -> None:
        self.meta_lbl.setText(f"Couldn't load details: {e}")

    def _fill(self, b: Bundle) -> None:
        self._bundle = b
        self.item = b.item
        self._want_season = None
        self._apply_header(b.item)
        if b.item.type == "Series":
            self.playable = b.next_up
            self._fill_seasons(b)
        elif b.item.type in PLAYABLE:
            self.playable = b.item
            self._setup_tracks()
        self._update_play_buttons()

    # ================================================================== header
    def _apply_header(self, it: Item, loading: bool = False) -> None:
        self.title_lbl.setText(it.name)
        if it.type == "Episode":
            self.poster.set_key(images.landscape(it, 704))
            parts = [p for p in (it.series_name, f"Season {it.parent_index_number}"
                                 if it.parent_index_number is not None else "") if p]
            self.context_btn.setText("‹ " + " · ".join(parts) if parts else "")
            self.context_btn.setVisible(bool(it.series_id))
        else:
            self.poster.set_key(images.poster(it, 440))
        self.header.set_key(images.backdrop(it))

        meta: list[str] = []
        if it.type == "Episode" and it.episode_label:
            meta.append(it.episode_label)
        if it.type == "Episode" and it.premiere_date:
            meta.append(it.premiere_date[:10])
        elif year_of(it):
            meta.append(year_of(it))
        if it.type in PLAYABLE and runtime_text(it):
            meta.append(runtime_text(it))
        if it.community_rating:
            meta.append(f"★ {it.community_rating:.1f}")
        if it.official_rating:
            meta.append(it.official_rating)
        if it.type == "Series":
            n = it.user_data.unplayed_item_count or 0
            if n and not it.user_data.played:
                meta.append(f"{n} ep unwatched")
            status = it.raw.get("Status")
            if status:
                meta.append(status)
        if loading:
            meta.append("loading…")
        self.meta_lbl.setText("  ·  ".join(meta))

        while self.genres_box.count():
            w = self.genres_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for g in it.genres[:6]:
            tag = QPushButton(g)
            tag.setProperty("role", "tag")
            tag.setCursor(Qt.CursorShape.PointingHandCursor)
            tag.setToolTip(f"Show {g} in this library")
            tag.clicked.connect(lambda _=False, genre=g: self.open_genre.emit(self.item, genre))
            self.genres_box.addWidget(tag)
        self.genres_box.addStretch()

        taglines = it.raw.get("Taglines") or []
        self.tagline_lbl.setText(taglines[0] if taglines else "")
        self.tagline_lbl.setVisible(bool(taglines))
        self.overview_lbl.setText(overview_html(it.overview) or ("" if loading else "No overview."))
        directors, cast = details.people(it)
        bits = []
        if directors:
            bits.append("Directed by " + ", ".join(directors))
        if cast:
            bits.append("With " + ", ".join(cast))
        self.people_lbl.setText("   ·   ".join(bits))
        self.people_lbl.setVisible(bool(bits))
        self._sync_toggles()

    def _sync_toggles(self) -> None:
        ud = self.item.user_data
        watched = self._watched()
        self.watched_btn.setToolTip("Click to mark unwatched" if watched else "Click to mark watched")
        for b, on, on_text, off_text in ((self.watched_btn, watched, "✓  Watched", "✓  Mark watched"),
                                         (self.fav_btn, ud.is_favorite, "♥  Favourite", "♡  Favourite")):
            b.blockSignals(True)
            b.setChecked(on)
            b.setText(on_text if on else off_text)
            b.blockSignals(False)

    def _watched(self) -> bool:
        """For a series: every regular season has no unwatched episodes (specials don't count)."""
        b = self._bundle
        if self.item.type == "Series" and b is not None and b.seasons:
            regular = [s for s in b.seasons if (s.index_number or 0) > 0] or b.seasons
            return all((s.user_data.unplayed_item_count or 0) == 0 for s in regular)
        return is_watched(self.item)

    def _toggle_watched(self) -> None:
        target = not self._watched()
        if self.item.type == "Series" and self._bundle is not None:
            # keep the header and season list honest straight away
            for s in self._bundle.seasons:
                if target:
                    s.user_data.unplayed_item_count = 0
                elif s.user_data.unplayed_item_count == 0:
                    s.user_data.unplayed_item_count = s.child_count or 1
        self.actions.set_played(self.item, target)
        self._sync_toggles()

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.watched_btn and self._watched():
            if ev.type() == QEvent.Type.Enter:
                self.watched_btn.setText("↺  Mark unwatched")
            elif ev.type() == QEvent.Type.Leave:
                self.watched_btn.setText("✓  Watched")
        return super().eventFilter(obj, ev)

    def _update_play_buttons(self) -> None:
        p = self.playable
        if p is None:
            self.play_btn.setEnabled(False)
            self.play_btn.setText("▶  Nothing to play")
            self.restart_btn.hide()
            return
        self.play_btn.setEnabled(True)
        resume = has_resume(p)
        if self.item.type == "Series":
            label = p.episode_label or ("Special" if (p.parent_index_number or 0) == 0 else "next episode")
            if self._watched() and not resume:
                self.play_btn.setText(f"↺  Watch again · {label}")
            else:
                self.play_btn.setText(f"▶  {'Resume' if resume else 'Play'} {label}")
            self.restart_btn.hide()
        else:
            secs = tracks.resume_seconds(p, rewind=0)
            if resume:
                self.play_btn.setText(f"▶  Resume from {clock(secs)}")
            else:
                self.play_btn.setText("↺  Watch again" if p.user_data.played else "▶  Play")
            self.restart_btn.setVisible(resume)

    # ================================================================== tracks
    def _source(self) -> MediaSource | None:
        it = self.item
        if not it.media_sources:
            return None
        sid = self.choice.source_id if self.choice else None
        return next((s for s in it.media_sources if s.id == sid), it.media_sources[0])

    def _setup_tracks(self) -> None:
        keep = self.choice is not None and any(s.id == self.choice.source_id for s in self.item.media_sources)
        if not keep:                      # first load; a quiet refresh keeps your picks
            self.choice = tracks.choice_from_settings(self.item, self.session.settings)
        self._populate_tracks()
        self.pickers.show()

    def _populate_tracks(self) -> None:
        it, src, ch = self.item, self._source(), self.choice
        for cb in (self.version_cb, self.audio_cb, self.subs_cb):
            cb.blockSignals(True)
            cb.clear()
        try:
            for i, s in enumerate(it.media_sources):
                self.version_cb.addItem(tracks.source_label(s, i), s.id)
            many = len(it.media_sources) > 1
            self.version_cb.setVisible(many)
            self.version_lbl.setVisible(many)
            if src is not None:
                self.version_cb.setCurrentIndex(max(0, self.version_cb.findData(src.id)))
            audio = src.audio_streams if src else []
            subs = src.subtitle_streams if src else []
            for st in audio:
                self.audio_cb.addItem(tracks.audio_label(st), st.index)
            self.subs_cb.addItem("Off", OFF)
            for st in subs:
                self.subs_cb.addItem(tracks.subtitle_label(st), st.index)
            if ch is not None:
                self.audio_cb.setCurrentIndex(max(0, self.audio_cb.findData(ch.audio_index)))
                self.subs_cb.setCurrentIndex(max(0, self.subs_cb.findData(ch.subtitle_index)))
            self.audio_cb.setEnabled(bool(audio))
            self.subs_cb.setEnabled(bool(subs))
            if src is None or not src.streams:
                self.tracks_note.setText("Track details aren't available for this file — "
                                         "the player will use the file's defaults.")
            elif self.item.type == "Episode":
                self.tracks_note.setText("Your audio and subtitle choice is remembered for this series.")
            else:
                self.tracks_note.setText("")
            self.tracks_note.setVisible(bool(self.tracks_note.text()))
        finally:
            for cb in (self.version_cb, self.audio_cb, self.subs_cb):
                cb.blockSignals(False)

    def _version_changed(self, _i: int) -> None:
        sid = self.version_cb.currentData()
        self.choice = tracks.choice_from_settings(self.item, self.session.settings, source_id=sid)
        self._populate_tracks()

    def _tracks_changed(self, _i: int) -> None:
        if self.choice is None:
            return
        audio = self.audio_cb.currentData()
        sub = self.subs_cb.currentData()
        self.choice = TrackChoice(self.choice.source_id, audio, OFF if sub is None else sub)
        sid = self.item.series_id if self.item.type == "Episode" else None
        src = self._source()
        if sid and src is not None:
            self.session.settings.set(f"series_tracks.{sid}",
                                      tracks.memory_for(src, audio, self.choice.subtitle_index,
                                                        self.item.series_name or None))

    # ================================================================== play
    def _play(self, restart: bool) -> None:
        if self.playable is not None:
            self._emit_play(self.playable, restart)

    def _emit_play(self, target: Item, restart: bool) -> None:
        rewind = float(self.session.settings.get("playback.resume_rewind_seconds", 5) or 0)
        start = 0.0 if restart else tracks.resume_seconds(target, rewind)
        if target.id == self.item.id and self.choice is not None:
            req = PlayRequest(target, self.choice.source_id, self.choice.audio_index,
                              self.choice.subtitle_index, start)
        else:
            req = PlayRequest(target, start_seconds=start)
        self.play.emit(req)

    # ================================================================== seasons
    def _fill_seasons(self, b: Bundle) -> None:
        self.series_box.show()
        self.season_cb.blockSignals(True)
        self.season_cb.clear()
        for s in b.seasons:
            self.season_cb.addItem(season_label(s), s.id)
        if b.season is not None:
            self.season_cb.setCurrentIndex(max(0, self.season_cb.findData(b.season.id)))
        self.season_cb.blockSignals(False)
        self.season_cb.setVisible(len(b.seasons) > 0)
        self._set_episodes(b.episodes)

    def _set_episodes(self, eps: list[Item]) -> None:
        self.episodes.list_model.set_items(eps)
        watched = sum(1 for e in eps if e.user_data.played)
        self.ep_count.setText(f"{len(eps)} episode{'s' if len(eps) != 1 else ''} · {watched} watched"
                              if eps else "No episodes")

    def _season_changed(self, _i: int) -> None:
        season_id = self.season_cb.currentData()
        if not season_id or self._bundle is None:
            return
        self._bundle.season = next((s for s in self._bundle.seasons if s.id == season_id), None)
        self._ep_gen += 1
        gen = self._ep_gen
        self.episodes.list_model.set_loading()
        c, series_id = self.session.client, self.item.id
        run_async(lambda: details.episodes(c, series_id, season_id),
                  lambda eps: gen == self._ep_gen and self._set_episodes(eps),
                  lambda e: self.actions.toast.emit(f"Couldn't load episodes: {e}", True))

    def _open_series(self) -> None:
        it = self.item
        if it.series_id:
            stub = Item.from_json({"Id": it.series_id, "Name": it.series_name or "", "Type": "Series"})
            self.open_item.emit(stub)

    # ================================================================== updates
    def _item_changed(self, item_id: str, fn) -> None:
        touched = False
        if item_id == self.item.id:
            fn(self.item)
            touched = True
        if self.playable is not None and self.playable is not self.item and self.playable.id == item_id:
            fn(self.playable)
            touched = True
        if self.episodes.list_model.update_where(item_id, fn):
            self._set_episodes(self.episodes.list_model.items)
        if touched:
            self._sync_toggles()
            self._update_play_buttons()

    def _server_changed(self) -> None:
        if self.isVisible():
            self.load()                # quiet refresh: header counts, season ticks, next up
        else:
            self._stale = True

    def page_shown(self) -> None:
        if self._stale:
            self._stale = False
            self.load()
