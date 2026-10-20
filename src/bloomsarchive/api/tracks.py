"""Pre-play choices: version, audio and subtitle track, start position.

Defaults come from (in order): what you picked last time for this series,
your preferred languages, then the file's own default flags.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Item, MediaSource, MediaStream, ticks_to_seconds

OFF = -1

LANGUAGES = {
    "eng": "English", "jpn": "Japanese", "tur": "Turkish", "ger": "German", "deu": "German",
    "fre": "French", "fra": "French", "spa": "Spanish", "ita": "Italian", "por": "Portuguese",
    "rus": "Russian", "chi": "Chinese", "zho": "Chinese", "kor": "Korean", "ara": "Arabic",
    "pol": "Polish", "dut": "Dutch", "nld": "Dutch", "swe": "Swedish", "hin": "Hindi",
    "tha": "Thai", "vie": "Vietnamese", "ind": "Indonesian", "may": "Malay", "msa": "Malay",
    "und": "Unknown",
    "en": "English", "ja": "Japanese", "tr": "Turkish", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "zh": "Chinese", "ko": "Korean", "ar": "Arabic",
    "pl": "Polish", "nl": "Dutch", "sv": "Swedish", "hi": "Hindi", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "ms": "Malay",
}
REGIONS = {"419": "Latin America", "br": "Brazil", "pt": "Portugal", "mx": "Mexico", "es": "Spain",
           "us": "US", "gb": "UK", "cn": "China", "tw": "Taiwan", "hk": "Hong Kong"}


@dataclass
class TrackChoice:
    source_id: str | None
    audio_index: int | None
    subtitle_index: int          # OFF (-1) for no subtitles


@dataclass
class PlayRequest:
    item: Item
    source_id: str | None = None
    audio_index: int | None = None
    subtitle_index: int | None = None     # None = let the player decide; OFF = none
    start_seconds: float = 0.0


def language_name(code: str | None) -> str:
    """'jpn' → Japanese, 'en' → English, 'es-419' → Spanish (Latin America)."""
    if not code:
        return "Unknown"
    c = code.lower().replace("_", "-")
    base, _, region = c.partition("-")
    name = LANGUAGES.get(base) or LANGUAGES.get(c)
    if name is None:
        return code.upper() if len(code) <= 3 else code
    return f"{name} ({REGIONS.get(region, region.upper())})" if region else name


def _lang(s: MediaStream) -> str:
    return (s.language or "und").lower()


def _same_lang(a: str, b: str) -> bool:
    """Same language whatever the code style: eng/en, ger/deu/de, pt-BR/por …"""
    def name(code: str) -> str:
        base = code.lower().replace("_", "-").split("-")[0]
        return LANGUAGES.get(base, base)
    return name(a) == name(b)


def _repeats_language(title: str, lang: str | None) -> bool:
    base = language_name((lang or "").split("-")[0]).lower()
    return base in title.lower() or title.lower() in language_name(lang).lower()


def audio_label(s: MediaStream) -> str:
    bits = [language_name(s.language)]
    if s.title and not _repeats_language(s.title, s.language):
        bits.append(s.title)
    tech = (s.codec or "").upper()
    if s.channels:
        tech += f" {_channels(s.channels)}"
    if tech.strip():
        bits.append(tech.strip())
    label = " · ".join(bits)
    return label + ("  (default)" if s.is_default else "")


def subtitle_label(s: MediaStream) -> str:
    bits = [language_name(s.language)]
    if s.title and not _repeats_language(s.title, s.language):
        bits.append(s.title)
    fmt = (s.codec or "").upper().replace("SUBRIP", "SRT")
    if fmt:
        bits.append(fmt)
    flags = [f for f, on in (("forced", s.is_forced), ("external", s.is_external), ("default", s.is_default)) if on]
    return " · ".join(bits) + (f"  ({', '.join(flags)})" if flags else "")


def _channels(n: int) -> str:
    return {1: "mono", 2: "2.0", 6: "5.1", 8: "7.1"}.get(n, f"{n}ch")


def source_label(src: MediaSource, i: int) -> str:
    name = src.name or f"Version {i + 1}"
    size = f" · {src.size / 1e9:.1f} GB" if src.size else ""
    return f"{name} · {src.container.upper()}{size}" if src.container else name + size


# --------------------------------------------------------------------------- defaults

def pick_audio(src: MediaSource, preferred: list[str], remembered: str | None) -> int | None:
    audio = src.audio_streams
    if not audio:
        return None
    for want in ([remembered] if remembered else []) + list(preferred):
        for s in audio:
            if _same_lang(_lang(s), want):
                return s.index
    if src.default_audio_index is not None and any(s.index == src.default_audio_index for s in audio):
        return src.default_audio_index
    default = next((s for s in audio if s.is_default), None)
    return (default or audio[0]).index


def pick_subtitle(src: MediaSource, audio_lang: str | None, preferred: list[str],
                  remembered: dict | None, when_audio_matches: str = "forced") -> int:
    subs = src.subtitle_streams
    if not subs:
        return OFF
    if remembered is not None:
        if remembered.get("off"):
            return OFF
        want = remembered.get("lang")
        if want:
            match = [s for s in subs if _same_lang(_lang(s), want)]
            if match:
                forced = bool(remembered.get("forced"))
                pick = next((s for s in match if s.is_forced == forced), match[0])
                return pick.index

    # Audio already in a language you read: by default only forced subs (signs/foreign lines).
    if audio_lang and any(_same_lang(audio_lang, p) for p in preferred) and when_audio_matches != "always":
        if when_audio_matches == "off":
            return OFF
        forced = next((s for s in subs if s.is_forced and _same_lang(_lang(s), audio_lang)), None)
        return forced.index if forced else OFF

    for want in preferred:
        match = [s for s in subs if _same_lang(_lang(s), want)]
        if match:
            full = [s for s in match if not s.is_forced]
            pool = full or match
            pick = next((s for s in pool if s.is_default), pool[0])
            return pick.index
    if src.default_subtitle_index is not None and src.default_subtitle_index >= 0:
        return src.default_subtitle_index
    return OFF


def default_choice(item: Item, preferred_audio: list[str], preferred_subs: list[str],
                   remembered: dict | None = None, source_id: str | None = None,
                   when_audio_matches: str = "forced") -> TrackChoice:
    if not item.media_sources:
        return TrackChoice(None, None, OFF)
    src = next((s for s in item.media_sources if s.id == source_id), item.media_sources[0])
    rem = remembered or {}
    a = pick_audio(src, preferred_audio, rem.get("audio_lang"))
    audio_lang = next((_lang(s) for s in src.audio_streams if s.index == a), None)
    sub_mem = rem.get("subtitle") if isinstance(rem.get("subtitle"), dict) else None
    sub = pick_subtitle(src, audio_lang, preferred_subs, sub_mem, when_audio_matches)
    return TrackChoice(src.id, a, sub)


def choice_from_settings(item: Item, settings, source_id: str | None = None) -> TrackChoice:
    """default_choice with the user's language settings and this series' memory."""
    langs = settings.get("languages", {}) or {}
    remembered = settings.get(f"series_tracks.{item.series_id}") if item.series_id else None
    return default_choice(item, list(langs.get("audio") or []), list(langs.get("subtitle") or []),
                          remembered, source_id, langs.get("subs_when_audio_matches", "forced") or "forced")


def memory_for(src: MediaSource, audio_index: int | None, subtitle_index: int, name: str | None = None) -> dict:
    """What to remember for a series: languages, not indexes (they differ per file)."""
    audio = next((s for s in src.audio_streams if s.index == audio_index), None)
    sub = next((s for s in src.subtitle_streams if s.index == subtitle_index), None)
    return {
        "audio_lang": _lang(audio) if audio else None,
        "subtitle": {"off": True} if sub is None else {"lang": _lang(sub), "forced": sub.is_forced},
        **({"name": name} if name else {}),
    }


def resume_seconds(item: Item, rewind: float = 5.0) -> float:
    if item.user_data.played:          # a finished item starts over, whatever position is stored
        return 0.0
    pos = ticks_to_seconds(item.user_data.playback_position_ticks)
    return max(0.0, pos - rewind) if pos > 0 else 0.0
