"""The D200 as Windows' Plug and Play sees it: is it stuck in boot mode, and restart it over USB.

    pnp = WindowsPnp()
    pnp.stuck()             # True when a D200 that hung while booting is present (USB 18D1:D002)
    pnp.restart_deck()      # (ok, message): restart the healthy deck's USB device, no admin needed

Both shell out to PowerShell / pnputil (slow: up to seconds), so callers must not call them in a tight loop.
device.Deck rate-limits stuck() itself; tests pass a fake with the same two methods.
"""
from __future__ import annotations

import subprocess

_NO_WINDOW = 0x08000000


def _ps(script: str, timeout: float = 15) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW).stdout


class WindowsPnp:
    def stuck(self) -> bool:
        try:
            out = _ps("Get-PnpDevice -PresentOnly | Where-Object InstanceId -match 'VID_18D1&PID_D002'")
        except Exception:
            return False
        return "18D1" in out.upper()

    def restart_deck(self) -> tuple[bool, str]:
        ids = _ps("(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match "
                  "'^USB.VID_2207&PID_0019.[^&]+$' }).InstanceId").split()
        if not ids:
            return False, "The deck isn't connected, or it's stuck in boot mode (then unplug it for 15 s)."
        r = subprocess.run(["pnputil", "/restart-device", ids[0]], capture_output=True, text=True,
                           creationflags=_NO_WINDOW)
        return (True, "Restarted the deck over USB.") if r.returncode == 0 else (False, (r.stdout + r.stderr).strip())
