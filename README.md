# Bloom's Archive

A desktop client for [Jellyfin](https://jellyfin.org) media servers. Browse with a PySide6 UI and play through [mpv](https://mpv.io), **direct play only**: the server sends the original file and never transcodes, so even a modest server can stream anything it stores.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Skeleton, config, theme, API client, discovery, auth, `--selfcheck` | done |
| 2 | Login screen (profiles, password, manual login, Quick Connect), switch user, log out | done |
| 3 | Shell (sidebar, back/forward, search box), home page with all rows, wide window on Hyprland | done |
| 4 | Library grid with sort, filters, lazy loading; played/favourite toggles everywhere | next |
| 5–9 | Details, mpv player, Lua UI, skip/up-next, settings | to do |

Phase 1 also includes the segment fetcher (`player/segments.py`) because `--selfcheck` reports which skip-intro source your server has.

## Install (Arch Linux)

```sh
sudo pacman -S pyside6 mpv python-keyring   # keyring is optional
cd bloomsarchive
python -m venv --system-site-packages .venv
source .venv/bin/activate.fish        # fish; bash/zsh: source .venv/bin/activate
pip install -e .
bloomsarchive --install-desktop       # launcher entry; also fixes the portal app-id warning
```

Other distributions: install mpv and PySide6 6.5 or newer from your package manager (or `pip install PySide6` inside the venv).

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

Startup finds the server (last working address first, then a background re-probe that switches to a better one), asks you to confirm the server on first run, and signs you straight in if your stored token is still valid. Otherwise you get the login screen:

- **Profiles** — click your avatar. Users without a password sign in immediately; a user whose token is still stored (after *Switch user*) switches back without a password.
- **Quick Connect** — shows a 6-digit code; approve it from any signed-in Jellyfin session (profile menu → Quick Connect). Expires after 5 minutes.
- **Manual login** — for users hidden from the profile list.
- *Switch user* keeps your token; *Log out* revokes it on the server. If the server ever rejects your token, you're sent back to sign in.

## Home and navigation

- **Rows:** Libraries, Continue Watching, Next Up, Recently Added (one per library; Collections/playlists skipped), Watch History, Favourites. Each loads independently; empty rows hide.
- **Cards:** progress bar on in-progress items, a tick on watched ones, an unwatched-count badge on series, a heart on favourites.
- **Right-click a card** to mark watched/unwatched or (un)favourite. The card updates at once and reverts if the server refuses.
- **Scrolling a row:** the ‹ › arrows on hover, Shift+wheel, or a sideways trackpad swipe. Plain wheel scrolls the page.
- **Keys:** Alt+← / Alt+→ or the mouse back/forward buttons, F5 refresh, Ctrl+F search.

### Window shape on Hyprland

Tiling gives a new window whatever slot the layout has, often a tall column. On Hyprland the app floats itself at 16:9 (about 80% of the monitor) and centres itself, using `hyprctl`. To keep it tiled instead, set `"window": {"hyprland_float": false}` in `settings.json`. If you add your own window rule that floats it wide, the app leaves it alone.

## Files

- Settings: `~/.config/bloomsarchive/settings.json` (mode 0600)
- Tokens: Secret Service via keyring, else `settings.json`
- Image cache: `~/.cache/bloomsarchive/img`

## Development

```sh
python tests/mock_jellyfin.py --port 8096            # fake server: Alice/hunter2, Guest/blank, Hidden/secret
# try the app against it without touching your real settings:
BLOOMSARCHIVE_CONFIG_DIR=/tmp/ba-mock bloomsarchive   # then set the LAN address to http://127.0.0.1:8096 in /tmp/ba-mock/settings.json
python tests/mock_jellyfin.py --port 8096 --legacy --version 10.8.13
cd tests && python -m unittest                       # or: pytest
```

The mock logs every request, and the tests assert that no HLS or non-static stream URL is ever requested.
