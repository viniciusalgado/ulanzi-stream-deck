"""Start a Spotify playlist without the Web API: open it, check the right page is showing, press its Play button.

Interface:
    start_playlist(uri, cover)   # cover = the playlist's cover image bytes (to recognise its page); raises on failure

Spotify's desktop app can only *open* a playlist from outside (tested 2026-11-25: ":play" / "?play=true" links don't
start it, there is no "play this page" shortcut, and the Store app exposes no page content to UI Automation).
So this does what a person would: bring Spotify forward, wait until the page header shows this playlist's cover
(Spotify takes ~2-3 s to switch pages, and clicking earlier would play the previous page), find the big green
Play button below the header, and click it, putting the mouse back afterwards. If the button already shows
Pause, the playlist is playing and nothing is clicked.

The pure parts (`find_play_button`, `cover_on_page`) take a PIL image of the window, so they are tested on
screenshots.
"""
from __future__ import annotations

import ctypes
import io
import logging
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageGrab, ImageStat

from .. import winutil

log = logging.getLogger(__name__)
SPOTIFY_EXE = "Spotify.exe"


@dataclass(frozen=True)
class PlayButton:
    box: tuple[int, int, int, int]   # left, top, right, bottom in window pixels
    playing: bool                    # shows Pause: this page's playlist is already playing

    @property
    def center(self) -> tuple[int, int]:
        return (self.box[0] + self.box[2]) // 2, (self.box[1] + self.box[3]) // 2


def _is_green(p) -> bool:
    r, g, b = p[:3]
    return g > 170 and r < 120 and g - r > 90 and g - b > 50   # Spotify green #1ED760 and its hover shades


def find_play_button(img: Image.Image, scale: float = 1.0) -> PlayButton | None:
    """The page's big green Play/Pause circle: the largest round green blob in the main area of the window."""
    img = img.convert("RGB")
    w, h = img.size
    step = 3
    small = img.resize((w // step, h // step), Image.NEAREST)
    sw, sh = small.size
    px = small.load()
    seen = bytearray(sw * sh)
    best = None
    for y in range(sh):
        for x in range(sw):
            i = y * sw + x
            if seen[i] or not _is_green(px[x, y]):
                continue
            stack, cells = [(x, y)], []          # flood fill one green blob
            seen[i] = 1
            while stack:
                cx, cy = stack.pop()
                cells.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < sw and 0 <= ny < sh and not seen[ny * sw + nx] and _is_green(px[nx, ny]):
                        seen[ny * sw + nx] = 1
                        stack.append((nx, ny))
            xs, ys = [c[0] for c in cells], [c[1] for c in cells]
            bw, bh = (max(xs) - min(xs) + 1) * step, (max(ys) - min(ys) + 1) * step
            fill = len(cells) * step * step / (bw * bh)
            if not (40 * scale <= bw <= 96 * scale and 0.8 <= bw / bh <= 1.25 and 0.5 <= fill <= 0.9):
                continue                              # not a big filled circle (shuffle icon, ticks, text)
            box = (min(xs) * step, min(ys) * step, (max(xs) + 1) * step, (max(ys) + 1) * step)
            if box[0] < w * 0.08 or box[1] > h * 0.8:
                continue                              # sidebar icons / player bar
            if best is None or bw * bh > (best[2] - best[0]) * (best[3] - best[1]):
                best = box
    if best is None:
        return None
    return PlayButton(best, _shows_pause(img, best))


def _shows_pause(img: Image.Image, box) -> bool:
    """Pause = two bars with a gap down the middle; Play = a triangle that covers the middle column."""
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    px = img.load()
    def dark(x, y):
        r, g, b = px[x, y][:3]
        return r + g + b < 200
    band = range(y0 + (y1 - y0) // 3, y1 - (y1 - y0) // 3)
    middle = sum(dark(x, y) for x in range(cx - 1, cx + 2) for y in band)
    sides = sum(dark(x, y) for x in range(x0 + (x1 - x0) // 4, x1 - (x1 - x0) // 4) for y in band)
    return sides > 0 and middle < 0.02 * sides * 3


def cover_on_page(img: Image.Image, button: PlayButton, cover: bytes) -> float:
    """How well the header's cover art matches `cover` (0 = identical, 1 = unrelated).

    The cover sits above the Play button, left-aligned with it; its size depends on the window width.
    """
    ref = Image.open(io.BytesIO(cover)).convert("L").resize((16, 16), Image.BILINEAR)
    x0, y0 = button.box[0], button.box[1]
    best = 1.0
    gray = img.convert("L")
    for size in range(96, 260, 6):
        for gap in range(16, 60, 4):
            top = y0 - gap - size
            if top < 0:
                continue
            for dx in (-4, 0, 4):
                crop = gray.crop((x0 + dx, top, x0 + dx + size, top + size)).resize((16, 16), Image.BILINEAR)
                diff = sum(ImageStat.Stat(ImageChops.difference(crop, ref)).mean) / 255
                best = min(best, diff)
    return best


# ---------------- the live part ----------------

def _window_image(hwnd) -> tuple[Image.Image, tuple[int, int]]:
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True), (r.left, r.top)


def _click(x: int, y: int) -> None:
    user32 = ctypes.windll.user32
    old = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(old))
    user32.SetCursorPos(x, y)
    time.sleep(0.03)
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # left down
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # left up
    time.sleep(0.03)
    user32.SetCursorPos(old.x, old.y)


def start_playlist(uri: str, cover: bytes | None, timeout: float = 8.0) -> bool:
    """Open `uri` in Spotify and press Play on its page. True if clicked, False if it was already playing.

    Whatever window was in front before gets the focus back afterwards.
    """
    previous = winutil.foreground()
    try:
        return _start(uri, cover, timeout)
    finally:
        if previous != winutil._window_for_exe(SPOTIFY_EXE):
            winutil.give_back_focus(previous)


def _start(uri: str, cover: bytes | None, timeout: float) -> bool:
    winutil.launch(uri)
    deadline = time.monotonic() + (timeout if winutil._window_for_exe(SPOTIFY_EXE) else timeout + 12)
    last = None
    while time.monotonic() < deadline:
        time.sleep(0.35)
        hwnd = winutil._window_for_exe(SPOTIFY_EXE)
        if not hwnd:
            continue
        try:
            winutil.focus_window(SPOTIFY_EXE)
        except RuntimeError:
            continue
        scale = ctypes.windll.user32.GetDpiForWindow(hwnd) / 96
        img, origin = _window_image(hwnd)
        button = find_play_button(img, scale)
        if button is None:
            last = "no Play button on the page"
            continue
        if cover is not None:
            match = cover_on_page(img, button, cover)
            if match > 0.06:
                last = f"page not showing this playlist yet (cover diff {match:.2f})"
                continue
        if button.playing:
            log.info("playlist %s is already playing", uri)
            return False
        x, y = button.center
        _click(origin[0] + x, origin[1] + y)
        log.info("pressed Play for %s", uri)
        return True
    raise RuntimeError(f"couldn't start the playlist: {last or 'Spotify did not open'}")
