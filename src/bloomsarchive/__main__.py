"""Entry point: `python -m bloomsarchive` or the `bloomsarchive` script."""
from __future__ import annotations

import argparse
import logging
import sys


_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"


_INVISIBLE = "\u00a0\u2007\u202f\u200b\u200c\u200d\u2060\ufeff"


def _normalize_dashes(argv: list[str]) -> list[str]:
    """Commands pasted from rendered chat/markdown can carry typographic dashes and
    invisible characters (non-breaking or zero-width spaces). Clean both."""
    out = []
    for a in argv:
        a = a.strip(_INVISIBLE + " \t")
        if not a:
            continue
        i = 0
        while i < len(a) and a[i] in _DASHES + "-":
            i += 1
        lead = a[:i]
        if lead and any(c in _DASHES for c in lead):
            # an em/en dash stands for "--"; hyphen-like singles count as one "-"
            n = sum(2 if c in "\u2013\u2014" else 1 for c in lead)
            a = "-" * min(n, 2) + a[i:]
        out.append(a)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bloomsarchive", description="Bloom's Archive — Jellyfin desktop client")
    p.add_argument("--selfcheck", action="store_true", help="check the connection to your server and exit")
    p.add_argument("--pin", action="store_true", help="with --selfcheck: pin the found ServerId without asking")
    p.add_argument("--user", help="with --selfcheck: sign in as this user for the signed-in checks")
    p.add_argument("--install-desktop", action="store_true", help="add a launcher entry (and fix the portal app-id warning)")
    p.add_argument("--uninstall-desktop", action="store_true", help="remove the launcher entry")
    p.add_argument("--debug", action="store_true", help="verbose logging")
    args = p.parse_args(_normalize_dashes(sys.argv[1:] if argv is None else argv))

    if args.install_desktop or args.uninstall_desktop:
        from . import desktop
        if args.install_desktop:
            print(f"installed {desktop.install()}")
        else:
            print("removed" if desktop.uninstall() else "nothing to remove")
        return 0

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.selfcheck:
        from .selfcheck import run
        return run(user=args.user, pin=args.pin)

    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("PySide6 is not installed. On Arch: sudo pacman -S pyside6\n"
              "(--selfcheck works without it.)", file=sys.stderr)
        return 2
    from .app import run_app
    return run_app(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
