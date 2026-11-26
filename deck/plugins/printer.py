"""Klipper printer through Moonraker: status, camera still, pause / resume / cancel, filament change.
On when deck.toml has [printer].

    [printer]
    api = "http://printer.local:7125"                        # Moonraker
    webcam = "http://printer.local/webcam/?action=snapshot"  # optional: for printer.camera

Sources: printer (PrinterState; polled faster while a printer widget is on screen), camera (a still on demand).
Actions: printer.pause, printer.resume, printer.cancel, printer.filament_change, printer.camera
Widgets: printer.status, printer.camera, printer.filament, printer.pause_resume, printer.cancel.
Badge: printer (progress while printing, II paused, ! error).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..keys import Do, Key, duration, offline
from ..render import AMBER, BLUE, GREEN, RED, AppTile, ArtTile, InfoTile
from ..sources.printer import FILAMENT_CHANGE_TEMP, Printer
from ..widgets import Option

NAME = "printer"
SECTION = "printer"
CAMERA_SHOW_S = 60    # a camera still stays on its key this long, then the icon is back


@dataclass(frozen=True)
class Still:
    jpeg: bytes
    until: float      # epoch seconds


def setup(s, printer: Printer | None = None) -> None:
    printer = printer or Printer(s.section["api"], s.section.get("webcam"))
    s.sources.add("printer", printer.poll, every=lambda: 2.0 if s.showing("printer.") else 6.0)
    s.sources.add("camera", None, initial=None)
    s.lively(lambda snap: snap.camera is not None and snap.camera.until > snap.now)

    def camera() -> None:
        """One webcam still on the camera key for CAMERA_SHOW_S. Only on demand: a stream of new images is what
        froze the deck's firmware (2026-11-25, twice)."""
        jpeg = printer.snapshot()
        if jpeg is None:
            raise RuntimeError("webcam unreachable")
        s.sources.put("camera", Still(jpeg, time.time() + CAMERA_SHOW_S))

    s.actions.add("printer.pause", printer.pause, doc="pause the print")
    s.actions.add("printer.resume", printer.resume, doc="resume the print")
    s.actions.add("printer.cancel", printer.cancel, doc="cancel the print")
    s.actions.add("printer.filament_change", printer.filament_change,
                  doc=f"heat the hotend to {FILAMENT_CHANGE_TEMP} °C for a filament change")
    s.actions.add("printer.camera", camera, doc="show one webcam still on the camera key")
    w = s.widgets

    def state(ctx):
        p = ctx.snap.printer
        return p if p is not None and p.online else None

    @w.widget("printer.status", needs=("printer",),
              options=(Option("open", str, "", "opened on a tap, e.g. the Mainsail URL"),),
              doc="progress / temperatures / done / error")
    def status(ctx):
        p = state(ctx)
        if p is None:
            return offline("Printer")
        temps = f"{p.nozzle[0]:.0f}°/{p.nozzle[1]:.0f}° · bed {p.bed[0]:.0f}°"
        name = p.filename.rsplit("/", 1)[-1].removesuffix(".gcode")
        if p.state == "printing":
            tile = InfoTile("Printing", f"{p.progress * 100:.0f}%",
                            (name, f"ETA {duration(p.eta)}" if p.eta else temps),
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
            tile = InfoTile("Heating" if heating else "Ready", f"{p.nozzle[0]:.0f}°",
                            ("nozzle", f"bed {p.bed[0]:.0f}°"), accent=AMBER if heating else GREEN,
                            icon="lucide:flame" if heating else "lucide:thermometer")
        return Key(tile, Do("launch", (ctx.options["open"],)) if ctx.options["open"] else None)

    @w.widget("printer.camera", needs=("printer", "camera"),
              options=(Option("stream", str, "", "opened on a long press: the live stream URL"),),
              doc="tap: one still on the key; long press: open the live stream")
    def camera_key(ctx):
        if state(ctx) is None:
            return Key(ArtTile(None, "Camera", fallback="lucide:camera"))
        still = ctx.snap.camera
        jpeg = still.jpeg if still is not None and still.until >= ctx.snap.now else None
        stream = ctx.options["stream"]
        return Key(ArtTile(jpeg, "Camera", fallback="lucide:camera"), Do("printer.camera"),
                   long=Do("launch", (stream,)) if stream else None)

    @w.widget("printer.filament", needs=("printer",), doc=f"heat to {FILAMENT_CHANGE_TEMP} °C while idle")
    def filament(ctx):
        p = ctx.snap.printer
        can = p is not None and p.can_change_filament
        return Key(AppTile("lucide:flame", "Filament Change", sub=f"{FILAMENT_CHANGE_TEMP} °C", color=ctx.color,
                           dim=not can), Do("printer.filament_change") if can else None)

    @w.widget("printer.pause_resume", needs=("printer",), doc="pause while printing, resume while paused")
    def pause_resume(ctx):
        p = state(ctx)
        if p is not None and p.state == "printing":
            return Key(AppTile("lucide:pause", "Pause", color=AMBER), Do("printer.pause"))
        if p is not None and p.state == "paused":
            return Key(AppTile("lucide:play", "Resume", color=GREEN), Do("printer.resume"))
        return Key(AppTile("lucide:pause", "Pause", dim=True))

    @w.widget("printer.cancel", needs=("printer",),
              options=(Option("hold", float, 2.0, "seconds to hold before it cancels"),),
              doc="cancel the print, only after a hold")
    def cancel(ctx):
        p = ctx.snap.printer
        if p is None or not p.active:
            return Key(AppTile("lucide:circle-stop", "Cancel", dim=True))
        hold = ctx.options["hold"]
        return Key(AppTile("lucide:circle-stop", "Cancel", sub=f"hold {hold:g} s", color=RED), Do("printer.cancel"),
                   hold=hold)

    w.badge("printer", _badge)


def _badge(snap):
    p = snap.printer
    if p is None:
        return None
    if p.state == "printing":
        return f"{p.progress * 100:.0f}%", BLUE
    if p.state == "paused":
        return "II", AMBER
    if p.state == "error":
        return "!", RED
    return None
