"""Qt-free helpers behind the Settings page (so they can be tested without a display)."""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from urllib.parse import urlparse

from .api.client import is_private_host, normalize_base_url
from .api.tracks import language_name

# Languages offered in the pickers (ISO 639-2, the style Jellyfin reports). Any other
# code can still be typed in.
COMMON_LANGUAGES = ["jpn", "eng", "tur", "kor", "chi", "spa", "fre", "ger", "ita", "por", "rus",
                    "ara", "hin", "pol", "dut", "swe", "tha", "vie", "ind"]

SKIP_MODES = [("button", "Show a Skip button"), ("auto", "Skip automatically"), ("off", "Do nothing")]
SUBS_WHEN_MATCHES = [("off", "No subtitles"), ("forced", "Signs & songs only (forced)"),
                     ("always", "Full subtitles")]
ASS_MODES = [("no", "Keep their styling"), ("scale", "Size only"), ("force", "Force my style")]

# mpv's own defaults, shown when you haven't changed a property.
STYLE_DEFAULTS = {"sub-scale": 1.0, "sub-bold": False, "sub-font": "", "sub-border-size": 3.0,
                  "sub-pos": 100, "sub-ass-override": "scale"}


def language_label(code: str) -> str:
    name = language_name(code)
    return f"{name} ({code})" if name.lower() != code.lower() else code


def clean_language(code: str) -> str | None:
    """'Japanese (jpn)' / ' JPN ' / 'pt-BR' → a code, or None if it isn't one."""
    c = (code or "").strip()
    if c.endswith(")") and "(" in c:
        c = c[c.rindex("(") + 1:-1]
    c = c.strip().lower().replace("_", "-")
    if not c or len(c) > 12 or not all(ch.isalnum() or ch == "-" for ch in c):
        return None
    return c


def add_language(langs: list[str], code: str) -> list[str]:
    c = clean_language(code)
    if not c or c in langs:
        return list(langs)
    return [*langs, c]


def move(seq: list, index: int, delta: int) -> list:
    out = list(seq)
    j = index + delta
    if 0 <= index < len(out) and 0 <= j < len(out):
        out[index], out[j] = out[j], out[index]
    return out


# --------------------------------------------------------------------------- addresses

@dataclass
class AddressRow:
    label: str
    url: str
    public: bool


def validate_addresses(rows: list[AddressRow]) -> tuple[list[dict], str]:
    """Rows from the editor → settings list, or ([], error message)."""
    out: list[dict] = []
    seen: set[str] = set()
    for i, r in enumerate(rows, 1):
        url = normalize_base_url(r.url)
        if not url:
            continue
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return [], f"Address {i} isn't a valid http(s) URL: {r.url.strip()}"
        if url in seen:
            return [], f"{url} is listed twice."
        seen.add(url)
        out.append({"label": r.label.strip() or p.hostname, "url": url, "public": bool(r.public)})
    if not out:
        return [], "Keep at least one address."
    return out, ""


def first_address(url: str) -> tuple[dict | None, str]:
    """The address typed on the first-run screen → an addresses entry, or (None, error)."""
    rows, err = validate_addresses([AddressRow("", url, guess_public(url))])
    if err:
        return None, ("Type your server's address, e.g. http://192.168.1.50:8096"
                      if "at least one" in err else err)
    a = rows[0]
    a["label"] = "Public" if a["public"] else "Home"
    return a, ""


def guess_public(url: str) -> bool:
    """New addresses: anything that isn't LAN/Tailscale/loopback counts as public."""
    u = normalize_base_url(url)
    return bool(u) and not is_private_host(u)


# --------------------------------------------------------------------------- remembered tracks

def describe_memory(mem: dict) -> str:
    """{'audio_lang': 'jpn', 'subtitle': {'lang': 'eng', 'forced': False}} → 'Japanese audio · English subtitles'."""
    parts = []
    a = mem.get("audio_lang")
    if a:
        parts.append(f"{language_name(a)} audio")
    sub = mem.get("subtitle") or {}
    if sub.get("off"):
        parts.append("no subtitles")
    elif sub.get("lang"):
        parts.append(f"{language_name(sub['lang'])} {'forced ' if sub.get('forced') else ''}subtitles")
    return " · ".join(parts) or "defaults"


def remembered_series(series_tracks: dict) -> list[tuple[str, str, str]]:
    """[(series_id, name or '', description)] sorted by name, unnamed last."""
    rows = [(sid, (m or {}).get("name") or "", describe_memory(m or {}))
            for sid, m in (series_tracks or {}).items() if isinstance(m, dict)]
    return sorted(rows, key=lambda r: (not r[1], r[1].lower(), r[0]))


# --------------------------------------------------------------------------- mpv

def args_to_text(args) -> str:
    if not args:
        return ""
    if isinstance(args, str):
        return args
    return shlex.join(str(a) for a in args)


def text_to_args(text: str) -> tuple[list[str], str]:
    """Parse the extra-args box; ([], error) when quotes don't match."""
    try:
        return shlex.split(text or ""), ""
    except ValueError as e:
        return [], f"Couldn't read the extra arguments: {e}"


def style_value(saved: dict, prop: str):
    return saved.get(prop, STYLE_DEFAULTS[prop])


def set_style(saved: dict, prop: str, value) -> dict:
    """Store a changed style property. Untouched mpv defaults aren't stored, so mpv.conf still wins."""
    out = dict(saved)
    if value == STYLE_DEFAULTS.get(prop) and prop not in out:
        return out
    out[prop] = value
    return out


def human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"
