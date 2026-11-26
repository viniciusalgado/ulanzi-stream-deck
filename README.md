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
copy deck.example.toml deck.toml        # then edit it: your apps, playlists, printer, monitors
C:\Python314\python.exe -m venv .venv
.venv\Scripts\python -m pip install hidapi pillow resvg-py winrt-runtime winrt-Windows.Media.Control winrt-Windows.Storage.Streams winrt-Windows.Foundation pytest
.venv\Scripts\python -m deck install      # task "UlanziDeckDaemon": starts at logon, restarts within 5 min if it exits
```

## Everyday

| | |
|---|---|
| change apps, playlists, printer, monitors | edit `deck.toml`, then `.venv\Scripts\python -m deck restart` |
| see all pages without the deck | `.venv\Scripts\python -m deck preview` (PNGs in `%APPDATA%\ulanzi-deck\preview`) |
| what the sources report | `.venv\Scripts\python -m deck status` |
| switch the desk from a terminal | `.venv\Scripts\python -m deck switch laptop` / `pc` |
| logs | `%APPDATA%\ulanzi-deck\deck.log` |
| the deck froze | `.venv\Scripts\python -m deck reset` (restarts it over USB, no admin); if that doesn't help, replug it |
| headphones back to this PC | Fun key 5: tap = reconnect / re-take playback, hold 1.5 s = drop and reconnect; or `python -m deck headphones reclaim` |
| preheat for a filament change | 3D Print key 7 with a configured switch, or key 6 without one. Heats the idle hotend to 200 °C and beeps when ready |
| switch printer power | With a configured switch, tap 3D Print key 8 to power on without heating. When idle and powered on, hold for 2 s to turn off the heaters, cool to 50 °C, and cut power |
| playlist keys start playback (Premium) | see `deck/sources/spotify.py`, then `python -m deck spotify-login <client id>` |
| back to the Ulanzi app | `.venv\Scripts\python -m deck uninstall` |
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

## Code map

| module | job |
|---|---|
| `deck/device.py` | the D200 USB protocol: `Deck.show(images)` sends only what changed, keeps the screen on, reconnects |
| `deck/render.py` | tile values (`AppTile`, `ThreadTile`, …) → 196×196 PNG; the visual language lives here |
| `deck/pages.py` | pure layout: `build(view, snapshot)` → what each key shows and does |
| `deck/app.py` | the daemon loop: wires sources, presses and the deck together; runs actions on workers |
| `deck/presses.py` | pure, clock-driven: what a press means (tap, hold-to-confirm, long press) and each key's flash |
| `deck/sources/registry.py` | every source's poll thread and latest state (names = `Snapshot` fields), `refresh` after an action |
| `deck/pnp.py` | Windows Plug and Play: is the deck stuck in boot mode, restart it over USB |
| `deck/sources/t3.py` | T3 Code threads over its local HTTP API; deep links and `t3 app` for actions |
| `deck/sources/kvm.py` | monitors over DDC/CI + the ESP32 that presses the UGREEN button |
| `deck/sources/printer.py` | Moonraker status, webcam, pause/resume/cancel |
| `deck/sources/media.py` | Windows media session (now playing, transport) and playlist covers |
| `deck/sources/headphones.py` | Bluetooth headphones: connected?, battery, Windows' own reconnect call |
| `deck.toml` | every machine- or taste-specific value |

Per-machine state (T3 read-only tokens, Spotify token, caches, logs) lives in `%APPDATA%\ulanzi-deck`.
Icons are vendored in `deck/assets/icons` (sources and licences in `SOURCES.md`; refresh with `python -m deck.fetch_icons`).

`firmware/ugreen-button` and `scripts/` are the ESP32 UGREEN-switch controller and its PC tools.
