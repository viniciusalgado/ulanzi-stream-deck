"""Bluetooth headphones (multipoint, e.g. Sony WH-1000XM5): is this PC connected for music, and "come back here".

Interface:
    hp = Headphones("WH-1000XM5")        # optional: audio= (a BtAudio, default WindowsBtAudio), sleep=, clock=
    state = hp.poll()      # HeadphoneState(found, connected, battery) (never raises)
    hp.reclaim(media)      # reconnect this PC's music link if it dropped; if it's up, re-take playback
    hp.reset(media)        # force: drop this PC's music link and reconnect it (for when reclaim isn't enough)

The Windows side sits behind three calls (`WindowsBtAudio`; tests pass a fake with the same methods):
    audio.endpoints(name) -> [Endpoint(name, state, filter_id)]   # the headphones' render endpoints
    audio.oneshot(filter_id, ONESHOT_RECONNECT | ONESHOT_DISCONNECT)
    audio.battery(filter_id) -> int | None

Reconnecting uses KSPROPERTY_ONESHOT_RECONNECT on the headphones' Bluetooth audio filter, the same call behind
the "Connect" button in Windows' Sound settings, so no admin rights or pairing changes are needed. The default
output device is never changed (this PC routes audio through VB-Audio Cable on purpose).
Multipoint: the headset plays whichever source started audio last, so after the reconnect the current music is
paused and resumed to make this PC the most recent source. Calls on the other machine still take priority.
"""
from __future__ import annotations

import ctypes
import functools
import logging
import time
from ctypes import POINTER, byref, c_ulong, c_void_p, c_wchar_p, wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEVICE_STATE_ACTIVE, DEVICE_STATE_NOTPRESENT, DEVICE_STATE_UNPLUGGED = 0x1, 0x4, 0x8
CLSCTX_ALL = 0x17
E_RENDER = 0
KSPROPERTY_TYPE_GET = 0x1
ONESHOT_RECONNECT, ONESHOT_DISCONNECT = 0, 1


@dataclass(frozen=True)
class HeadphoneState:
    found: bool = False
    connected: bool = False      # this PC's stereo (A2DP) link is up
    battery: int | None = None


@dataclass(frozen=True)
class Endpoint:
    name: str
    state: int           # DEVICE_STATE_* flags
    filter_id: str       # the Bluetooth audio filter it connects to (its service: 110b = A2DP, 111e = hands-free)


def music_endpoint(endpoints: list[Endpoint]) -> Endpoint | None:
    """The stereo (A2DP, 110b) endpoint. Windows keeps old ones around ("2- WH-1000XM5"): prefer the active one,
    then one that is merely disconnected, and a stale (not present) one only when there is nothing else."""
    music = [e for e in endpoints if "0000110b" in e.filter_id.lower()]
    rank = lambda e: 0 if e.state & DEVICE_STATE_ACTIVE else 1 if e.state & DEVICE_STATE_UNPLUGGED else 2
    return min(music, key=rank, default=None)


@functools.lru_cache(maxsize=1)
def _com():
    """COM interfaces, defined once and lazily so importing this module never needs comtypes."""
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator

    class KSPROPERTY(ctypes.Structure):
        _fields_ = [("Set", GUID), ("Id", c_ulong), ("Flags", c_ulong)]

    class IKsControl(IUnknown):
        _iid_ = GUID("{28F54685-06FD-11D2-B27A-00A0C9223196}")
        _methods_ = [
            COMMETHOD([], HRESULT, "KsProperty", (["in"], POINTER(KSPROPERTY), "p"), (["in"], c_ulong, "plen"),
                      (["in"], c_void_p, "data"), (["in"], c_ulong, "dlen"), (["out"], POINTER(c_ulong), "ret")),
        ]

    class IConnector(IUnknown):
        _iid_ = GUID("{9c2c4058-23f5-41de-877a-df3af236a09e}")

    IConnector._methods_ = [COMMETHOD([], HRESULT, n) for n in (
        "GetType", "GetDataFlow", "ConnectTo", "Disconnect", "IsConnected")] + [
        COMMETHOD([], HRESULT, "GetConnectedTo", (["out"], POINTER(POINTER(IConnector)), "to")),
        COMMETHOD([], HRESULT, "GetConnectorIdConnectedTo", (["out"], POINTER(c_wchar_p), "id")),
        COMMETHOD([], HRESULT, "GetDeviceIdConnectedTo", (["out"], POINTER(c_wchar_p), "id")),
    ]

    class IDeviceTopology(IUnknown):
        _iid_ = GUID("{2A07407E-6497-4A18-9787-32F79BD0D98F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetConnectorCount", (["out"], POINTER(c_ulong), "n")),
            COMMETHOD([], HRESULT, "GetConnector", (["in"], c_ulong, "i"), (["out"], POINTER(POINTER(IConnector)), "c")),
        ]

    return comtypes, GUID, IMMDeviceEnumerator, CLSID_MMDeviceEnumerator, KSPROPERTY, IKsControl, IDeviceTopology


KSPROPSETID_BTAUDIO = "{7FA06C40-B8F6-4C7E-8556-E8C33A12E54D}"


class Headphones:
    def __init__(self, name: str, *, audio=None, sleep=time.sleep, clock=time.monotonic):
        self.name = name
        self._audio = audio or WindowsBtAudio()
        self._sleep = sleep
        self._clock = clock

    # ----- reads -----

    def _state(self) -> HeadphoneState:
        """Raises when Windows can't be asked (unlike poll), so actions don't mistake that for "disconnected"."""
        ep = music_endpoint(self._audio.endpoints(self.name))
        if ep is None:
            return HeadphoneState()
        return HeadphoneState(True, bool(ep.state & DEVICE_STATE_ACTIVE), self._audio.battery(ep.filter_id))

    def poll(self) -> HeadphoneState:
        try:
            return self._state()
        except Exception as e:
            log.debug("headphones poll failed: %s", e)
            return HeadphoneState()

    # ----- actions -----

    def _oneshot(self, op: int) -> None:
        ep = music_endpoint(self._audio.endpoints(self.name))
        if ep is None:
            raise RuntimeError(f"{self.name} isn't paired with this PC")
        self._audio.oneshot(ep.filter_id, op)

    def _wait_connected(self, seconds: float) -> bool:
        end = self._clock() + seconds
        while self._clock() < end:
            if self.poll().connected:
                return True
            self._sleep(0.5)
        return False

    def reclaim(self, media=None) -> None:
        if not self._state().connected:
            self._oneshot(ONESHOT_RECONNECT)
            if not self._wait_connected(10):
                raise RuntimeError(f"{self.name} didn't connect (on and in range?)")
        self._retake(media)

    def reset(self, media=None) -> None:
        self._oneshot(ONESHOT_DISCONNECT)
        self._sleep(2.0)
        # A reconnect sent while the link is still going down can be ignored: ask once more before giving up, so a
        # reset never leaves this PC with no link at all.
        for wait in (6, 8):
            self._oneshot(ONESHOT_RECONNECT)
            if self._wait_connected(wait):
                self._retake(media)
                return
            log.info("%s didn't reconnect within %d s; asking again", self.name, wait)
        raise RuntimeError(f"{self.name} didn't reconnect")

    def _retake(self, media) -> None:
        """Pause and resume what's playing, so the headset switches to this PC as the latest audio source."""
        if media is None:
            return
        state = media.poll()
        if state.playing:
            media.toggle()
            self._sleep(0.8)
            media.toggle()


class WindowsBtAudio:
    """Core Audio + the Bluetooth audio driver's KS properties (COM, via comtypes / pycaw)."""

    def endpoints(self, name: str) -> list[Endpoint]:
        comtypes, GUID, IEnum, CLSID, _ks, _ctl, IDeviceTopology = _com()
        comtypes.CoInitialize()   # per thread; harmless when already initialised
        enum = comtypes.CoCreateInstance(CLSID, IEnum, comtypes.CLSCTX_INPROC_SERVER)
        coll = enum.EnumAudioEndpoints(E_RENDER, 0xF)
        out = []
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            try:
                topo = dev.Activate(byref(IDeviceTopology._iid_), CLSCTX_ALL, None).QueryInterface(IDeviceTopology)
                filter_id = topo.GetConnector(0).GetDeviceIdConnectedTo() or ""
            except Exception:
                continue
            if "bthenum" not in filter_id.lower() and "bthhfenum" not in filter_id.lower():
                continue
            friendly = _friendly_name(dev)
            if name.lower() in friendly.lower():
                out.append(Endpoint(friendly, dev.GetState(), filter_id))
        return out

    def oneshot(self, filter_id: str, op: int) -> None:
        comtypes, GUID, IEnum, CLSID, KSPROPERTY, IKsControl, _topo = _com()
        comtypes.CoInitialize()
        # The endpoint's connector leads to the Bluetooth audio KS filter; open that filter as a device and ask
        # it for IKsControl (what Windows' own "Connect" button does).
        enum = comtypes.CoCreateInstance(CLSID, IEnum, comtypes.CLSCTX_INPROC_SERVER)
        ks = enum.GetDevice(filter_id).Activate(byref(IKsControl._iid_), CLSCTX_ALL, None).QueryInterface(IKsControl)
        prop = KSPROPERTY(GUID(KSPROPSETID_BTAUDIO), op, KSPROPERTY_TYPE_GET)
        ks.KsProperty(byref(prop), ctypes.sizeof(prop), None, 0)

    def battery(self, filter_id: str) -> int | None:
        return _battery(filter_id)


def _friendly_name(dev) -> str:
    """'Fones de ouvido (2- WH-1000XM5)' style name; '' for stale endpoints whose properties are empty."""
    from comtypes import GUID
    from pycaw.api.mmdeviceapi.depend import PROPERTYKEY
    store = dev.OpenPropertyStore(0)
    for fmtid, pid in (("{a45c254e-df1c-4efd-8020-67d146a850e0}", 14),    # PKEY_Device_FriendlyName
                       ("{026E516E-B814-414B-83CD-856D6FEF4822}", 2)):    # PKEY_DeviceInterface_FriendlyName
        key = PROPERTYKEY()
        key.fmtid, key.pid = GUID(fmtid), pid
        try:
            value = store.GetValue(byref(key)).GetValue()
        except Exception:
            continue
        if value:
            return str(value)
    return ""


def _battery(filter_id: str) -> int | None:
    """Battery % that Windows reads over hands-free (shown in Bluetooth settings), if it knows it."""
    import re
    m = re.search(r"&([0-9a-f]{12})_", filter_id.lower())
    if not m:
        return None
    address = m.group(1).upper()
    cfg = ctypes.WinDLL("cfgmgr32")
    size = c_ulong()
    flt = "BTHENUM"
    if cfg.CM_Get_Device_ID_List_SizeW(byref(size), flt, 1) != 0:
        return None
    buf = ctypes.create_unicode_buffer(size.value)
    if cfg.CM_Get_Device_ID_ListW(flt, buf, size, 1) != 0:
        return None
    ids = [i for i in buf[:].split("\0") if address in i.upper() and "0000111E" in i.upper()]

    class DEVPROPKEY(ctypes.Structure):
        _fields_ = [("fmtid", ctypes.c_byte * 16), ("pid", c_ulong)]

    import uuid
    key = DEVPROPKEY()
    ctypes.memmove(key.fmtid, uuid.UUID("{104EA319-6EE2-4701-BD47-8DDBF425BBE5}").bytes_le, 16)
    key.pid = 2
    for inst in ids:
        node = wintypes.DWORD()
        if cfg.CM_Locate_DevNodeW(byref(node), inst, 0) != 0:
            continue
        ptype, value, vsize = c_ulong(), ctypes.c_ubyte(), c_ulong(1)
        if cfg.CM_Get_DevNode_PropertyW(node, byref(key), byref(ptype), byref(value), byref(vsize), 0) == 0:
            return int(value.value)
    return None
