"""Local control of the relay that powers the printer."""
from __future__ import annotations

import threading
from enum import Enum


class PowerState(Enum):
    UNKNOWN = "unreachable"
    OFF = "off"
    ON = "on"


class TuyaSwitch:
    def __init__(self, *, device_id: str, address: str, local_key: str, version: float, dps: int = 1):
        from tinytuya import OutletDevice

        if not device_id or not address or len(local_key.encode()) != 16:
            raise ValueError("The printer switch needs a device ID, IP address, and 16-byte local key")
        self._device = OutletDevice(device_id, address, local_key, version=version, connection_timeout=2,
                                    connection_retry_limit=1, send_retry_limit=1)
        self._dps = dps
        self._lock = threading.Lock()

    def _read(self) -> PowerState:
        response = self._device.status()
        value = response.get("dps", {}).get(str(self._dps)) if isinstance(response, dict) else None
        if value is True:
            return PowerState.ON
        if value is False:
            return PowerState.OFF
        return PowerState.UNKNOWN

    def poll(self) -> PowerState:
        try:
            with self._lock:
                return self._read()
        except Exception:
            return PowerState.UNKNOWN

    def set_power(self, on: bool) -> None:
        try:
            with self._lock:
                self._device.set_status(on, switch=self._dps)
                if self._read() is (PowerState.ON if on else PowerState.OFF):
                    return
        except Exception:
            pass
        raise RuntimeError("The printer switch did not confirm the requested power state")
