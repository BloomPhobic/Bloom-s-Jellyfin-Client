"""Text shown on cards and pages, kept out of Qt so it's testable."""
from __future__ import annotations

import html as _html
from html.parser import HTMLParser as _HTMLParser

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
        bits = [f"{unplayed} ep unwatched" if unplayed and not item.user_data.played else "",
                year_of(item)]
        return item.name, " | ".join(b for b in bits if b)
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


# --------------------------------------------------------------------------- overviews
# Jellyfin overviews often carry HTML from metadata providers (<br>, <i>, <p> …).

_KEEP = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u"}
_BREAKS = {"br", "p", "div", "li"}


class _Overview(_HTMLParser):
    def __init__(self, rich: bool):
        super().__init__(convert_charrefs=True)
        self.rich = rich
        self.out: list[str] = []
        self._skip = 0          # inside <script>/<style>

    def _brk(self) -> None:
        self.out.append("\n")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BREAKS:
            self._brk()
        elif self.rich and tag in _KEEP:
            self.out.append(f"<{_KEEP[tag]}>")

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in _BREAKS:
            self._brk()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("p", "div"):
            self._brk()
        elif self.rich and tag in _KEEP:
            self.out.append(f"</{_KEEP[tag]}>")

    def handle_data(self, data):
        if self._skip:
            return
        self.out.append(_html.escape(data, quote=False) if self.rich else data)


def _paragraphs(text: str) -> list[str]:
    paras, cur = [], []
    for line in text.split("\n"):
        line = " ".join(line.split())
        if line:
            cur.append(line)
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    return paras


def overview_html(text: str | None) -> str:
    """Safe rich text for a QLabel: only b/i/u kept, line breaks become paragraph gaps."""
    if not text:
        return ""
    p = _Overview(rich=True)
    p.feed(text.replace("\r\n", "\n"))
    p.close()
    return "<br><br>".join(_paragraphs("".join(p.out)))


def overview_plain(text: str | None) -> str:
    """Single-line plain text (episode rows)."""
    if not text:
        return ""
    p = _Overview(rich=False)
    p.feed(text)
    p.close()
    return " ".join(_paragraphs("".join(p.out)))
