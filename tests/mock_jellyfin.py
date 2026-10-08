"""A small fake Jellyfin server for tests and offline development.

    python tests/mock_jellyfin.py --port 8096 [--legacy] [--version 10.8.13]

Every request is recorded in `server.log` so tests can assert that no
transcode URL (master.m3u8, non-static stream) is ever requested.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TPS = 10_000_000
STREAM_SIZE = 1_048_576
STREAM_BYTES = bytes((i * 7 + 3) % 256 for i in range(STREAM_SIZE))


@dataclass
class LoggedRequest:
    method: str
    path: str
    query: dict
    headers: dict
    status: int = 0


@dataclass
class MockConfig:
    server_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "Home Media"
    version: str = "10.10.7"
    route_style: str = "auto"          # auto (by version) | new | legacy | both
    delay: float = 0.0                 # seconds before every response
    quick_connect: bool = True
    qc_auto_approve_after: int | None = None   # approve after N polls (None: wait for /Authorize)
    segments: str = "media-segments"   # media-segments | intro-skipper | chapters | none
    honor_range: bool = True


def _ticks(sec: float) -> int:
    return int(sec * TPS)


def _media_source(item_id: str) -> dict:
    return {
        "Id": item_id, "Name": "Default", "Container": "mkv", "Size": STREAM_SIZE, "Bitrate": 4_000_000,
        "DefaultAudioStreamIndex": 1, "DefaultSubtitleStreamIndex": 3,
        "MediaStreams": [
            {"Index": 0, "Type": "Video", "Codec": "hevc", "DisplayTitle": "1080p HEVC"},
            {"Index": 1, "Type": "Audio", "Codec": "flac", "Language": "jpn", "Channels": 2,
             "DisplayTitle": "Japanese FLAC stereo", "IsDefault": True},
            {"Index": 2, "Type": "Audio", "Codec": "aac", "Language": "eng", "Channels": 6,
             "DisplayTitle": "English AAC 5.1"},
            {"Index": 3, "Type": "Subtitle", "Codec": "ass", "Language": "eng", "Title": "Full",
             "DisplayTitle": "English (ASS)", "IsDefault": True},
            {"Index": 4, "Type": "Subtitle", "Codec": "srt", "Language": "eng", "IsExternal": True,
             "DisplayTitle": "English (SRT, external)",
             "DeliveryUrl": f"/Videos/{item_id}/{item_id}/Subtitles/4/Stream.srt"},
        ],
    }


def build_library() -> tuple[list[dict], dict[str, dict]]:
    views = [
        {"Id": "view-movies", "Name": "Movies", "Type": "CollectionFolder", "CollectionType": "movies",
         "ImageTags": {"Primary": "tag-view-movies"}},
        {"Id": "view-anime", "Name": "Anime", "Type": "CollectionFolder", "CollectionType": "tvshows",
         "ImageTags": {"Primary": "tag-view-anime"}},
        {"Id": "view-collections", "Name": "Collections", "Type": "CollectionFolder",
         "CollectionType": "boxsets", "ImageTags": {}},
    ]
    items: dict[str, dict] = {}

    def add(d: dict) -> dict:
        d.setdefault("ImageTags", {"Primary": "tag-" + d["Id"]})
        d.setdefault("UserData", {"Played": False, "IsFavorite": False, "PlaybackPositionTicks": 0, "PlayCount": 0})
        items[d["Id"]] = d
        return d

    add({"Id": "movie-1", "Name": "Test Movie", "Type": "Movie", "ParentId": "view-movies",
         "ProductionYear": 2021, "RunTimeTicks": _ticks(5400), "Genres": ["Drama"],
         "DateCreated": "2026-01-01T00:00:00Z", "Overview": "A test movie.",
         "MediaSources": [_media_source("movie-1")]})
    add({"Id": "series-1", "Name": "Test Anime", "Type": "Series", "ParentId": "view-anime",
         "ProductionYear": 2024, "Genres": ["Action", "Fantasy"], "DateCreated": "2026-02-01T00:00:00Z"})
    add({"Id": "season-1", "Name": "Season 1", "Type": "Season", "ParentId": "series-1",
         "SeriesId": "series-1", "IndexNumber": 1})
    for n in range(1, 4):
        eid = f"ep-{n}"
        add({"Id": eid, "Name": f"Episode {n}", "Type": "Episode", "ParentId": "season-1",
             "SeriesId": "series-1", "SeriesName": "Test Anime", "SeasonId": "season-1",
             "SeriesPrimaryImageTag": "tag-series-1",
             "IndexNumber": n, "ParentIndexNumber": 1, "RunTimeTicks": _ticks(1440),
             "DateCreated": f"2026-02-0{n + 1}T00:00:00Z",
             "Chapters": [
                 {"Name": "Prologue", "StartPositionTicks": 0},
                 {"Name": "Opening", "StartPositionTicks": _ticks(90)},
                 {"Name": "Part A", "StartPositionTicks": _ticks(180)},
                 {"Name": "Ending", "StartPositionTicks": _ticks(1320)},
             ],
             "MediaSources": [_media_source(eid)]})
    # Some viewing history: ep-1 half watched, the movie watched, the series a favourite.
    items["ep-1"]["UserData"].update({"PlaybackPositionTicks": _ticks(600), "PlayedPercentage": 41.6,
                                      "LastPlayedDate": "2026-10-05T20:00:00Z"})
    items["movie-1"]["UserData"].update({"Played": True, "PlayCount": 1,
                                         "LastPlayedDate": "2026-10-04T21:00:00Z"})
    items["series-1"]["UserData"]["IsFavorite"] = True
    return views, items


class MockState:
    def __init__(self, cfg: MockConfig):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.log: list[LoggedRequest] = []
        self.users = [
            {"Id": "user-alice", "Name": "Alice", "HasPassword": True, "PrimaryImageTag": "avatar1",
             "_pw": "hunter2"},
            {"Id": "user-guest", "Name": "Guest", "HasPassword": False, "_pw": ""},
        ]
        self.hidden_users = [{"Id": "user-hidden", "Name": "Hidden", "HasPassword": True, "_pw": "secret"}]
        self.tokens: dict[str, str] = {}
        self.qc: dict[str, dict] = {}     # secret -> {code, approved_by, polls}
        self.views, self.items = build_library()
        self.reports: list[tuple[str, dict]] = []   # playback reports

    def all_users(self):
        return self.users + self.hidden_users

    def new_routes(self) -> bool:
        s = self.cfg.route_style
        if s in ("new", "legacy"):
            return s == "new"
        nums = [int(x) for x in re.findall(r"\d+", self.cfg.version)[:2]]
        return nums >= [10, 9]

    def legacy_routes(self) -> bool:
        s = self.cfg.route_style
        if s == "both":
            return True
        if s == "auto":
            return True          # real servers kept legacy routes around for a while
        return s == "legacy"


def _public_user(u: dict) -> dict:
    return {k: v for k, v in u.items() if not k.startswith("_")}


class Handler(BaseHTTPRequestHandler):
    server_version = "MockJellyfin/1.0"
    protocol_version = "HTTP/1.1"
    state: MockState  # set per server class

    def log_message(self, *a):  # quiet
        pass

    # ------------------------------------------------------------------ plumbing
    def _send(self, status: int, body=None, ctype="application/json", headers=None):
        payload = b""
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        if payload or status != 204:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if payload and self.command != "HEAD":
            self.wfile.write(payload)
        self._status = status

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return {}

    def _token(self) -> str | None:
        h = self.headers.get("Authorization") or self.headers.get("X-Emby-Authorization") or ""
        m = re.search(r'Token="([^"]*)"', h)
        return m.group(1) if m else None

    def _user(self) -> dict | None:
        tok = self._token()
        uid = self.state.tokens.get(tok or "")
        return next((u for u in self.state.all_users() if u["Id"] == uid), None)

    def _auth_result(self, user: dict) -> dict:
        tok = secrets.token_hex(16)
        self.state.tokens[tok] = user["Id"]
        return {"AccessToken": tok, "ServerId": self.state.cfg.server_id, "User": _public_user(user),
                "SessionInfo": {"Id": uuid.uuid4().hex}}

    def _handle(self):
        split = urlsplit(self.path)
        query = {k: v for k, v in parse_qs(split.query).items()}
        entry = LoggedRequest(self.command, split.path, query, dict(self.headers))
        with self.state.lock:
            self.state.log.append(entry)
        if self.state.cfg.delay:
            time.sleep(self.state.cfg.delay)
        self._status = 0
        try:
            self._route(split.path, {k: v[-1] for k, v in query.items()}, query)
        except BrokenPipeError:
            return
        entry.status = self._status

    do_GET = do_POST = do_DELETE = do_HEAD = _handle

    # ------------------------------------------------------------------ routes
    def _route(self, path: str, q: dict, qmulti: dict):
        st, cfg = self.state, self.state.cfg
        m = lambda pattern: re.fullmatch(pattern, path)  # noqa: E731
        method = self.command

        # --- public
        if path == "/System/Info/Public":
            return self._send(200, {"Id": cfg.server_id, "ServerName": cfg.name, "Version": cfg.version,
                                    "ProductName": "Jellyfin Server", "LocalAddress": "",
                                    "StartupWizardCompleted": True})
        if path == "/Users/Public":
            return self._send(200, [_public_user(u) for u in st.users])
        if path == "/Users/AuthenticateByName" and method == "POST":
            b = self._body()
            u = next((u for u in st.all_users() if u["Name"].lower() == str(b.get("Username", "")).lower()), None)
            if not u or (b.get("Pw") or "") != u["_pw"]:
                return self._send(401, b"Error processing request.", "text/plain")
            return self._send(200, self._auth_result(u))
        if path == "/QuickConnect/Enabled":
            return self._send(200, cfg.quick_connect)
        if path == "/QuickConnect/Initiate" and method == "POST":
            if not cfg.quick_connect:
                return self._send(401, b"Quick connect is disabled", "text/plain")
            secret, code = secrets.token_hex(32), f"{secrets.randbelow(10**6):06d}"
            st.qc[secret] = {"code": code, "approved_by": None, "polls": 0}
            return self._send(200, {"Secret": secret, "Code": code, "Authenticated": False})
        if path == "/QuickConnect/Connect":
            s = st.qc.get(q.get("secret", ""))
            if not s:
                return self._send(404)
            s["polls"] += 1
            if cfg.qc_auto_approve_after is not None and s["polls"] >= cfg.qc_auto_approve_after:
                s["approved_by"] = s["approved_by"] or "user-alice"
            return self._send(200, {"Secret": q["secret"], "Code": s["code"], "Authenticated": bool(s["approved_by"])})
        if path == "/Users/AuthenticateWithQuickConnect" and method == "POST":
            s = st.qc.pop(self._body().get("Secret", ""), None)
            if not s or not s["approved_by"]:
                return self._send(400, b"Unknown secret", "text/plain")
            u = next(u for u in st.all_users() if u["Id"] == s["approved_by"])
            return self._send(200, self._auth_result(u))
        if m(r"/Users/[^/]+/Images/Primary"):
            return self._send(200, b"\x89PNG\r\n\x1a\nmock", "image/png")
        if m(r"/Items/[^/]+/Images/\w+(/\d+)?"):
            return self._send(200, b"\x89PNG\r\n\x1a\nmock", "image/png")

        # --- everything below needs a token
        user = self._user()
        if user is None:
            return self._send(401, b"Unauthorized", "text/plain")

        if path == "/Users/Me":
            return self._send(200, _public_user(user))
        if path == "/Sessions/Logout" and method == "POST":
            st.tokens.pop(self._token() or "", None)
            return self._send(204)
        if path == "/QuickConnect/Authorize" and method == "POST":
            for s in st.qc.values():
                if s["code"] == q.get("code"):
                    s["approved_by"] = user["Id"]
                    return self._send(200, True)
            return self._send(404)
        if path in ("/Sessions/Playing", "/Sessions/Playing/Progress", "/Sessions/Playing/Stopped") and method == "POST":
            st.reports.append((path, self._body()))
            return self._send(204)

        # --- video: direct play only
        mm = m(r"/Videos/([^/]+)/stream(\.\w+)?")
        if mm:
            if q.get("static", "").lower() != "true" or mm.group(2):
                return self._send(400, b"mock refuses non-static streams", "text/plain")
            if mm.group(1) not in st.items:
                return self._send(404)
            return self._serve_bytes()
        if "master.m3u8" in path or path.endswith(".m3u8"):
            return self._send(400, b"mock refuses HLS", "text/plain")

        # --- user-scoped routes, new and legacy
        new, legacy = st.new_routes(), st.legacy_routes()
        uid = user["Id"]

        def want(new_path: str, legacy_re: str) -> bool:
            if new and path == new_path:
                return True
            lm = m(legacy_re)
            return bool(legacy and lm and lm.group(1) == uid)

        if want("/UserViews", r"/Users/([^/]+)/Views"):
            return self._send(200, {"Items": st.views, "TotalRecordCount": len(st.views)})
        if want("/UserItems/Resume", r"/Users/([^/]+)/Items/Resume"):
            res = [i for i in st.items.values() if i["UserData"]["PlaybackPositionTicks"] > 0]
            return self._send(200, {"Items": res, "TotalRecordCount": len(res)})
        if want("/Items/Latest", r"/Users/([^/]+)/Items/Latest"):
            res = [i for i in st.items.values() if i["Type"] in ("Movie", "Episode")
                   and (not q.get("parentId") or self._under(i, q["parentId"]))]
            res.sort(key=lambda i: i.get("DateCreated", ""), reverse=True)
            return self._send(200, res[: int(q.get("limit", 20))])
        if want("/Items", r"/Users/([^/]+)/Items"):
            return self._send(200, self._query_items(q))
        if path == "/Shows/NextUp":
            nxt = [i for i in st.items.values() if i["Type"] == "Episode" and not i["UserData"]["Played"]
                   and (q.get("enableResumable", "true").lower() == "true"
                        or not i["UserData"]["PlaybackPositionTicks"])][:1]
            return self._send(200, {"Items": nxt, "TotalRecordCount": len(nxt)})

        for new_prefix, legacy_word, key in (("/UserPlayedItems/", "PlayedItems", "Played"),
                                             ("/UserFavoriteItems/", "FavoriteItems", "IsFavorite")):
            iid = None
            if new and path.startswith(new_prefix):
                iid = path[len(new_prefix):]
            lm = m(rf"/Users/([^/]+)/{legacy_word}/([^/]+)")
            if legacy and lm and lm.group(1) == uid:
                iid = lm.group(2)
            if iid is not None:
                it = st.items.get(iid)
                if not it or method not in ("POST", "DELETE"):
                    return self._send(404)
                it["UserData"][key] = method == "POST"
                return self._send(200, it["UserData"])

        mm = m(r"/Items/([^/]+)") if new else None
        lm = m(r"/Users/([^/]+)/Items/([^/]+)") if legacy else None
        iid = mm.group(1) if mm else (lm.group(2) if lm and lm.group(1) == uid else None)
        if iid is not None:
            it = st.items.get(iid) or next((v for v in st.views if v["Id"] == iid), None)
            return self._send(200, it) if it else self._send(404)

        # --- segments
        mm = m(r"/MediaSegments/([^/]+)")
        if mm and cfg.segments == "media-segments" and _vt(cfg.version) >= (10, 10):
            if mm.group(1) not in st.items:
                return self._send(404)
            types = qmulti.get("includeSegmentTypes") or ["Intro", "Outro"]
            segs = [{"Id": uuid.uuid4().hex, "ItemId": mm.group(1), "Type": "Intro",
                     "StartTicks": _ticks(90), "EndTicks": _ticks(180)},
                    {"Id": uuid.uuid4().hex, "ItemId": mm.group(1), "Type": "Outro",
                     "StartTicks": _ticks(1320), "EndTicks": _ticks(1410)}]
            segs = [s for s in segs if s["Type"] in types]
            return self._send(200, {"Items": segs, "TotalRecordCount": len(segs), "StartIndex": 0})
        mm = m(r"/Episode/([^/]+)/IntroSkipperSegments")
        if mm and cfg.segments == "intro-skipper":
            return self._send(200, {
                "Introduction": {"EpisodeId": mm.group(1), "Valid": True, "Start": 90.0, "End": 180.0},
                "Credits": {"EpisodeId": mm.group(1), "Valid": True, "Start": 1320.0, "End": 1410.0}})

        self._send(404)

    # ------------------------------------------------------------------ helpers
    def _under(self, item: dict, parent_id: str) -> bool:
        seen = 0
        while item and seen < 10:
            if item.get("ParentId") == parent_id:
                return True
            item = self.state.items.get(item.get("ParentId", ""))
            seen += 1
        return False

    def _query_items(self, q: dict) -> dict:
        items = list(self.state.items.values())
        if q.get("parentId"):
            if q.get("recursive", "").lower() == "true":
                items = [i for i in items if self._under(i, q["parentId"])]
            else:
                items = [i for i in items if i.get("ParentId") == q["parentId"]]
        if q.get("includeItemTypes"):
            types = set(q["includeItemTypes"].split(","))
            items = [i for i in items if i["Type"] in types]
        filters = set((q.get("filters") or "").split(",")) - {""}
        if "IsPlayed" in filters:
            items = [i for i in items if i["UserData"]["Played"]]
        if "IsUnplayed" in filters:
            items = [i for i in items if not i["UserData"]["Played"]]
        if "IsFavorite" in filters:
            items = [i for i in items if i["UserData"]["IsFavorite"]]
        if q.get("searchTerm"):
            items = [i for i in items if q["searchTerm"].lower() in i["Name"].lower()]
        if q.get("nameStartsWith"):
            items = [i for i in items if i["Name"].lower().startswith(q["nameStartsWith"].lower())]
        key = (q.get("sortBy") or "SortName").split(",")[0]
        if key == "DatePlayed":
            items.sort(key=lambda i: i["UserData"].get("LastPlayedDate") or "",
                       reverse=(q.get("sortOrder") == "Descending"))
            key = "_keep"
        field_map = {"SortName": "Name", "Name": "Name", "DateCreated": "DateCreated",
                     "PremiereDate": "PremiereDate", "ProductionYear": "ProductionYear"}
        if key != "_keep":
            f = field_map.get(key, "Name")
            items.sort(key=lambda i: str(i.get(f, "")), reverse=(q.get("sortOrder") == "Descending"))
        total = len(items)
        start = int(q.get("startIndex", 0))
        limit = int(q.get("limit", total or 1))
        return {"Items": items[start:start + limit], "TotalRecordCount": total, "StartIndex": start}

    def _serve_bytes(self):
        data = STREAM_BYTES
        rng = self.headers.get("Range")
        if rng and self.state.cfg.honor_range:
            mr = re.fullmatch(r"bytes=(\d+)-(\d*)", rng.strip())
            if mr:
                start = int(mr.group(1))
                end = int(mr.group(2)) if mr.group(2) else len(data) - 1
                end = min(end, len(data) - 1)
                return self._send(206, data[start:end + 1], "video/x-matroska",
                                  {"Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes"})
        return self._send(200, data, "video/x-matroska", {"Accept-Ranges": "bytes"})


def _vt(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


class MockJellyfin:
    """Context manager: `with MockJellyfin(MockConfig(...)) as srv: srv.url`."""

    def __init__(self, cfg: MockConfig | None = None, port: int = 0, host: str = "127.0.0.1"):
        self.state = MockState(cfg or MockConfig())
        handler = type("BoundHandler", (Handler,), {"state": self.state})
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def cfg(self) -> MockConfig:
        return self.state.cfg

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def log(self) -> list[LoggedRequest]:
        return self.state.log

    def start(self) -> "MockJellyfin":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8096)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--version", default="10.10.7")
    ap.add_argument("--legacy", action="store_true", help="only legacy /Users/{id}/... routes")
    ap.add_argument("--server-id", default="mockserver0000000000000000000000")
    a = ap.parse_args()
    cfg = MockConfig(server_id=a.server_id, version=a.version, route_style="legacy" if a.legacy else "auto")
    srv = MockJellyfin(cfg, port=a.port, host=a.host).start()
    print(f"mock Jellyfin {cfg.version} at {srv.url}  (users: Alice/hunter2, Guest/blank)")
    try:
        srv.thread.join()
    except KeyboardInterrupt:
        srv.stop()
