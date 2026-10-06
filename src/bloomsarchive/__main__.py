"""Entry point: `python -m bloomsarchive` or the `bloomsarchive` script."""
from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bloomsarchive", description="Bloom's Archive — Jellyfin desktop client")
    p.add_argument("--selfcheck", action="store_true", help="check the connection to your server and exit")
    p.add_argument("--pin", action="store_true", help="with --selfcheck: pin the found ServerId without asking")
    p.add_argument("--user", help="with --selfcheck: sign in as this user for the signed-in checks")
    p.add_argument("--debug", action="store_true", help="verbose logging")
    args = p.parse_args(argv)

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
