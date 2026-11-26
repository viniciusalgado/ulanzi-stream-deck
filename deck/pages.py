"""What every key shows and does, per page. Pure: `build(view, snap, svc, cfg)` returns data, runs nothing.

Key numbers on the D200 (5 x 3, the wide key spans the last two cells of the bottom row):

     0   1   2   3   4
     5   6   7   8   9
    10  11  12  [ 13 ]

Bottom row on every page: 10-12 switch pages (Fun / AI / 3D Print), 13 moves the desk between PC and laptop.

  Fun:      0 Dota 2   1 Discord   2-4 playlists        5 headphones (tap: back to this PC, hold: hard reconnect)
            6 YouTube  7 prev  8 play/pause (now playing)  9 next
  AI:       0 T3 Code  1 Dictate   2 New thread         3-9 live threads (newest first; 9 pages when there are more)
  New:      0 Back     1 Environment                    2-9 projects of that environment, most recently used first
  3D Print: 0 New thread (3d project)  1-3 its threads  4 camera (tap: snapshot, hold: stream)
            5 print status  6 filament change  7 pause/resume  8 cancel (hold)  9 Cura

Nobody signed in (Snapshot.desktop == "signed_out"): only the 3D Print page, with every key that works
without a Windows session: camera still, printer status, filament change, pause/resume, cancel, desk switch;
plus "Signed out" and a clock where the T3 keys were. Fun and AI say "sign in".
Locked, or the agent missing: keys whose action needs the desktop (deck.desktop.usable) are drawn dimmed and
do nothing; the rest of the page is unchanged.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable

from .desktop import usable
from .render import (AMBER, BLUE, FG, GREEN, MUTED, RED, AppTile, ArtTile, Blank, InfoTile, SwitchTile,
                     ThreadTile)
from .sources.printer import FILAMENT_CHANGE_TEMP

PAGE_COLORS = {"fun": "#f472b6", "ai": "#a78bfa", "print": "#fb923c"}
NAV = [("fun", "lucide:gamepad-2", "Fun"), ("ai", "lucide:bot", "AI"), ("print", "lucide:printer", "3D Print")]
WIDE = 13


@dataclass(frozen=True)
class View:
    page: str = "ai"          # fun | ai | new_thread | print
    offset: int = 0           # paging through threads / projects
    env: str | None = None    # environment chosen in the new-thread picker


@dataclass(frozen=True)
class Go:
    view: View


@dataclass(frozen=True)
class Do:
    fn: Callable
    args: tuple = ()
    then: View | None = None  # navigate here as soon as the key is pressed


@dataclass(frozen=True)
class Key:
    tile: object
    action: Go | Do | None = None
    hold: float = 0.0          # seconds the key must be held before the action runs
    long: Do | None = None     # optional second action for a long press (then `action` runs on a short tap)


@dataclass(frozen=True)
class Snapshot:
    """Everything the pages may show, gathered by the app from the sources."""
    t3: object                 # sources.t3.T3State
    media: object              # sources.media.MediaState
    playlists: tuple = ()      # sources.media.Playlist, in deck.toml order
    printer: object = None     # sources.printer.PrinterState
    camera: bytes | None = None
    kvm: object = None         # sources.kvm.KvmState
    kvm_busy: bool = False
    kvm_message: str = ""
    kvm_message_error: bool = False   # red when the monitors failed; amber for notes (e.g. USB didn't follow)
    dictating: bool = False
    new_draft: bool = False    # a New thread draft was just opened: Dictate will type into it
    headphones: object = None  # sources.headphones.HeadphoneState
    desktop: str = "here"      # here | locked | away | signed_out (see deck.desktop)
    now: float = 0.0


def age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def duration(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def build(view: View, snap: Snapshot, svc, cfg: dict) -> dict[int, Key]:
    signed_out = snap.desktop == "signed_out"
    if signed_out:
        view = View("print", offset=0)
    keys: dict[int, Key] = {i: Key(Blank()) for i in range(14)}
    page = {"fun": _fun, "ai": _ai, "new_thread": _new_thread, "print": _print}.get(view.page, _ai)
    keys.update(page(view, snap, svc, cfg))
    keys.update(_bottom(view, snap, svc))
    if signed_out:
        keys.update(_signed_out(snap))
    return {i: _gate(k, snap.desktop) for i, k in keys.items()}


def _gate(key: Key, presence: str) -> Key:
    """Drop the actions the desktop can't run right now; an AppTile left with nothing to do is drawn dimmed."""
    action = key.action if not isinstance(key.action, Do) or usable(key.action.fn, presence) else None
    long = key.long if key.long is None or usable(key.long.fn, presence) else None
    if action is key.action and long is key.long:
        return key
    tile = key.tile
    if action is None and long is None and isinstance(tile, AppTile):
        tile = replace(tile, dim=True)
    return Key(tile, action, key.hold if action is not None else 0.0, long)


def _signed_out(snap: Snapshot) -> dict[int, Key]:
    """What replaces the session-only keys of the 3D Print page when nobody is signed in."""
    t = time.localtime(snap.now)
    nav = {i: Key(AppTile(icon, label, sub="sign in", color=MUTED, bg="#15171c", dim=True))
           for i, (page, icon, label) in enumerate(NAV) if page != "print"}
    return {
        0: Key(InfoTile("This PC", "Signed out", ("sign in for T3,", "music & apps"), accent=MUTED,
                        icon="lucide:monitor")),
        1: Key(InfoTile("Clock", time.strftime("%H:%M", t), (time.strftime("%a %d %b", t),), accent=MUTED)),
        2: Key(Blank()), 3: Key(Blank()), 9: Key(Blank()),
        **{10 + i: k for i, k in nav.items()},
    }


# ---------------- shared ----------------

def _bottom(view: View, snap: Snapshot, svc) -> dict[int, Key]:
    keys = {}
    current = "ai" if view.page == "new_thread" else view.page
    for i, (page, icon, label) in enumerate(NAV):
        on = page == current
        color = PAGE_COLORS[page]
        badge, badge_color = _nav_badge(page, snap)
        keys[10 + i] = Key(AppTile(icon, label, color=color if on else MUTED, bg="#1f2229" if on else "#15171c",
                                   accent=color if on else None, badge=badge, badge_color=badge_color),
                           Go(View(page)))
    k = snap.kvm
    here = getattr(k, "here", None) or "pc"          # where the monitors are
    usb = getattr(k, "usb", None)
    active = {"pc": "left", "laptop": "right"}[here]
    note = "ACTIVE" if usb in (None, here) else ("USB still on PC" if usb == "pc" else "USB on laptop")
    keys[WIDE] = Key(SwitchTile(("This PC", "lucide:monitor"), ("Laptop", "lucide:laptop"), active,
                                busy=snap.kvm_busy, message=snap.kvm_message,
                                message_color=RED if snap.kvm_message_error else AMBER, active_note=note),
                     None if snap.kvm_busy else Do(svc.toggle_kvm))   # target decided at press time, not when drawn
    return keys


def _nav_badge(page: str, snap: Snapshot) -> tuple[str | None, str]:
    if page == "ai":
        threads = getattr(snap.t3, "threads", ())
        need = sum(t.state in ("attention", "error") for t in threads)
        working = sum(t.state == "working" for t in threads)
        if need:
            return str(need), AMBER
        if working:
            return str(working), BLUE
    if page == "print" and snap.printer is not None:
        p = snap.printer
        if p.state == "printing":
            return f"{p.progress * 100:.0f}%", BLUE
        if p.state == "paused":
            return "II", AMBER
        if p.state == "error":
            return "!", RED
    if page == "fun" and getattr(snap.media, "playing", False):
        return "▶", GREEN
    return None, AMBER


def _thread_key(t, snap: Snapshot, svc) -> Key:
    status = {"working": f"Working {age(snap.now - t.since)}", "attention": "Needs you", "error": "Failed",
              "ready": f"Ready · {age(snap.now - t.since)}"}[t.state]
    return Key(ThreadTile(t.title, t.project.title, t.project.icon, t.state, status,
                          phase=int(snap.now) if t.state == "working" else 0),
               Do(svc.desktop.open_thread, (t,)))


def _paged(items: list, slots: list[int], view: View, make: Callable, more_view: Callable[[int], View]) -> dict:
    """Fill slots with items; when they don't fit, the last slot pages through the rest."""
    keys = {}
    if len(items) <= len(slots):
        shown, start = items, 0
    else:
        per = len(slots) - 1
        start = view.offset if view.offset < len(items) else 0
        shown = items[start: start + per]
        left = len(items) - start - len(shown)
        nxt = start + per if start + per < len(items) else 0
        keys[slots[-1]] = Key(AppTile("lucide:chevrons-right", f"+{left}" if left else "Back to first",
                                      sub="more", color=MUTED), Go(more_view(nxt)))
    for slot, item in zip(slots, shown):
        keys[slot] = make(item)
    return keys


def _offline(name: str) -> Key:
    return Key(InfoTile(name, "Offline", ("not reachable",), accent=MUTED, icon="lucide:unplug"))


# ---------------- pages ----------------

def _fun(view: View, snap: Snapshot, svc, cfg: dict) -> dict[int, Key]:
    keys = {}
    apps = cfg.get("apps", [])
    for slot, app in zip((0, 1, 6), apps):
        keys[slot] = Key(AppTile(app["icon"], app["name"], color=app.get("color", FG)),
                         Do(svc.desktop.launch, (app["open"],)))
    for slot, pl in zip((2, 3, 4), snap.playlists):
        keys[slot] = Key(ArtTile(pl.cover, pl.title, fallback="lucide:list-music"),
                         Do(svc.desktop.play_playlist, (pl.uri,)))
    keys[5] = _headphones_key(snap, svc, cfg)
    m = snap.media
    has = getattr(m, "available", False)
    keys[7] = Key(AppTile("lucide:skip-back", "Previous", dim=not has), Do(svc.desktop.media_prev) if has else None)
    keys[9] = Key(AppTile("lucide:skip-forward", "Next", dim=not has), Do(svc.desktop.media_next) if has else None)
    if has:
        keys[8] = Key(ArtTile(m.art, m.title or "Playing", m.artist, glyph="lucide:pause" if m.playing else "lucide:play",
                              accent=GREEN if m.playing else None), Do(svc.desktop.media_toggle))
    else:
        keys[8] = Key(AppTile("brand:spotify", "Spotify", sub="not playing", color="#1ed760"),
                      Do(svc.desktop.launch, (cfg.get("spotify_app", "spotify:"),)))
    return keys


def _headphones_key(snap: Snapshot, svc, cfg: dict) -> Key:
    hp = snap.headphones
    label = cfg.get("headphones_label", "Headphones")
    if hp is None or not hp.found:
        return Key(AppTile("lucide:headphones", label, sub="not paired", dim=True))
    battery = f" · {hp.battery}%" if hp.battery is not None else ""
    if hp.connected:
        tile = AppTile("lucide:headphones", label, sub=f"on this PC{battery}", color=GREEN)
    else:
        tile = AppTile("lucide:headphones", label, sub="tap to connect", color=AMBER, badge="!", badge_color=AMBER)
    return Key(tile, Do(svc.desktop.reclaim_headphones), long=Do(svc.desktop.reset_headphones))


def _ai(view: View, snap: Snapshot, svc, cfg: dict) -> dict[int, Key]:
    keys = {
        0: Key(AppTile("app:t3code", "T3 Code", icon_scale=0.5), Do(svc.desktop.open_app)),
        1: Key(_dictate_tile(snap), Do(svc.toggle_dictation)),
        2: Key(AppTile("lucide:square-plus", "New thread", sub="pick project", color=PAGE_COLORS["ai"]),
               Go(View("new_thread"))),
    }
    t3 = snap.t3
    primary = next((e for e in t3.envs if e.primary), None)
    if primary is not None and not t3.online.get(primary.key, False):
        keys[3] = _offline("T3 Code")
        return keys
    threads = list(t3.threads)
    if not threads:
        keys[3] = Key(InfoTile("Threads", "", ("No active threads", "settled ones stay hidden"),
                               accent=GREEN, icon="lucide:circle-check"))
        return keys
    keys.update(_paged(threads, [3, 4, 5, 6, 7, 8, 9], view, lambda t: _thread_key(t, snap, svc),
                       lambda o: View("ai", offset=o)))
    return keys


def _dictate_tile(snap: Snapshot) -> AppTile:
    if snap.dictating:
        return AppTile("lucide:mic", "Dictate", sub="listening… tap to stop", color=RED, accent=RED)
    if snap.new_draft:
        return AppTile("lucide:mic", "Dictate", sub="into new thread", color=PAGE_COLORS["ai"],
                       accent=PAGE_COLORS["ai"])
    return AppTile("lucide:mic", "Dictate", sub="into open thread")


def _new_thread(view: View, snap: Snapshot, svc, cfg: dict) -> dict[int, Key]:
    back = Key(AppTile("lucide:arrow-left", "Back", color=MUTED), Go(View("ai")))
    envs = [e for e in snap.t3.envs if snap.t3.online.get(e.key, False)] or list(snap.t3.envs[:1])
    if not envs:
        return {0: back, 1: Key(InfoTile("T3 Code", "", ("no environments", "in deck.toml"), accent=MUTED,
                                         icon="lucide:server"))}
    env = next((e for e in envs if e.key == view.env), envs[0])
    nxt = envs[(envs.index(env) + 1) % len(envs)]
    keys = {
        0: back,
        1: Key(AppTile("lucide:server", env.name, sub="environment" if len(envs) > 1 else "only one online",
                       color=PAGE_COLORS["ai"], accent=PAGE_COLORS["ai"]),
               Go(View("new_thread", env=nxt.key)) if len(envs) > 1 else None),
    }
    projects = [p for p in snap.t3.projects if p.env == env.key]
    if not projects:
        keys[2] = Key(InfoTile("Projects", "", ("none in this", "environment"), accent=MUTED, icon="lucide:folder-git-2"))
        return keys

    def project_key(p):
        return Key(AppTile(p.icon, p.title, sub=env.name, icon_scale=0.42), Do(svc.new_thread, (p,), then=View("ai")))

    keys.update(_paged(projects, [2, 3, 4, 5, 6, 7, 8, 9], view, project_key,
                       lambda o: View("new_thread", offset=o, env=env.key)))
    return keys


def _print(view: View, snap: Snapshot, svc, cfg: dict) -> dict[int, Key]:
    keys = {}
    color = PAGE_COLORS["print"]
    want = (cfg.get("project") or "").lower()
    project = next((p for p in snap.t3.projects
                    if p.title.lower() == want or p.root.replace("\\", "/").rstrip("/").lower().endswith("/" + want)), None)
    if project is not None:
        keys[0] = Key(AppTile("lucide:square-plus", "New thread", sub=project.title, color=color),
                      Do(svc.new_thread, (project,)))
        threads = [t for t in snap.t3.threads if t.project.id == project.id]
        if threads:
            keys.update(_paged(threads, [1, 2, 3], view, lambda t: _thread_key(t, snap, svc),
                               lambda o: View("print", offset=o)))
        else:
            keys[1] = Key(InfoTile("3D threads", "", ("none active",), accent=GREEN, icon="lucide:circle-check"))
    else:
        keys[0] = Key(AppTile("lucide:square-plus", "New thread", sub=f"no project '{want}'", dim=True))
    keys[9] = Key(AppTile("app:cura", "Cura"), Do(svc.desktop.launch, (cfg["cura"],)))
    p = snap.printer
    can_change_filament = p is not None and p.can_change_filament
    keys[6] = Key(AppTile("lucide:flame", "Filament Change", sub=f"{FILAMENT_CHANGE_TEMP} °C", color=color,
                         dim=not can_change_filament),
                  Do(svc.printer.filament_change) if can_change_filament else None)
    keys[8] = Key(AppTile("lucide:circle-stop", "Cancel", dim=True))
    if p is None or not p.online:
        keys[5] = _offline("Printer")
        keys[4] = Key(ArtTile(None, "Camera", fallback="lucide:camera"))
        keys[7] = Key(AppTile("lucide:pause", "Pause", dim=True))
        return keys
    temps = f"{p.nozzle[0]:.0f}°/{p.nozzle[1]:.0f}° · bed {p.bed[0]:.0f}°"
    name = p.filename.rsplit("/", 1)[-1].removesuffix(".gcode")
    if p.state == "printing":
        tile = InfoTile("Printing", f"{p.progress * 100:.0f}%", (name, f"ETA {duration(p.eta)}" if p.eta else temps),
                        progress=p.progress, accent=BLUE, icon="lucide:layers")
    elif p.state == "paused":
        tile = InfoTile("Paused", f"{p.progress * 100:.0f}%", (name, temps), progress=p.progress, accent=AMBER,
                        icon="lucide:pause")
    elif p.state == "complete":
        tile = InfoTile("Done", "100%", (name, f"took {duration(p.elapsed)}"), progress=1.0, accent=GREEN,
                        icon="lucide:circle-check")
    elif p.state in ("error", "shutdown"):
        tile = InfoTile("Error", "", (p.message[:60] or p.state, temps), accent=RED, icon="lucide:triangle-alert")
    else:
        heating = p.nozzle[1] > 0 or p.bed[1] > 0
        tile = InfoTile("Heating" if heating else "Ready", f"{p.nozzle[0]:.0f}°", ("nozzle", f"bed {p.bed[0]:.0f}°"),
                        accent=AMBER if heating else GREEN, icon="lucide:flame" if heating else "lucide:thermometer")
    keys[5] = Key(tile, Do(svc.desktop.launch, (cfg["mainsail"],)))
    keys[4] = Key(ArtTile(snap.camera, "Camera", fallback="lucide:camera"), Do(svc.camera_snapshot),
                  long=Do(svc.desktop.launch, (cfg["camera_page"],)))   # tap: one still on the key; hold: live stream
    if p.state == "printing":
        keys[7] = Key(AppTile("lucide:pause", "Pause", color=AMBER), Do(svc.printer.pause))
    elif p.state == "paused":
        keys[7] = Key(AppTile("lucide:play", "Resume", color=GREEN), Do(svc.printer.resume))
    else:
        keys[7] = Key(AppTile("lucide:pause", "Pause", dim=True))
    if p.active:
        keys[8] = Key(AppTile("lucide:circle-stop", "Cancel", sub="hold 2 s", color=RED), Do(svc.printer.cancel),
                      hold=2.0)
    return keys
