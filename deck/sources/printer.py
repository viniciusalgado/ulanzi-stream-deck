"""The 3D printer through Moonraker (Klipper's API server).

Interface:
    printer = Printer("http://printer.local:7125", webcam="http://printer.local/webcam/?action=snapshot")
    state = printer.poll()          # PrinterState (never raises; online=False when unreachable)
    jpeg = printer.snapshot()       # webcam still, or None
    printer.pause() / resume() / cancel() / filament_change() / power_on() / power_off()
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from dataclasses import dataclass

from .tuya import PowerState, TuyaSwitch

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
OBJECTS = "print_stats&display_status&extruder&heater_bed&virtual_sdcard&webhooks&pause_resume"
FILAMENT_CHANGE_TEMP = 200
FILAMENT_HEAT_TIMEOUT = 300
POWER_ON_TIMEOUT = 120
USB_SETTLE_TIME = 6
POWER_OFF_TEMP = 50
POWER_OFF_TIMEOUT = 600
IDLE_STATES = ("standby", "complete", "cancelled")


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
        return self.online and self.state in IDLE_STATES


def parse_status(status: dict) -> PrinterState:
    ps = status.get("print_stats", {})
    hooks = status.get("webhooks", {})
    sd = status.get("virtual_sdcard", {})
    ext, bed = status.get("extruder", {}), status.get("heater_bed", {})
    state = ps.get("state") or "unknown"
    if hooks.get("state") != "ready":
        state = hooks.get("state") or "unknown"   # startup / shutdown / error from Klipper itself
    elif state == "paused" or status.get("pause_resume", {}).get("is_paused"):
        state = "paused"
    elif sd.get("is_active"):
        state = "printing"
    elif state in IDLE_STATES and not all(
        isinstance(heater.get(field), (int, float)) and math.isfinite(heater[field])
        for heater in (ext, bed) for field in ("temperature", "target")
    ):
        state = "unknown"
    progress = float(status.get("display_status", {}).get("progress")
                     or sd.get("progress") or 0.0)
    elapsed = float(ps.get("print_duration") or 0.0)
    eta = elapsed / progress - elapsed if state == "printing" and progress > 0.02 else None
    message = hooks.get("state_message") if hooks.get("state") != "ready" else ps.get("message")
    return PrinterState(online=True, state=state, filename=ps.get("filename") or "", progress=progress,
                        elapsed=elapsed, eta=eta,
                        nozzle=(float(ext.get("temperature") or 0), float(ext.get("target") or 0)),
                        bed=(float(bed.get("temperature") or 0), float(bed.get("target") or 0)),
                        message=message or "")


class Printer:
    def __init__(self, api: str, webcam: str | None = None, *, power: TuyaSwitch | None = None):
        self.api = api.rstrip("/")
        self.webcam = webcam
        self.power = power
        self._filament_change_lock = threading.Lock()
        self._power_lock = threading.Lock()

    def _get(self, path: str) -> dict:
        with _opener.open(self.api + path, timeout=3) as r:
            return json.load(r)["result"]

    def poll(self) -> PrinterState:
        try:
            return parse_status(self._get(f"/printer/objects/query?{OBJECTS}")["status"])
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
            if not self._power_lock.acquire(blocking=False):
                raise RuntimeError("Printer power is already changing")
            try:
                self._start_filament_heat()
            finally:
                self._power_lock.release()

            deadline = time.monotonic() + FILAMENT_HEAT_TIMEOUT
            while time.monotonic() < deadline:
                if not self._power_lock.acquire(blocking=False):
                    raise RuntimeError("Filament change heating was interrupted")
                try:
                    status = self.poll()
                    if not status.can_change_filament or status.nozzle[1] != FILAMENT_CHANGE_TEMP:
                        raise RuntimeError("Filament change heating was interrupted")
                    if status.nozzle[0] >= FILAMENT_CHANGE_TEMP:
                        self._gcode("M300 S1000 P400")
                        return
                finally:
                    self._power_lock.release()
                time.sleep(1)
            raise RuntimeError("Hotend did not reach filament change temperature in time")
        finally:
            self._filament_change_lock.release()

    def _start_filament_heat(self) -> None:
        status = self.poll()
        if self.power is not None and not status.active:
            switched_on = self._power_on_if_off(self.power)
            if switched_on or not status.online:
                status = self._wait_until_ready(recover_mcu=switched_on)
            if status.can_change_filament:
                self._require_queue_idle()
                status = self.poll()
        if not status.can_change_filament:
            raise RuntimeError("The printer must be idle and ready for filament change")
        self._gcode(f"M104 S{FILAMENT_CHANGE_TEMP}")

    def power_on(self) -> None:
        if self.power is None:
            raise RuntimeError("No printer switch is configured")
        if not self._power_lock.acquire(blocking=False):
            raise RuntimeError("Printer power is already changing")
        try:
            if self._power_on_if_off(self.power):
                self._wait_until_ready(recover_mcu=True)
        finally:
            self._power_lock.release()

    @staticmethod
    def _power_on_if_off(power: TuyaSwitch) -> bool:
        state = power.poll()
        if state is PowerState.UNKNOWN:
            raise RuntimeError("The printer switch is unreachable")
        if state is PowerState.ON:
            return False
        power.set_power(True)
        return True

    def _wait_until_ready(self, *, recover_mcu: bool) -> PrinterState:
        started = time.monotonic()
        while time.monotonic() - started < POWER_ON_TIMEOUT:
            status = self.poll()
            if status.can_change_filament or status.active:
                return status
            if recover_mcu and time.monotonic() - started >= USB_SETTLE_TIME and status.state in ("error", "shutdown"):
                message = status.message.lower()
                if "lost communication with mcu" in message or ("mcu " in message and "unable to connect" in message):
                    # Moonraker may stay up while the printer's USB controller loses power.
                    self._post("/printer/firmware_restart")
                    recover_mcu = False
            time.sleep(2)
        raise RuntimeError("Klipper did not become ready after powering on")

    def power_off(self) -> None:
        if self.power is None:
            raise RuntimeError("No printer switch is configured")
        if not self._power_lock.acquire(blocking=False):
            raise RuntimeError("Printer power is already changing")
        try:
            power_state = self.power.poll()
            if power_state is PowerState.OFF:
                return
            if power_state is PowerState.UNKNOWN:
                raise RuntimeError("The printer switch is unreachable")
            status = self._idle_for_power_off()
            if status.nozzle[1] or status.bed[1]:
                self._gcode("TURN_OFF_HEATERS")
            deadline = time.monotonic() + POWER_OFF_TIMEOUT
            while time.monotonic() < deadline:
                status = self._idle_for_power_off()
                if status.nozzle[1] or status.bed[1]:
                    raise RuntimeError("A heater target is still set")
                if status.nozzle[0] <= POWER_OFF_TEMP:
                    self.power.set_power(False)
                    return
                time.sleep(2)
            raise RuntimeError("Hotend did not cool down in time")
        finally:
            self._power_lock.release()

    def _idle_for_power_off(self) -> PrinterState:
        self._require_queue_idle()
        status = self.poll()
        if not status.can_change_filament:
            raise RuntimeError("The printer must be idle and ready before turning off power")
        return status

    def _require_queue_idle(self) -> None:
        queue = self._get("/server/job_queue/status")
        if queue["queue_state"] in ("loading", "starting") or (
            queue["queue_state"] == "ready" and queue["queued_jobs"]
        ):
            raise RuntimeError("Moonraker is preparing a queued print")
