"""`bloomsarchive --selfcheck`: a plain-text report to run against the real server.

Prints the server version, which address won and why, Quick Connect status,
public user count, which compat routes work, which skip-segment source works,
and does one small static=true byte-range request (direct play only).
"""
from __future__ import annotations

import getpass
import sys
from typing import TextIO

from . import __version__
from .api import auth
from .api.client import JellyfinClient, JellyfinError, Unauthorized
from .api.compat import parse_version
from .api.discovery import Candidate, discover
from .api.models import Item
from .config import Settings, TokenStore
from .player import segments

RANGE_BYTES = 65536


class Report:
    def __init__(self, out: TextIO):
        self.out = out
        self.problems: list[str] = []

    def head(self, title: str) -> None:
        print(f"\n== {title}", file=self.out)

    def line(self, text: str) -> None:
        print(f"   {text}", file=self.out)

    def ok(self, text: str) -> None:
        print(f"[ok] {text}", file=self.out)

    def warn(self, text: str) -> None:
        print(f"[!!] {text}", file=self.out)
        self.problems.append(text)

    def info(self, text: str) -> None:
        print(f"[--] {text}", file=self.out)


def stream_url_path(item_id: str) -> tuple[str, dict]:
    """The only playback URL shape this app ever uses."""
    return f"/Videos/{item_id}/stream", {"static": "true"}


def run(settings: Settings | None = None, out: TextIO = sys.stdout, user: str | None = None,
        password: str | None = None, pin: bool = False, interactive: bool | None = None) -> int:
    settings = settings or Settings()
    tokens = TokenStore(settings)
    if interactive is None:
        interactive = sys.stdin.isatty()
    r = Report(out)
    print(f"Bloom's Archive {__version__} self-check", file=out)

    # ------------------------------------------------------------------ setup
    r.head("Local setup")
    r.line(f"settings: {settings.path}")
    r.line(f"token store: {tokens.backend}")
    device_id = settings.device_id()
    r.line(f"device id: {device_id}")
    pinned = settings.get("server_id")
    r.line(f"pinned ServerId: {pinned or '(none yet)'}")

    # ------------------------------------------------------------------ discovery
    r.head("Address selection")
    cands = Candidate.from_settings(settings.get("addresses", []))
    net = settings.get("network", {})
    d = discover(cands, pinned, device_id,
                 private_timeout=net.get("probe_timeout_private", 1.5),
                 public_timeout=net.get("probe_timeout_public", 3.0))
    # discover() returns as soon as the winner is known; show everything we heard.
    for res in d.results:
        r.line(res.describe())
    for c in cands:
        if c not in [x.candidate for x in d.results]:
            r.line(f"{c.label or c.url}: not needed (a better address already won)")
    if not d.chosen:
        r.warn(d.reason)
        return _finish(r)
    r.ok(f"using {d.chosen.candidate.url} — {d.reason}")
    info = d.chosen.info
    assert info is not None

    if d.needs_pin:
        do_pin = pin
        if not pin and interactive:
            ans = input(f"   Pin ServerId {info.id} ({info.name}) as your server? [Y/n] ").strip().lower()
            do_pin = ans in ("", "y", "yes")
        if do_pin:
            settings.set("server_id", info.id)
            r.ok(f"pinned ServerId {info.id}")
        else:
            r.warn("ServerId not pinned — run with --pin (or answer Y) so a shared network can't impersonate it")
    if d.is_public:
        if settings.get("network.never_stream_public", True):
            r.warn("only the public (Cloudflare) address works right now — browsing OK, "
                   "playback will ask you to connect Tailscale")
        else:
            r.info("on the public address; video would stream through Cloudflare")
    settings.set("last_address", d.chosen.candidate.url)

    # ------------------------------------------------------------------ server
    r.head("Server")
    r.line(f"name: {info.name}   product: {info.product or '?'}   version: {info.version}")
    client = JellyfinClient(d.chosen.candidate.url, device_id=device_id)
    client.server_info = info.raw
    client.compat.set_version(info.version)
    v = parse_version(info.version)
    r.line(f"route style expected: {'new (10.9+)' if client.compat.prefers_new() else 'legacy (<10.9)'}")
    if v and v < (10, 8, 0):
        r.warn(f"Jellyfin {info.version} is old; this client targets 10.8+")

    try:
        qc = auth.quick_connect_enabled(client)
        (r.ok if qc else r.info)(f"Quick Connect {'enabled' if qc else 'disabled'}")
    except JellyfinError as e:
        r.warn(f"Quick Connect check failed: {e}")
    try:
        users = auth.public_users(client)
        r.ok(f"{len(users)} public user(s): {', '.join(u.name for u in users) or '(none — use Manual login)'}")
    except JellyfinError as e:
        users = []
        r.warn(f"/Users/Public failed: {e}")

    # ------------------------------------------------------------------ login
    r.head("Sign-in")
    if not _sign_in(client, settings, tokens, info.id, users, user, password, interactive, r):
        r.info("skipping signed-in checks (run interactively, or pass --user)")
        return _finish(r)

    # ------------------------------------------------------------------ routes
    r.head("API routes")
    try:
        views = client.compat.call("views")
        names = [x.get("Name", "?") for x in (views or {}).get("Items", [])]
        r.ok(f"libraries via {client.compat.resolve_path('views')}: {', '.join(names) or '(none)'}")
    except JellyfinError as e:
        r.warn(f"libraries failed: {e}")
    for name, params in (("resume", {"mediaTypes": "Video", "limit": 1}),
                         ("latest", {"limit": 1})):
        try:
            client.compat.call(name, params=params)
            r.ok(f"{name} via {client.compat.resolve_path(name)}")
        except JellyfinError as e:
            r.warn(f"{name} failed: {e}")
    try:
        client.get("/Shows/NextUp", userId=client.user_id, limit=1)
        r.ok("next up via /Shows/NextUp")
    except JellyfinError as e:
        r.warn(f"next up failed: {e}")

    # ------------------------------------------------------------------ segments
    r.head("Skip intro / credits")
    episodes = _sample(client, "Episode", 3)
    if not episodes:
        r.info("no episodes found to test")
    else:
        for src, verdict in segments.probe_sources(client, episodes).items():
            (r.ok if verdict.startswith("works") else r.info)(f"{src}: {verdict}")

    # ------------------------------------------------------------------ direct play
    r.head("Direct play")
    video = (episodes or _sample(client, "Movie", 1) or [None])[0]
    if video is None:
        r.info("no video items found to test")
    else:
        _range_test(client, video, r)

    return _finish(r)


# --------------------------------------------------------------------------- helpers

def _sign_in(client: JellyfinClient, settings: Settings, tokens: TokenStore, server_id: str,
             users: list, user: str | None, password: str | None, interactive: bool, r: Report) -> bool:
    last = settings.get("last_user")
    if last and not user:
        tok = tokens.get(server_id, last.get("id", ""))
        if tok:
            client.token, client.user_id = tok, last["id"]
            try:
                me = auth.current_user(client)
                r.ok(f"stored token for {me.name} is valid")
                return True
            except Unauthorized:
                r.warn(f"stored token for {last.get('name')} was rejected (expired or revoked)")
                client.token = client.user_id = None
            except JellyfinError as e:
                r.warn(f"could not validate stored token: {e}")
                return False
    if not user and interactive:
        hint = f" [{users[0].name}]" if len(users) == 1 else ""
        user = input(f"   Username{hint}: ").strip() or (users[0].name if len(users) == 1 else "")
    if not user:
        return False
    if password is None:
        password = getpass.getpass("   Password (blank if none): ") if interactive else ""
    try:
        res = auth.login_password(client, user, password)
    except (auth.AuthError, JellyfinError) as e:
        r.warn(f"login as {user} failed: {e}")
        return False
    r.ok(f"signed in as {res.user.name} (token kept in memory only)")
    return True


def _sample(client: JellyfinClient, item_type: str, n: int) -> list[Item]:
    try:
        data = client.compat.call("items", params={
            "recursive": True, "includeItemTypes": item_type, "limit": n,
            "fields": "MediaSources,Chapters", "sortBy": "DateCreated", "sortOrder": "Descending",
        })
    except JellyfinError:
        return []
    return [Item.from_json(x) for x in (data or {}).get("Items", [])]


def _range_test(client: JellyfinClient, item: Item, r: Report) -> None:
    source = item.media_sources[0] if item.media_sources else None
    path, params = stream_url_path(item.id)
    if source:
        params["MediaSourceId"] = source.id
    label = f"{item.series_name + ' ' if item.series_name else ''}{item.episode_label} {item.name}".strip()
    r.line(f"item: {label}" + (f" ({source.container}, {len(source.streams)} streams)" if source else ""))
    r.line(f"url: {client.url(path, params)}")
    try:
        with client.open("GET", path, params, headers={"Range": f"bytes=0-{RANGE_BYTES - 1}"},
                         timeout=15) as resp:
            body = resp.read(RANGE_BYTES)
            status = resp.status
            ctype = resp.headers.get("Content-Type", "?")
            crange = resp.headers.get("Content-Range", "")
    except JellyfinError as e:
        r.warn(f"static stream request failed: {e}")
        return
    if status == 206:
        r.ok(f"byte-range direct play works: 206, {len(body)} bytes, {ctype}, {crange}")
    elif status == 200:
        r.warn(f"server ignored Range (200, {ctype}) — seeking in mpv may be slow")
    else:
        r.warn(f"unexpected status {status}")
    if "mpegurl" in ctype.lower():
        r.warn("server answered with a playlist — that would be a transcode; report this")


def _finish(r: Report) -> int:
    r.head("Summary")
    if r.problems:
        for p in r.problems:
            r.line(f"- {p}")
        print("\nSome checks need attention. Paste this whole output back if you want help.", file=r.out)
        return 1
    print("\nAll checks passed.", file=r.out)
    return 0
