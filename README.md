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
# from an ADMINISTRATOR terminal: copies the repo to C:\Program Files\UlanziDeck, installs the service + agent
.venv\Scripts\python -m deck deploy
```

The deck runs as a Windows service, so it works from boot to shutdown, whether anyone is signed in or not
(docs/plans/service-mode.md). With nobody signed in it shows the 3D Print page with every key that works without
a session (camera still, printer status, filament change, pause/resume, cancel), a clock, and the desk switch.
Locked: keys that would open windows or type are dimmed; music, printer and desk keep working.
Code only reaches Program Files through `deck deploy`: never edit files there.

## Everyday

| | |
|---|---|
| change keys, pages, apps, playlists, printer, monitors | edit `deck.toml`, `.venv\Scripts\python -m deck check`, then `.venv\Scripts\python -m deck deploy` (administrator) |
| what keys can I use? | `.venv\Scripts\python -m deck widgets` (every widget, its options, every action) |
| deck.toml from before layouts (has `[pages.fun]`) | `.venv\Scripts\python scripts\migrate_config.py` |
| change code | edit here, then `.venv\Scripts\python -m deck deploy` (administrator) |
| restart what's deployed | `.venv\Scripts\python -m deck restart` (no admin) |
| try a change without deploying | `sc stop UlanziDeck` (allowed without admin), then `.venv\Scripts\python -m deck run` |
| see all pages without the deck | `.venv\Scripts\python -m deck preview` (PNGs in `%APPDATA%\ulanzi-deck\preview`) |
| what the sources report | `.venv\Scripts\python -m deck status` |
| switch the desk from a terminal | `.venv\Scripts\python -m deck switch laptop` / `pc` |
| logs | service `C:\ProgramData\ulanzi-deck\deck.log`, monitor helper `...\console.log`, agent `%APPDATA%\ulanzi-deck\agent.log` |
| ESP32 token or IP changed | `deck deploy` again (the service keeps its own copy of the ESP config) |
| the deck froze | `.venv\Scripts\python -m deck reset` (restarts it over USB, no admin); if that doesn't help, replug it |
| headphones back to this PC | Fun key 5: tap = reconnect / re-take playback, hold 1.5 s = drop and reconnect; or `python -m deck headphones reclaim` |
| preheat for a filament change | 3D Print key 7 with a configured switch, or key 6 without one. Heats the idle hotend to 200 °C and beeps when ready |
| switch printer power | With a configured switch, tap 3D Print key 8 to power on without heating. When idle and powered on, hold for 2 s to turn off the heaters, cool to 50 °C, and cut power |
| playlist keys start playback (Premium) | see `deck/sources/spotify.py`, then `python -m deck spotify-login <client id>` |
| back to the Ulanzi app | `.venv\Scripts\python -m deck undeploy` (administrator) |
| tests | `.venv\Scripts\python -m pytest -q` |

## Local printer power switch

For a Tuya Wi-Fi relay, install the optional dependency with `.venv\Scripts\python -m pip install tinytuya`.
Connect the printer's power supply to the relay and keep the Moonraker host independently powered.
Copy the commented `[printer.power]` example to your private `deck.toml` and fill in the switch's LAN IP, device ID,
local key, protocol version, and relay datapoint. Restart the daemon and run `python -m deck status` in the virtual
environment to check that it can read the relay. Keep the same IP with a DHCP reservation.

The deck talks directly to the switch over the LAN. When the relay is off, **On + Preheat** powers it up and waits
for Klipper to be idle and ready. During a print or pause, the same two buttons control the job. **Power Off**
requires a fresh idle reading from Klipper and stops if a job starts while the hotend is cooling.

To verify LAN-only operation with stock firmware, block internet access for the relay at the router while preserving
local access, then check that the relay still responds after a reboot.

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
| `deck/desktop.py` | everything that needs a signed-in session (T3, music, headphones, launching), and presence |
| `deck/agent.py` | service ↔ agent protocol, both ends: `RemoteDesktop` (service side) and the agent loop |
| `deck/console.py` | monitor switching for the service, via a SYSTEM helper in the console session |
| `deck/service.py` | the service: App + RemoteDesktop + ConsoleDdc, following who is at the PC |
| `deck/deploy.py` | deploy / undeploy / restart: the Program Files copy, the service, the agent task |
| `deck/link.py`, `deck/wire.py` | calls between processes, and the JSON-only values they carry (no pickle) |
| `deck/winpipe.py`, `winsession.py`, `winservice.py` | pipes with access lists, sessions, the Service Control Manager |
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
