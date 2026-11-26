"""The Ulanzi D200 over USB HID, without the Ulanzi app.

Interface (all a caller needs):

    deck = Deck(on_key=lambda index, pressed: ...)   # index 0-12 = keys, 13 = the wide key
                                    # optional: hid_module=, pnp= (deck.pnp), clock= (monotonic seconds)
    deck.start()                    # background reader, keepalive and auto-reconnect
    deck.show({0: png, ..., 13: wide_png})   # what every key should show now; send it as often as you like
    deck.set_brightness(80)
    deck.close()

`show` sends a screen only when some key changed, at most MAX_FPS times a second (newer requests replace
older ones), and resends everything after a reconnect, so callers never track what's on the device.
Keys are 196x196 PNGs; the wide key is 458x196. The screen never sleeps: the deck stays unlocked and gets a
keepalive every few seconds.

Always whole screens, never the firmware's "partial update" command (0x000d): on this D200 a stream of partial
updates (webcam frames, animations) made the firmware slow down and then stop responding until it was restarted
(2026-11-25). Whole screens are what the long-running community drivers use.

Protocol (from redphx/strmdck and jcalado/companion-surface-d200, MIT; checked on this D200):
  every report is 1024 bytes: 7c 7c | cmd (2, big-endian) | length (4, little-endian) | data, zero padded.
  A screen is a zip (manifest.json + Images/*.png) split across consecutive reports. Firmware bug: the byte
  at 1016 + 1024*n of the zip must not be 0x00 or 0x7c, so a filler file is added until it isn't.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import random
import string
import threading
import time
import zipfile
from typing import Callable

VID, PID = 0x2207, 0x0019
STUCK_VID, STUCK_PID = 0x18D1, 0xD002   # how a D200 that hung while booting shows up
PACKET, HEADER = 1024, 8
COLS = 5
KEYS = 13            # 0-12; row 3 has 3 keys, then the wide key
WIDE = 13

CMD_SET_BUTTONS = 0x0001
CMD_SMALL_WINDOW = 0x0006
CMD_BRIGHTNESS = 0x000A
CMD_UNLOCK_SCREEN = 0x0010
IN_BUTTON = 0x0101
IN_DEVICE_INFO = 0x0303
SMALL_WINDOW_IMAGE = 2       # the wide key shows our picture, not the built-in clock/stats

KEEPALIVE_S = 5.0
RECONNECT_POLL_S = 0.5         # the deck shows its factory screen until we draw: notice it fast after a boot
STUCK_CHECK_S = 30.0           # while no deck is found, ask Windows whether it's stuck in boot mode this often
SETTLE_S = 1.0
MAX_FPS = 4                  # screens per second, at most
DEBOUNCE_S = 0.2             # a second "pressed" report for the same key this soon is a repeat, not a press

log = logging.getLogger(__name__)


def frame(cmd: int, length: int, data: bytes) -> bytes:
    if len(data) > PACKET - HEADER:
        raise ValueError("data too large for one report")
    return (b"\x7c\x7c" + cmd.to_bytes(2, "big") + length.to_bytes(4, "little") + data).ljust(PACKET, b"\x00")


def screen_zip(images: dict[int, bytes]) -> bytes:
    """manifest.json + Images/<hash>.png for the given slots, shaped so the firmware accepts it."""
    manifest, files = {}, {}
    for idx, png in sorted(images.items()):
        name = f"Images/{hashlib.sha1(png).hexdigest()[:16]}.png"   # the deck caches icons by file name
        files[name] = png
        if idx == WIDE:
            manifest["3_2"] = {"State": 0, "SmallViewMode": SMALL_WINDOW_IMAGE,
                               "ViewParam": [{"Icon": name, "Text": ""}]}
        else:
            manifest[f"{idx % COLS}_{idx // COLS}"] = {"State": 0, "ViewParam": [{"Icon": name, "Text": ""}]}
    filler = ""
    for _ in range(300):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            if filler:
                z.writestr("dummy.txt", filler)
            z.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
            for name, data in files.items():
                z.writestr(name, data)
        payload = buf.getvalue()
        if all(payload[i] not in (0x00, 0x7C) for i in range(PACKET - HEADER, len(payload), PACKET)):
            return payload
        filler += "".join(random.choices(string.ascii_letters, k=8))
    raise RuntimeError("could not build a zip the firmware accepts")


def chunk(cmd: int, payload: bytes) -> list[bytes]:
    first = frame(cmd, len(payload), payload[: PACKET - HEADER])
    rest = [payload[o: o + PACKET].ljust(PACKET, b"\x00") for o in range(PACKET - HEADER, len(payload), PACKET)]
    return [first, *rest]


def parse_report(r: bytes) -> tuple[str, object] | None:
    """('key', (index, pressed)) | ('info', text) | None."""
    if len(r) < HEADER or r[0] != 0x7C or r[1] != 0x7C:
        return None
    cmd = int.from_bytes(r[2:4], "big")
    length = int.from_bytes(r[4:8], "little")
    data = r[HEADER: HEADER + length]
    if cmd == IN_BUTTON and len(data) >= 4:
        return "key", (data[1], data[3] == 0x01)
    if cmd == IN_DEVICE_INFO:
        return "info", data.split(b"\x00")[0].decode("ascii", "replace")
    return None


class Deck:
    def __init__(self, on_key: Callable[[int, bool], None], *, brightness: int = 80,
                 hid_module=None, on_connect: Callable[[], None] | None = None, pnp=None,
                 clock: Callable[[], float] = time.monotonic):
        if hid_module is None:
            import hid as hid_module
        if pnp is None:
            from .pnp import WindowsPnp
            pnp = WindowsPnp()
        self._hid = hid_module
        self._pnp = pnp
        self._clock = clock
        self._on_key = on_key
        self._on_connect = on_connect
        self._brightness = brightness
        self._dev = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._sent: dict[int, bytes] = {}       # what the device shows now (valid only while connected)
        self._wanted: dict[int, bytes] = {}     # what callers last asked for
        self._last_send = float("-inf")
        self._pending = threading.Event()       # a newer screen is waiting for the frame-rate limit
        self._next_keepalive = clock() + KEEPALIVE_S
        self._last_press: dict[int, float] = {}
        self._warned_stuck = False
        self._next_stuck_check = float("-inf")
        self._unresponsive = False
        self.info = ""

    # ----- interface -----

    @property
    def connected(self) -> bool:
        return self._dev is not None

    def start(self) -> None:
        self._try_open()
        threading.Thread(target=self._read_loop, name="deck-read", daemon=True).start()
        threading.Thread(target=self._keepalive_loop, name="deck-keepalive", daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            dev, self._dev = self._dev, None
        if dev is not None:
            try:
                dev.close()
            except (OSError, ValueError):
                pass

    def show(self, images: dict[int, bytes]) -> None:
        with self._lock:
            self._wanted = dict(images)
            if self._dev is None or self._wanted == self._sent:
                return
            if self._clock() - self._last_send < 1 / MAX_FPS:
                self._pending.set()   # the keepalive thread sends it when the limit allows
                return
            self._send_full()

    def invalidate(self) -> None:
        """Forget what the device shows, so the next show() resends every key (e.g. after another app drew)."""
        with self._lock:
            self._sent = {}

    def set_brightness(self, percent: int) -> None:
        self._brightness = max(0, min(100, int(percent)))
        with self._lock:
            self._write([frame(CMD_BRIGHTNESS, len(b := str(self._brightness).encode()), b)])

    # ----- internals -----

    def _send_full(self) -> None:
        self._pending.clear()
        if not self._wanted:
            return
        wanted = dict(self._wanted)
        if self._write(chunk(CMD_SET_BUTTONS, screen_zip(wanted))):
            self._sent = wanted
            self._last_send = self._clock()

    def _write(self, packets: list[bytes]) -> bool:
        dev = self._dev
        if dev is None:
            return False
        try:
            for p in packets:
                if dev.write(b"\x00" + p) < 0:   # report id 0. hidapi returns -1 after a 1 s timeout; never raises
                    raise OSError("the deck didn't take the report within 1 s")
            if self._unresponsive:
                log.info("the deck answers again")
                self._unresponsive = False
            return True
        except (OSError, ValueError) as e:
            if not self._unresponsive:     # say it once, not on every retry
                log.warning("deck stopped responding (%s). If the screen stays frozen, its firmware hung: unplug it "
                            "for 15 s and plug it back in (a USB reset doesn't help)", e)
                self._unresponsive = True
            self._drop()
            return False

    def _small_window(self) -> list[bytes]:
        data = f"{SMALL_WINDOW_IMAGE}|0|0|{time.strftime('%H:%M:%S')}|0".encode()
        return [frame(CMD_SMALL_WINDOW, len(data), data)]

    def _try_open(self) -> bool:
        info = next((d for d in self._hid.enumerate(VID, PID)
                     if d.get("interface_number") == 0 and d.get("usage_page") in (0x0C, None)), None)
        if info is None:
            info = next((d for d in self._hid.enumerate(VID, PID) if d.get("interface_number") == 0), None)
        if info is None:
            self._check_stuck()
            return False
        dev = self._hid.device()
        try:
            dev.open_path(info["path"])
        except (OSError, ValueError) as e:
            log.info("deck found but can't be opened yet: %s", e)
            return False
        with self._lock:
            self._dev = dev
            self._sent = {}
        self._warned_stuck = False
        log.info("deck connected")
        return True

    def _check_stuck(self) -> None:
        """Asks Windows at most every STUCK_CHECK_S: the query takes seconds and runs on the reader thread, which
        retries every RECONNECT_POLL_S while the deck is unplugged."""
        now = self._clock()
        if self._warned_stuck or now < self._next_stuck_check:
            return
        self._next_stuck_check = now + STUCK_CHECK_S
        if self._pnp.stuck():
            log.warning("the deck is stuck in its boot mode (USB 18D1:D002). Unplug it for 15 s and plug it "
                        "straight into the PC.")
            self._warned_stuck = True

    def _drop(self) -> None:
        with self._lock:
            dev, self._dev = self._dev, None
            self._sent = {}
        if dev is not None:
            try:
                dev.close()
            except (OSError, ValueError):
                pass

    def _after_connect(self) -> None:
        if self._stop.wait(SETTLE_S):
            return
        with self._lock:
            self._write([frame(CMD_UNLOCK_SCREEN, 0, b"")])
            self._write([frame(CMD_BRIGHTNESS, len(b := str(self._brightness).encode()), b)])
            self._write(self._small_window())
            self._send_full()
        if self._on_connect:
            self._on_connect()

    def _read_loop(self) -> None:
        if self._dev is not None:
            self._after_connect()
        while not self._stop.is_set():
            dev = self._dev
            if dev is None:
                if self._stop.wait(RECONNECT_POLL_S):
                    return
                if self._try_open():
                    self._after_connect()
                continue
            try:
                r = dev.read(PACKET, 200)
            except (OSError, ValueError):
                if self._dev is dev:
                    log.info("deck disconnected")
                    self._drop()
                continue
            if r:
                self._handle_report(bytes(r))

    def _handle_report(self, r: bytes) -> None:
        parsed = parse_report(r)
        if parsed is None:
            return
        kind, value = parsed
        if kind == "key":
            index, pressed = value
            now = self._clock()
            if pressed:
                if now - self._last_press.get(index, float("-inf")) < DEBOUNCE_S:
                    log.info("ignored a repeated press report for key %d", index)
                    return
                self._last_press[index] = now
            try:
                self._on_key(index, pressed)
            except Exception:
                log.exception("key handler failed")
        elif value != self.info:
            self.info = value
            log.info("deck says: %s", value)

    def _service(self, now: float) -> float:
        """Does whatever is due at `now` (a screen the frame-rate limit held back, the keepalive) and returns the
        seconds until something is due again. The keepalive thread calls it; tests call it with a fake clock."""
        with self._lock:
            if self._pending.is_set():
                wait = 1 / MAX_FPS - (now - self._last_send)
                if self._dev is None:
                    self._pending.clear()
                elif wait > 0:
                    return min(wait, max(0.0, self._next_keepalive - now))
                else:
                    self._send_full()
            if now >= self._next_keepalive:
                if self._dev is not None:
                    self._write(self._small_window())
                self._next_keepalive = now + KEEPALIVE_S
            return max(0.0, self._next_keepalive - now)

    def _keepalive_loop(self) -> None:
        """Keeps the deck awake, and sends screens that the frame-rate limit held back."""
        while not self._stop.is_set():
            delay = self._service(self._clock())
            if self._pending.is_set():          # held back by the frame-rate limit: sleep that off
                if self._stop.wait(delay):
                    return
            else:                               # nothing waiting: sleep until the keepalive or a new screen
                self._pending.wait(timeout=delay)
