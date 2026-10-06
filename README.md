# Bloom's Archive

A desktop client for [Jellyfin](https://jellyfin.org) media servers. Browse with a PySide6 UI and play through [mpv](https://mpv.io), **direct play only**: the server sends the original file and never transcodes, so even a modest server can stream anything it stores.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Skeleton, config, theme, API client, discovery, auth, `--selfcheck` | done |
| 2 | Login screen (public users, password, Quick Connect) | next |
| 3–9 | Home, library grid, details, mpv player, Lua UI, skip/up-next, settings | to do |

Phase 1 also includes the segment fetcher (`player/segments.py`) because `--selfcheck` reports which skip-intro source your server has.

## Install (Arch Linux)

```sh
sudo pacman -S pyside6 mpv python-keyring   # keyring is optional
cd bloomsarchive
python -m venv --system-site-packages .venv && . .venv/bin/activate
pip install -e .
```

Other distributions: install mpv and PySide6 6.5 or newer from your package manager (or `pip install PySide6` inside the venv).

The API layer is standard-library only, so `--selfcheck` works even without PySide6.

## Point it at your server

The app tries a list of addresses for your server in order and uses the first one that answers. They're in
`~/.config/bloomsarchive/settings.json` (created on first run) under `addresses`:

```json
"addresses": [
  {"label": "Home LAN",  "url": "http://192.168.1.50:8096",     "public": false},
  {"label": "Tailscale", "url": "http://100.64.0.10:8096",      "public": false},
  {"label": "Public",    "url": "https://jellyfin.example.com", "public": true}
]
```

Replace these examples with your own (one address is enough). `"public": true` marks an address reached over
the internet: it's tried last with a longer timeout, and by default video won't stream over it
(`network.never_stream_public`), so a slow or metered tunnel is only used for browsing.

## First run: self-check

```sh
bloomsarchive --selfcheck          # asks to pin your ServerId and to sign in
bloomsarchive --selfcheck --pin --user Alice
```

It prints which address won and why, the Jellyfin version, Quick Connect status, public users, which API route style works, which skip-segment source works, and does one 64 KB `static=true` byte-range request. If anything is flagged, include the whole output when you report a problem.

Pinning the ServerId matters on shared networks (dorms, offices, public Wi-Fi): another device may answer at your server's LAN address, and the app refuses to talk to anything whose Jellyfin `Id` doesn't match.

## Run the app

```sh
bloomsarchive            # or: python -m bloomsarchive --debug
```

Phase 1 opens the branded startup screen: it finds the server (last working address first, then a background re-probe that switches to a better address), asks you to confirm the server on first run, and restores a stored login.

## Files

- Settings: `~/.config/bloomsarchive/settings.json` (mode 0600)
- Tokens: Secret Service via keyring, else `settings.json`
- Image cache: `~/.cache/bloomsarchive/img`

## Development

```sh
python tests/mock_jellyfin.py --port 8096            # fake server: Alice/hunter2, Guest/blank
python tests/mock_jellyfin.py --port 8096 --legacy --version 10.8.13
cd tests && python -m unittest                       # or: pytest
```

The mock logs every request, and the tests assert that no HLS or non-static stream URL is ever requested.
