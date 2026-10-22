# Bloom's Archive

A desktop client for [Jellyfin](https://jellyfin.org) media servers. Browse with a PySide6 UI and play through [mpv](https://mpv.io), **direct play only**: the server sends the original file and never transcodes, so even a modest server can stream anything it stores.

Linux first (tested on Arch with Hyprland and other Wayland/X11 desktops); needs Jellyfin 10.8 or newer.

**Highlights**
- Home rows, library grids with sort/filters/A–Z jump, live search and detail pages
- Plays the original file in mpv: no transcoding, your own mpv.conf, your audio/subtitle choices remembered per series
- On-screen controls inside mpv, Skip Intro/Credits, Up Next with countdown, "Still watching?"
- Several server addresses (home, VPN, public) with automatic fail-over, pinned to your server's ID
- Optionally refuses to stream video over the public address, so a slow tunnel is only used for browsing

## Build phases

| Phase | What | State |
|---|---|---|
| 1 | Skeleton, config, theme, API client, discovery, auth, `--selfcheck` | done |
| 2 | Login screen (profiles, password, manual login, Quick Connect), switch user, log out | done |
| 3 | Shell (sidebar, back/forward, search box), home page with all rows, wide window on Hyprland | done |
| 4 | Library grid (sort, filters, A–Z jump, lazy paging), Favourites, live search, hover ✓/♥, smooth scrolling | done |
| 5 | Detail pages, track pickers, seasons and episodes | done |
| 6 | mpv playback: launch, IPC, resume, track selection, progress reporting | done |
| 7 | In-player controls (bloom.lua): transport, menus, episode browser, style memory, basic autoplay | done |
| 8 | Skip intro/credits, Up Next card with countdown, "Still watching?" | done |
| 9 | Settings page | done |

Phase 1 also includes the segment fetcher (`player/segments.py`), because `--selfcheck` reports which skip-intro source your server has.

## Install (Arch Linux)

```sh
sudo pacman -S pyside6 mpv python-keyring   # keyring is optional
cd bloomsarchive
python -m venv --system-site-packages .venv
source .venv/bin/activate.fish        # fish; bash/zsh: source .venv/bin/activate
pip install -e .
bloomsarchive --install-desktop       # launcher entry; also fixes the portal app-id warning
#   (or, with no flags to mistype:  python -m bloomsarchive.desktop)
```

Other distributions: install mpv and PySide6 6.5 or newer from your package manager (or `pip install PySide6` inside the venv).

Other distributions: install mpv and PySide6 6.5 or newer from your package manager (or `pip install PySide6` inside the venv).

Other distributions: install mpv and PySide6 6.5 or newer from your package manager (or `pip install PySide6` inside the venv).

The API layer is standard-library only, so `--selfcheck` works even without PySide6.

## Point it at your server

On first start the app asks for your server's address, for example `http://192.168.1.50:8096` at home or `https://jellyfin.example.com`, then asks you to confirm it's your server.

You can give it more than one address in *Settings → Server & connection* (or `addresses` in `~/.config/bloomsarchive/settings.json`). They're tried in order and the first that answers wins, so one list works at home, over a VPN like Tailscale, and away:

```json
"addresses": [
  {"label": "Home LAN",  "url": "http://192.168.1.50:8096",     "public": false},
  {"label": "Tailscale", "url": "http://100.64.0.10:8096",      "public": false},
  {"label": "Public",    "url": "https://jellyfin.example.com", "public": true}
]
```

`"public": true` marks an address reached over the internet: it's tried last with a longer timeout, and by default video won't stream over it, so a slow or metered tunnel is only used for browsing.

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
- **Quick Connect** — shows a 6-digit code; approve it from any signed-in Jellyfin session (profile menu → Quick Connect), or from *Settings → Account* in this app on another computer. Expires after 5 minutes.
- **Manual login** — for users hidden from the profile list.
- *Switch user* keeps your token; *Log out* revokes it on the server. If the server ever rejects your token, you're sent back to sign in.

## Home and navigation

- **Rows:** Libraries, Continue Watching, Next Up, Recently Added (one per library; Collections/playlists skipped), Watch History, Favourites. Each loads independently; empty rows hide.
- **Cards:** progress bar on in-progress items, a tick on watched ones, an unwatched-count badge on series, a heart on favourites.
- **Right-click a card** to mark watched/unwatched or (un)favourite. The card updates at once and reverts if the server refuses.
- **Scrolling a row:** the ‹ › arrows on hover, Shift+wheel, or a sideways trackpad swipe. Plain wheel scrolls the page.
- **Keys:** Alt+← / Alt+→ or the mouse back/forward buttons, F5 refresh, Ctrl+F search.

## Libraries, Favourites, Search

- **Grid:** one poster per title (series only in TV/Anime libraries). It loads 60 at a time as you scroll, so big libraries stay fast; the scrollbar is right from the start.
- **Sort:** Name, Date added, Release date, Rating, Date played, Runtime, Random — each reversible (the order button reshuffles Random).
- **Filters:** All / Unwatched / Watched, Favourites, Genres and Years (pick several; years are grouped by decade). *Clear filters* resets them.
- **A–Z bar:** jumps to the first title under a letter; available when sorted by Name, and it respects your filters.
- Sort and filters are remembered per library. Collections open as their own grid.
- **Hover a poster** for ✓ (watched) and ♥ (favourite) buttons — they glow when pointed at, say what they'll do, and pop when clicked. Right-click works too.
- **Years** filter: pick one or several years (newest or oldest first); sort by **Year** for a year-ordered grid.
- **Search** updates as you type (from two characters): Movies, Shows and Episodes rows.
- **Smooth scrolling:** mouse-wheel notches glide instead of jumping; touchpads keep their native feel.

## Detail pages

- **Movies and episodes:** backdrop, poster or still, year/runtime/rating, genres, tagline, overview, director and cast; **Play / Resume from mm:ss**, **Start over**, **Watched**, **Favourite**.
- **Pickers:** Version (when there's more than one file), Audio and Subtitles. Defaults: what you chose last time for that series, then your preferred languages (*Settings → Languages*; Japanese audio + English subtitles by default), then the file's own flags. Dubbed audio in a language you read gets forced/signs subtitles only.
- **Series:** opens on the season of your Next Up episode. The Play button plays that episode. Each episode row has a thumbnail with progress, a ▶ on hover, and a watched toggle.
- If a server leaves stream lists out of item responses, the app asks `PlaybackInfo` with transcoding and stream-copy disabled; it's information only and never starts a transcode.
- **Genre tags** open the item's own library filtered to that genre (your sort is kept).

## Playback (mpv)

- **Play** opens mpv fullscreen in its own window (Wayland app id `bloomsarchive-player`, for Hyprland rules), streaming the original file with `static=true` — direct play only; if mpv can't open it you get a clear error, never a transcode.
- It starts **paused for a moment** so your audio/subtitle choice is applied before anything is heard (Jellyfin stream indexes are matched to mpv tracks via `ff-index`; external subtitles are added from the server).
- **Resume** starts a few seconds before where you stopped (default 5 s, in Settings).
- Progress goes back to Jellyfin every 10 s and immediately on pause, seek and track changes; a final report when the episode ends, when you quit mpv, or if it crashes. Reports that fail are re-sent. Past the watched threshold (default 90 %) the item is marked watched.
- Your own `~/.config/mpv/mpv.conf` applies (*Use my mpv.conf* in Settings, on by default), except resume points: Jellyfin's win over mpv's watch-later.
- The access token reaches mpv over its IPC socket as an HTTP header, never on the command line.
- After playback, Home, grids and detail pages refresh their progress and ticks.

## In-player controls

Move the mouse and Jellyfin-style controls appear over the video (they hide after 2.5 s, and stay while paused). Your own mpv keybinds keep working.

- **Top:** ← back to the app, series · episode and title.
- **Seek bar:** click or drag; hover shows the time and chapter; lighter part = buffered; ticks = chapters.
- **Left:** previous episode, −10 s, play/pause, +30 s, next episode, time, *Ends at*.
- **Right:** ♥ favourite, episode browser (seasons → episodes, ✓ watched, jump to any), audio, subtitles (CC), subtitle style (Aa: size, bold, font, outline, position, how styled ASS subs are treated), SYNC (subtitle timing: ±0.1 s / ±1 s, reset; the button shows the current offset, and it resets for the next file), version (if several files), AUTO (autoplay next episode), volume + mute, fullscreen.
- Clicking a button again closes its menu.
- Click the picture to pause; double-click for fullscreen; Esc closes a menu.
- Audio/subtitle choices made in the player are remembered for the series; subtitle style changes are remembered for everything (only what you changed — the rest still comes from your mpv.conf).
- **Skip Intro / Skip Credits** appear (even with the controls hidden) while Jellyfin's media segments — or the Intro Skipper plugin, or chapter names — say an intro/credits/recap is playing. In *Settings → Playback*, intros and credits each choose: show a button, skip automatically, or do nothing.
- **Up Next:** when the credits start (or in the last 20 s; adjustable in Settings) a card shows the next episode with **Play now · 20** counting down and **Cancel**. With AUTO off it waits for you. Reaching Up Next counts the episode as watched.
- **Still watching?** After 5 episodes in a row that started by themselves with no clicks, pauses or seeks (adjustable, or *Never ask*, in Settings), the card asks instead of counting down; with no answer, playback stops at the end of the episode.
- Prefer your own mpv UI (uosc etc.)? Turn off *Settings → Player → Bloom controls*.

### Window shape on Hyprland

Tiling gives a new window whatever slot the layout has, often a tall column. On Hyprland the app floats itself at 16:9 (about 80% of the monitor) and centres itself, using `hyprctl`. To keep it tiled instead, turn off *Settings → App → Wide floating window on Hyprland*. If you add your own window rule that floats it wide, the app leaves it alone.

## Settings

Everything saves as soon as you change it. Open the page from the bottom of the sidebar; the chips at the top jump to a section.

- **Server & connection:** which server and address you're on (with latency), *Re-check now*, the address list (edit, add, reorder, mark public, **Test** each one, then *Save addresses*), *Never stream video over the public address*, and *Forget server…* (asks "Is this your server?" again; your sign-in is kept).
- **Account:** switch user, log out, and **approve a Quick Connect code** for another device.
- **Playback:** autoplay, when Up Next appears (5–60 s), *Still watching?* after 2–10 episodes or never, **intros** and **credits** each as *Show a Skip button / Skip automatically / Do nothing*, resume rewind, and the watched threshold.
- **Languages:** audio and subtitle languages in order (add, reorder, remove), what to do when the audio is already in a subtitle language (none / forced only / full), and the series with remembered tracks (forget some or all).
- **Subtitle appearance:** size, bold, font, outline, position and styled-ASS handling, with a live preview. It's the same setting as the player's Aa menu, so a change in either shows up in the other. *Use my mpv.conf instead* drops your changes.
- **Player:** mpv program (with *Detect* and its version), extra mpv options, use my mpv.conf, Bloom controls, start fullscreen. These apply the next time a video starts.
- **App:** smooth scrolling (applies at once), wide floating window on Hyprland.
- **Storage & about:** image-cache size and *Clear*, open the settings folder, install the launcher entry, **Reset all settings** (keeps server, addresses and sign-in), versions, and an in-app **self-check** with *Copy report*.

Settings saved by 0.8 (`playback.skip_mode`) are converted automatically.

## Files

- Settings: `~/.config/bloomsarchive/settings.json` (mode 0600)
- Tokens: Secret Service via keyring, else `settings.json`
- Image cache: `~/.cache/bloomsarchive/img`

## Development

```sh
python tests/mock_jellyfin.py --port 8096            # fake server: Alice/hunter2, Guest/blank, Hidden/secret
# try the app against it without touching your real settings:
BLOOMSARCHIVE_CONFIG_DIR=/tmp/ba-mock bloomsarchive   # then enter http://127.0.0.1:8096 on the first-run screen
python tests/mock_jellyfin.py --port 8096 --legacy --version 10.8.13
cd tests && python -m unittest                       # or: pytest
```

The mock logs every request, and the tests assert that no HLS or non-static stream URL is ever requested.

## License

MIT, see [LICENSE](LICENSE). Not affiliated with the Jellyfin project.
