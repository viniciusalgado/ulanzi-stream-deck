"""The 3D printer through Moonraker (Klipper's API server).

Interface:
    printer = Printer("http://printer.local:7125", webcam="http://printer.local/webcam/?action=snapshot")
    state = printer.poll()          # PrinterState (never raises; online=False when unreachable)
    jpeg = printer.snapshot()       # webcam still, or None
    printer.pause() / resume() / cancel() / filament_change()
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
OBJECTS = "print_stats&display_status&extruder&heater_bed&virtual_sdcard&webhooks"
FILAMENT_CHANGE_TEMP = 200
FILAMENT_HEAT_TIMEOUT = 300


@dataclass(frozen=True)
class PrinterState:
    online: bool = False
    state: str = "offline"   # printing | paused | complete | cancelled | error | standby | offline | startup | shutdown
    filename: str = ""
    progress: float = 0.0
    elapsed: float = 0.0     # seconds printing
    eta: float | None = None  # seconds left (estimated)
    nozzle: tuple[float, float] = (0.0, 0.0)  # (temperature, target)
    bed: tuple[float, float] = (0.0, 0.0)
    message: str = ""

    @property
    def active(self) -> bool:
        return self.state in ("printing", "paused")

    @property
    def can_change_filament(self) -> bool:
        return self.online and self.state in ("standby", "complete", "cancelled")


def parse_status(status: dict) -> PrinterState:
    ps = status.get("print_stats", {})
    hooks = status.get("webhooks", {})
    state = ps.get("state") or "standby"
    if hooks.get("state") and hooks["state"] != "ready":
        state = hooks["state"]          # startup / shutdown / error from Klipper itself
    progress = float(status.get("display_status", {}).get("progress")
                     or status.get("virtual_sdcard", {}).get("progress") or 0.0)
    elapsed = float(ps.get("print_duration") or 0.0)
    eta = elapsed / progress - elapsed if state == "printing" and progress > 0.02 else None
    ext, bed = status.get("extruder", {}), status.get("heater_bed", {})
    return PrinterState(online=True, state=state, filename=ps.get("filename") or "", progress=progress,
                        elapsed=elapsed, eta=eta,
                        nozzle=(float(ext.get("temperature") or 0), float(ext.get("target") or 0)),
                        bed=(float(bed.get("temperature") or 0), float(bed.get("target") or 0)),
                        message=ps.get("message") or hooks.get("state_message", "") or "")


class Printer:
    def __init__(self, api: str, webcam: str | None = None):
        self.api = api.rstrip("/")
        self.webcam = webcam
        self._filament_change_lock = threading.Lock()

    def poll(self) -> PrinterState:
        try:
            with _opener.open(f"{self.api}/printer/objects/query?{OBJECTS}", timeout=3) as r:
                return parse_status(json.load(r)["result"]["status"])
        except Exception:
            return PrinterState()

    def snapshot(self) -> bytes | None:
        if not self.webcam:
            return None
        try:
            with _opener.open(f"{self.webcam}&t={int(time.time())}", timeout=4) as r:
                return r.read()
        except Exception:
            return None

    def _post(self, path: str, payload: dict | None = None) -> None:
        data = json.dumps(payload).encode("utf-8") if payload is not None else b""
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        req = urllib.request.Request(self.api + path, method="POST", data=data, headers=headers)
        with _opener.open(req, timeout=10) as r:
            body = json.load(r)
        if "error" in body:
            raise RuntimeError(body["error"].get("message", "printer refused"))

    def pause(self) -> None:
        self._post("/printer/print/pause")

    def resume(self) -> None:
        self._post("/printer/print/resume")

    def cancel(self) -> None:
        self._post("/printer/print/cancel")

    def _gcode(self, script: str) -> None:
        self._post("/printer/gcode/script", {"script": script})

    def filament_change(self) -> None:
        if not self._filament_change_lock.acquire(blocking=False):
            raise RuntimeError("Filament change is already heating")
        try:
            if not self.poll().can_change_filament:
                raise RuntimeError("The printer must be idle and ready for filament change")
            self._gcode(f"M104 S{FILAMENT_CHANGE_TEMP}")

            deadline = time.monotonic() + FILAMENT_HEAT_TIMEOUT
            while time.monotonic() < deadline:
                status = self.poll()
                if not status.can_change_filament or status.nozzle[1] != FILAMENT_CHANGE_TEMP:
                    raise RuntimeError("Filament change heating was interrupted")
                if status.nozzle[0] >= FILAMENT_CHANGE_TEMP:
                    self._gcode("M300 S1000 P400")
                    return
                time.sleep(1)
            raise RuntimeError("Hotend did not reach filament change temperature in time")
        finally:
            self._filament_change_lock.release()
