"""Pick the best reachable address for the server.

Probes GET /System/Info/Public on every candidate in parallel, rejects any
server whose Id doesn't match the pinned ServerId (a shared network may have a
different device at 192.168.1.50), and returns the highest-priority match as
soon as every higher-priority candidate has answered or failed. If the LAN
answers first, startup costs one fast request.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from .client import JellyfinClient, normalize_base_url
from .models import ServerInfo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    url: str
    label: str = ""
    public: bool = False
    priority: int = 0          # lower = preferred

    @classmethod
    def from_settings(cls, addresses: list[dict]) -> list["Candidate"]:
        out = []
        for i, a in enumerate(addresses):
            url = normalize_base_url(a.get("url", ""))
            if url:
                out.append(cls(url=url, label=a.get("label", ""), public=bool(a.get("public")), priority=i))
        return out


@dataclass
class ProbeResult:
    candidate: Candidate
    ok: bool
    info: ServerInfo | None = None
    latency_ms: float | None = None
    error: str = ""
    id_match: bool | None = None   # None when no ServerId is pinned yet

    @property
    def usable(self) -> bool:
        return self.ok and self.id_match is not False

    def describe(self) -> str:
        c = self.candidate
        name = f"{c.label} ({c.url})" if c.label else c.url
        if not self.ok:
            return f"{name}: unreachable — {self.error}"
        assert self.info
        base = f"{name}: {self.info.name or '?'} v{self.info.version or '?'} in {self.latency_ms:.0f} ms"
        if self.id_match is False:
            return f"{base} — REJECTED, ServerId {self.info.id} is not the pinned server"
        return base


@dataclass
class Discovery:
    chosen: ProbeResult | None
    results: list[ProbeResult] = field(default_factory=list)
    reason: str = ""
    needs_pin: bool = False        # no ServerId pinned yet: confirm and pin chosen.info.id

    @property
    def base_url(self) -> str | None:
        return self.chosen.candidate.url if self.chosen else None

    @property
    def is_public(self) -> bool:
        return bool(self.chosen and self.chosen.candidate.public)


def probe(candidate: Candidate, expected_server_id: str | None, device_id: str,
          timeout: float) -> ProbeResult:
    client = JellyfinClient(candidate.url, device_id=device_id, timeout=timeout)
    t0 = time.monotonic()
    try:
        raw = client.public_info(timeout=timeout)
    except Exception as e:
        return ProbeResult(candidate, ok=False, error=str(e) or type(e).__name__)
    latency = (time.monotonic() - t0) * 1000
    info = ServerInfo.from_json(raw)
    match = None if not expected_server_id else (info.id == expected_server_id)
    return ProbeResult(candidate, ok=True, info=info, latency_ms=latency, id_match=match)


def _timeout_for(c: Candidate, private_timeout: float, public_timeout: float) -> float:
    return public_timeout if c.public else private_timeout


def discover(candidates: list[Candidate], expected_server_id: str | None, device_id: str,
             last_good: str | None = None, private_timeout: float = 1.5,
             public_timeout: float = 3.0) -> Discovery:
    """Choose an address. See module docstring.

    If `last_good` is given and still answers with the right ServerId, it is
    returned straight away; the app should then call discover() again without
    last_good in the background and switch if a better address wins.
    """
    if not candidates:
        return Discovery(None, reason="No addresses configured")

    if last_good and expected_server_id:
        lg = normalize_base_url(last_good)
        cand = next((c for c in candidates if c.url == lg), None)
        if cand:
            r = probe(cand, expected_server_id, device_id, _timeout_for(cand, private_timeout, public_timeout))
            if r.usable:
                return Discovery(r, [r], reason=f"last working address still answers ({cand.label or cand.url})")

    ordered = sorted(candidates, key=lambda c: c.priority)
    results: dict[Candidate, ProbeResult] = {}
    pool = ThreadPoolExecutor(max_workers=len(ordered), thread_name_prefix="probe")
    futures: dict[Future, Candidate] = {
        pool.submit(probe, c, expected_server_id, device_id,
                    _timeout_for(c, private_timeout, public_timeout)): c
        for c in ordered
    }
    pending = set(futures)
    try:
        while True:
            # Walk priorities in order: accept the first usable one whose
            # betters have all resolved; stop at the first still-pending one.
            for c in ordered:
                r = results.get(c)
                if r is None:
                    break
                if r.usable:
                    skipped = [results[o] for o in ordered if o.priority < c.priority]
                    reason = _reason(r, skipped, expected_server_id)
                    return Discovery(r, _in_order(ordered, results), reason,
                                     needs_pin=expected_server_id is None)
            else:
                return Discovery(None, _in_order(ordered, results),
                                 reason="No address reached the server" +
                                 ("" if expected_server_id is None else " with the pinned ServerId"))
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for f in done:
                results[futures[f]] = f.result()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _in_order(ordered: list[Candidate], results: dict[Candidate, ProbeResult]) -> list[ProbeResult]:
    return [results[c] for c in ordered if c in results]


def _reason(chosen: ProbeResult, skipped: list[ProbeResult], expected: str | None) -> str:
    label = chosen.candidate.label or chosen.candidate.url
    if not skipped:
        why = f"{label} is the preferred address and answered"
    else:
        notes = []
        for s in skipped:
            sl = s.candidate.label or s.candidate.url
            notes.append(f"{sl} wrong server" if s.ok and s.id_match is False else f"{sl} unreachable")
        why = f"{label} chosen ({'; '.join(notes)})"
    if expected is None:
        why += " — no ServerId pinned yet"
    return why
