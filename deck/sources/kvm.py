"""Move the desk (both monitors + the UGREEN USB switch) between this PC and the company laptop.

Interface:
    kvm = Kvm.from_config(cfg["kvm"])
    state = kvm.poll()              # KvmState: where the monitors are, where the USB switch is (never raises)
    kvm.switch("laptop")            # or "pc": both monitors at once over DDC/CI, fast; raises if one didn't switch
    note = kvm.switch_usb("laptop") # then the USB switch via the ESP32 (slow, confirmed): None, or what went wrong

Monitors: MCCS VCP 0x60 (input source) through Windows' dxva2, matched by the model in each monitor's DDC
capabilities string (Windows names both "Generic PnP Monitor"). Input codes live in deck.toml. These AOC monitors
report wrong values when the input is read back, so the desk side is the side the monitors were last switched to
(remembered across restarts), not something read from them.
USB switch: the ESP32 wired across the UGREEN's button (firmware/ugreen-button). When its LED inputs are wired it
selects a side and confirms; until then this module presses only when the switch is on the wrong side, judged
by whether the keyboard behind the switch is visible to this PC.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
HOSTS = ("pc", "laptop")


@dataclass(frozen=True)
class KvmState:
    here: str | None = None   # where the monitors are: "pc" | "laptop"
    usb: str | None = None    # where the USB switch is: "pc" | "laptop" | None (unknown)
    via: str = ""             # how `usb` is known: "leds" (ESP sees the switch's LEDs) | "usb" (keyboard visible?)
    esp_online: bool = False


@dataclass(frozen=True)
class MonitorInput:
    model: str               # as in the DDC caps string, e.g. "27G2" or "24G2HE5"
    pc: int                  # MCCS input code for this PC
    laptop: int              # and for the laptop


# ---------------- DDC/CI ----------------

class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("handle", wintypes.HANDLE), ("description", wintypes.WCHAR * 128)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


class Ddc:
    """Set monitor inputs by model name. Caps strings are slow to read (~1 s), so models are cached per display."""

    def __init__(self):
        self._user32 = ctypes.WinDLL("user32")
        self._dxva2 = ctypes.WinDLL("dxva2")
        self._models: dict[str, str] = {}

    @contextmanager
    def _monitors(self):
        """Every physical monitor as (key, handle), kept open until the block ends."""
        hmons: list = []
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                   wintypes.LPARAM)
        cb = proto(lambda hm, hdc, rc, lp: hmons.append(hm) or True)
        self._user32.EnumDisplayMonitors(None, None, cb, 0)
        opened, out = [], []
        try:
            for hm in hmons:
                info = _MONITORINFOEXW()
                info.cbSize = ctypes.sizeof(info)
                self._user32.GetMonitorInfoW(hm, ctypes.byref(info))
                n = wintypes.DWORD()
                if not self._dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hm, ctypes.byref(n)) or n.value == 0:
                    continue
                arr = (_PHYSICAL_MONITOR * n.value)()
                if not self._dxva2.GetPhysicalMonitorsFromHMONITOR(hm, n, arr):
                    continue
                opened.append((n, arr))
                out += [(f"{info.szDevice}#{i}", pm.handle) for i, pm in enumerate(arr)]
            yield out
        finally:
            for n, arr in opened:
                self._dxva2.DestroyPhysicalMonitors(n, arr)

    def _model(self, key: str, handle) -> str:
        if key not in self._models:
            n = wintypes.DWORD()
            caps = ""
            if self._dxva2.GetCapabilitiesStringLength(handle, ctypes.byref(n)) and n.value:
                buf = ctypes.create_string_buffer(n.value)
                if self._dxva2.CapabilitiesRequestAndCapabilitiesReply(handle, buf, n):
                    caps = buf.value.decode("ascii", "replace")
            i = caps.find("model(")
            self._models[key] = caps[i + 6: caps.find(")", i)] if i >= 0 else ""
        return self._models[key]

    def models(self) -> list[str]:
        with self._monitors() as mons:
            return [self._model(key, h) for key, h in mons]

    def set_inputs(self, codes: dict[str, int]) -> dict[str, bool]:
        """Send each monitor (by model) its input code, all at the same time. Returns which ones took it."""
        want = {m.lower(): m for m in codes}
        ok = {m: False for m in codes}

        def one(key, handle, model):
            ok[model] = bool(self._dxva2.SetVCPFeature(handle, ctypes.c_ubyte(0x60), wintypes.DWORD(codes[model])))
            if not ok[model]:
                self._models.pop(key, None)   # displays may have been renumbered; re-read caps next time

        with self._monitors() as mons:
            workers = []
            for key, handle in mons:
                model = want.get(self._model(key, handle).lower())
                if model is not None:
                    workers.append(threading.Thread(target=one, args=(key, handle, model)))
            for t in workers:
                t.start()
            for t in workers:
                t.join()
        return ok

    def set_input(self, model: str, code: int) -> bool:
        return self.set_inputs({model: code})[model]


# ---------------- ESP32 button presser ----------------

class Esp:
    def __init__(self, config_path: str):
        self.config_path = os.path.expandvars(config_path)

    def _cfg(self) -> dict:
        with open(self.config_path, encoding="utf-8") as f:
            return json.load(f)

    def call(self, method: str, path: str, timeout: float = 4) -> dict:
        cfg = self._cfg()
        last = None
        for host in [h for h in (cfg.get("ip"), cfg["hostname"] + ".local") if h]:
            req = urllib.request.Request(f"http://{host}:{cfg.get('port', 80)}{path}", method=method,
                                         data=b"" if method == "POST" else None,
                                         headers={"Authorization": "Bearer " + cfg["token"]})
            try:
                with _opener.open(req, timeout=timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                return json.load(e)
            except OSError as e:
                last = e
        raise ConnectionError(f"USB-switch controller unreachable: {last}")


# ---------------- the switch ----------------

class Kvm:
    def __init__(self, monitors: list[MonitorInput], presence: list[tuple[int, int]], esp: Esp | None,
                 ddc: Ddc | None = None, hid_module=None, sleep=time.sleep, side_file: Path | None = None):
        self.monitors = monitors
        self.presence = presence
        self.esp = esp
        self.ddc = ddc or Ddc()
        if hid_module is None:
            import hid as hid_module
        self._hid = hid_module
        self._sleep = sleep
        self._side_file = side_file
        self.side = "pc"
        if side_file is not None and side_file.is_file() and side_file.read_text().strip() in HOSTS:
            self.side = side_file.read_text().strip()
        self._warm = False

    @classmethod
    def from_config(cls, cfg: dict) -> "Kvm":
        from ..paths import STATE_DIR
        mons = [MonitorInput(m["model"], int(m["pc"]), int(m["laptop"])) for m in cfg.get("monitors", [])]
        presence = [tuple(int(x, 16) for x in s.split(":")) for s in cfg.get("presence", [])]
        esp = Esp(cfg["esp_config"]) if cfg.get("esp_config") else None
        return cls(mons, presence, esp, side_file=STATE_DIR / "desk-side.txt")

    def keyboard_here(self) -> bool:
        return any(self._hid.enumerate(vid, pid) for vid, pid in self.presence)

    def poll(self) -> KvmState:
        if not self._warm:               # read the monitors' caps once now, so the first switch doesn't pay ~1 s each
            self._warm = True
            try:
                self.ddc.models()
            except Exception:
                pass
        esp_online, usb, via = False, None, ""
        if self.esp is not None:
            try:
                st = self.esp.call("GET", "/status", timeout=2)
                esp_online = True
                if st.get("active") in HOSTS:
                    usb, via = st["active"], "leds"
            except Exception:
                pass
        if usb is None:
            try:
                usb, via = ("pc" if self.keyboard_here() else "laptop"), "usb"
            except Exception:
                pass
        return KvmState(self.side, usb, via, esp_online)

    def switch(self, to: str) -> None:
        """Both monitors at once. Coming back to the PC, a DisplayPort monitor may need a moment to reappear."""
        if to not in HOSTS:
            raise ValueError(f"switch to pc or laptop, not {to!r}")
        pending = {m.model: getattr(m, to) for m in self.monitors}
        for attempt in range(1 if to == "laptop" else 6):
            done = self.ddc.set_inputs(pending)
            pending = {m: c for m, c in pending.items() if not done[m]}
            if not pending:
                break
            self._sleep(1.0)
        if len(pending) < len(self.monitors):   # at least one moved: the desk is on that side now
            self.side = to
            if self._side_file is not None:
                self._side_file.parent.mkdir(parents=True, exist_ok=True)
                self._side_file.write_text(to)
        if pending:
            raise RuntimeError("monitor " + ", ".join(pending) + " didn't switch")

    def switch_usb(self, to: str) -> str | None:
        """Move the USB switch to `to` with the ESP32. None when it's there; otherwise a short reason."""
        if self.esp is None or not self.presence:
            return None
        try:
            st = self.esp.call("GET", "/status", timeout=3)
            if st.get("active") in HOSTS:       # LEDs wired: the ESP knows the state and confirms the change
                r = self.esp.call("POST", f"/select?to={to}", timeout=12)
                return None if r.get("ok") else f"USB: {r.get('error', 'failed')}"[:60]
            if self.keyboard_here() == (to == "pc"):
                return None                     # already on the right side
            r = self.esp.call("POST", "/press", timeout=5)
            if not r.get("ok"):
                return f"USB: {r.get('error', 'press failed')}"[:60]
            for _ in range(16):                 # confirm: the keyboard appears (to pc) or disappears (to laptop)
                self._sleep(0.5)
                if self.keyboard_here() == (to == "pc"):
                    return None
            return "USB switch didn't follow"
        except Exception as e:
            return f"USB: {e}"[:60]
