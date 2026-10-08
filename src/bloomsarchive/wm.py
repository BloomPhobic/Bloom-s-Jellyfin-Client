"""Window-manager helpers. On Hyprland, make the main window a wide, centred float.

Wayland apps can't place or size their own windows, and a tiling compositor
gives a new window whatever slot the layout has (often a tall column). Hyprland
exposes hyprctl, so on first show we look ourselves up by PID and, if the
window is tiled or taller than wide, float it at 16:9 and centre it on its
monitor. A user window rule that already floats us wide is left alone.

The geometry maths is pure so it can be tested without Hyprland.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

ASPECT = 16 / 9
WIDTH_FRACTION = 0.80     # of the monitor's usable width
HEIGHT_FRACTION = 0.86    # never taller than this share of usable height


@dataclass
class Geometry:
    x: int
    y: int
    w: int
    h: int


def is_hyprland() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and shutil.which("hyprctl") is not None


def target_geometry(monitor: dict, width_fraction: float = WIDTH_FRACTION,
                    height_fraction: float = HEIGHT_FRACTION) -> Geometry:
    """16:9 box centred in the monitor's usable area, in Hyprland layout coordinates."""
    scale = float(monitor.get("scale") or 1.0)
    pw, ph = int(monitor["width"]), int(monitor["height"])
    if int(monitor.get("transform", 0)) % 2 == 1:       # rotated 90/270
        pw, ph = ph, pw
    lw, lh = pw / scale, ph / scale
    # reserved = [left, top, right, bottom] (bars, docks) in layout pixels
    rl, rt, rr, rb = (list(monitor.get("reserved") or [0, 0, 0, 0]) + [0, 0, 0, 0])[:4]
    uw, uh = max(200.0, lw - rl - rr), max(200.0, lh - rt - rb)
    w = min(uw * width_fraction, uh * height_fraction * ASPECT)
    h = w / ASPECT
    x = monitor.get("x", 0) + rl + (uw - w) / 2
    y = monitor.get("y", 0) + rt + (uh - h) / 2
    return Geometry(int(round(x)), int(round(y)), int(round(w)), int(round(h)))


def needs_fix(client: dict) -> bool:
    """Tiled, or floating but taller than wide (e.g. a rule we don't control)."""
    if not client.get("floating"):
        return True
    w, h = (client.get("size") or [0, 0])[:2]
    return h > w


def batch_commands(address: str, geo: Geometry, floating: bool) -> list[str]:
    win = f"address:{address}"
    cmds = []
    if not floating:
        cmds.append(f"dispatch setfloating {win}")
    cmds.append(f"dispatch resizewindowpixel exact {geo.w} {geo.h},{win}")
    cmds.append(f"dispatch movewindowpixel exact {geo.x} {geo.y},{win}")
    return cmds


def find_own_client(clients: list[dict], pid: int, klass: str | None = None) -> dict | None:
    mine = [c for c in clients if c.get("pid") == pid and c.get("mapped", True)]
    if klass:
        exact = [c for c in mine if c.get("class") == klass]
        mine = exact or mine
    return mine[0] if mine else None


# --------------------------------------------------------------------------- side effects

def _hyprctl_json(*args: str) -> object:
    out = subprocess.run(["hyprctl", *args, "-j"], capture_output=True, text=True, timeout=3, check=True)
    return json.loads(out.stdout)


def make_window_wide(klass: str, pid: int | None = None) -> str:
    """Float + 16:9 + centre our window on Hyprland. Returns a short log message."""
    if not is_hyprland():
        return "not Hyprland"
    pid = pid or os.getpid()
    try:
        clients = _hyprctl_json("clients")
        client = find_own_client(clients if isinstance(clients, list) else [], pid, klass)
        if not client:
            return "own window not found"
        if not needs_fix(client):
            return "already floating wide"
        monitors = _hyprctl_json("monitors")
        mon = next((m for m in monitors if m.get("id") == client.get("monitor")), None) \
            if isinstance(monitors, list) else None
        if not mon:
            return "monitor not found"
        geo = target_geometry(mon)
        cmds = batch_commands(client["address"], geo, bool(client.get("floating")))
        subprocess.run(["hyprctl", "--batch", " ; ".join(cmds)], capture_output=True,
                       text=True, timeout=3, check=True)
        return f"floated at {geo.w}x{geo.h}+{geo.x}+{geo.y}"
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as e:
        log.debug("hyprland sizing failed: %s", e)
        return f"failed: {e}"
