<img src="assets/icon.svg" width="64" height="64" alt="">

# Ulanzi stream deck

A single background process that drives an Ulanzi D200 directly over USB (no Ulanzi app): three pages (Fun, AI,
3D Print), live T3 Code threads, Spotify, the Klipper printer, and one key that moves the desk (both monitors and
the UGREEN USB switch) between this PC and the company laptop.

## Layout

```
 0   1   2   3   4        Fun       Dota 2 · Discord · 3 playlists / headphones · YouTube · prev · now playing · next
 5   6   7   8   9        AI        T3 Code · Dictate · New thread / up to 7 live threads (newest first, paged)
10  11  12  [ 13  ]       3D Print  New 3D thread · its threads · camera / status · filament change · pause/resume · cancel · Cura
                          bottom    Fun · AI · 3D Print page keys, and the wide desk-switch key on every page
```

Thread keys show the project (its T3 icon, or a coloured monogram), the title, and the state: a blue animated
border while **working**, amber **Needs you** when it waits for an answer, red **Failed**, green **Ready** otherwise.
Settled, archived, snoozed and subagent threads never appear. The AI page key shows how many need you.

## Setup (once)

```powershell
copy deck.example.toml deck.toml        # then edit it: your pages and keys, apps, playlists, printer, monitors
C:\Python314\python.exe -m venv .venv
.venv\Scripts\python -m pip install hidapi pillow resvg-py winrt-runtime winrt-Windows.Media.Control winrt-Windows.Storage.Streams winrt-Windows.Foundation pytest
.venv\Scripts\python -m deck install      # task "UlanziDeckDaemon": starts at logon, restarts within 5 min if it exits
```

## Everyday

| | |
|---|---|
| change keys, pages, apps, playlists, printer, monitors | edit `deck.toml`, `.venv\Scripts\python -m deck check`, then `.venv\Scripts\python -m deck restart` |
| what keys can I use? | `.venv\Scripts\python -m deck widgets` (every widget, its options, every action) |
| deck.toml from before layouts (has `[pages.fun]`) | `.venv\Scripts\python scripts\migrate_config.py` |
| see all pages without the deck | `.venv\Scripts\python -m deck preview` (PNGs in `%APPDATA%\ulanzi-deck\preview`) |
| what the sources report | `.venv\Scripts\python -m deck status` |
| switch the desk from a terminal | `.venv\Scripts\python -m deck switch laptop` / `pc` |
| logs | `%APPDATA%\ulanzi-deck\deck.log` |
| the deck froze | `.venv\Scripts\python -m deck reset` (restarts it over USB, no admin); if that doesn't help, replug it |
| headphones back to this PC | Fun key 5: tap = reconnect / re-take playback, hold 1.5 s = drop and reconnect; or `python -m deck headphones reclaim` |
| preheat for a filament change | 3D Print key 6 while idle heats the hotend to 200 °C and beeps on the printer when ready |
| playlist keys start playback (Premium) | see `deck/sources/spotify.py`, then `python -m deck spotify-login <client id>` |
| back to the Ulanzi app | `.venv\Scripts\python -m deck uninstall` |
| tests | `.venv\Scripts\python -m pytest -q` |

Agents (and people) changing the layout or adding widgets: see [AGENTS.md](AGENTS.md).

## Code map

| module | job |
|---|---|
| `deck/device.py` | the D200 USB protocol: `Deck.show(images)` sends only what changed, keeps the screen on, reconnects |
| `deck/render.py` | tile values (`AppTile`, `ThreadTile`, …) → 196×196 PNG; the visual language lives here |
| `deck/keys.py` | what a key is: `Key(tile, Do("action.name"))`, `View`, `Snapshot` |
| `deck/layout.py` | `deck.toml` pages → which widget on which slot; `build(view, snapshot)` → keys (pure) |
| `deck/widgets.py` / `deck/actions.py` | the registries plugins fill: widget kinds, and named actions |
| `deck/plugins/` | one module per integration (core, t3, media, headphones, printer, kvm): sources, actions, widgets |
| `deck/app.py` | the daemon loop: loads plugins and the layout, runs presses and actions, keeps the deck drawn |
| `deck/presses.py` | pure, clock-driven: what a press means (tap, hold-to-confirm, long press) and each key's flash |
| `deck/sources/registry.py` | every source's poll thread and latest state (names = `Snapshot` fields), `refresh` after an action |
| `deck/pnp.py` | Windows Plug and Play: is the deck stuck in boot mode, restart it over USB |
| `deck/sources/t3.py` | T3 Code threads over its local HTTP API; deep links and `t3 app` for actions |
| `deck/sources/kvm.py` | monitors over DDC/CI + the ESP32 that presses the UGREEN button |
| `deck/sources/printer.py` | Moonraker status, webcam, pause/resume/cancel |
| `deck/sources/media.py` | Windows media session (now playing, transport) and playlist covers |
| `deck/sources/headphones.py` | Bluetooth headphones: connected?, battery, Windows' own reconnect call |
| `deck.toml` | your layout and every machine- or taste-specific value (git-ignored; start from `deck.example.toml`) |

Per-machine state (T3 read-only tokens, Spotify token, caches, logs) lives in `%APPDATA%\ulanzi-deck`.
Icons are vendored in `deck/assets/icons` (sources and licences in `SOURCES.md`; refresh with `python -m deck.fetch_icons`).

`firmware/ugreen-button` and `scripts/` are the ESP32 UGREEN-switch controller and its PC tools.
