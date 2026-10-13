"""Build the mpv command line and the IPC socket path.

The access token is never on the command line (it would show in `ps`);
the controller hands it to mpv over IPC as an HTTP header instead.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

PLAYER_APP_ID = "bloomsarchive-player"


def socket_path(tag: str = "") -> str:
    """Unix socket under $XDG_RUNTIME_DIR, or a named pipe name on Windows."""
    name = f"bloomsarchive-mpv-{os.getpid()}{('-' + tag) if tag else ''}"
    if sys.platform == "win32":
        return name                                 # QLocalSocket adds \\.\pipe\
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return str(Path(base) / f"{name}.sock")


def mpv_ipc_arg(sock: str) -> str:
    return f"\\\\.\\pipe\\{sock}" if sys.platform == "win32" else sock


def find_mpv(configured: str | None) -> str | None:
    path = (configured or "mpv").strip()
    if os.path.sep in path or (sys.platform == "win32" and "/" in path):
        return path if Path(path).exists() else None
    return shutil.which(path)


def _extra(args) -> list[str]:
    if not args:
        return []
    if isinstance(args, str):
        return shlex.split(args)
    return [str(a) for a in args]


def build_args(mpv_settings: dict, sock: str, script: str | None = None) -> list[str]:
    """mpv arguments for one playback session (without the program itself)."""
    args = [
        "--idle=yes",
        "--force-window=immediate",
        "--fs",
        f"--input-ipc-server={mpv_ipc_arg(sock)}",
        f"--wayland-app-id={PLAYER_APP_ID}",
        "--title=${media-title}",
        "--keep-open=no",
        # Jellyfin is the source of truth for resume points:
        "--resume-playback=no",
        "--save-position-on-quit=no",
    ]
    if not mpv_settings.get("inherit_user_config", True):
        args.append("--no-config")
    if script and mpv_settings.get("use_bloom_ui", True):
        args += ["--osc=no", f"--script={script}"]
    args += _extra(mpv_settings.get("extra_args"))
    return args
