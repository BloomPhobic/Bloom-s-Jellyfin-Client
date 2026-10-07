"""Install a .desktop entry so the app shows in launchers.

It also silences Qt's "Could not register app ID … App info not found" portal
warning: the XDG portal looks up <app-id>.desktop for the Wayland app id.
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from . import APP_ID, APP_NAME


def applications_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "applications"


def desktop_entry(python: str | None = None) -> str:
    exe = shlex.quote(python or sys.executable)
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "GenericName=Media Player",
        "Comment=Browse and direct-play your Jellyfin library",
        f"Exec={exe} -m {APP_ID}",
        "Icon=multimedia-video-player",
        "Terminal=false",
        "Categories=AudioVideo;Video;Player;",
        f"StartupWMClass={APP_ID}",
        "Keywords=jellyfin;anime;movies;mpv;",
        "",
    ])


def install(python: str | None = None) -> Path:
    d = applications_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{APP_ID}.desktop"
    path.write_text(desktop_entry(python), encoding="utf-8")
    return path


def uninstall() -> bool:
    path = applications_dir() / f"{APP_ID}.desktop"
    if path.exists():
        path.unlink()
        return True
    return False
