"""Tiles: what a key shows, as plain frozen values, and `render(tile) -> PNG bytes`.

Pages only build tiles; they never draw. Every tile kind shares one visual language (dark keys, one accent
colour per meaning), so a new key only needs a new tile value, not new drawing code. Rendering is cached on
the tile value, so unchanged keys cost nothing per frame.
"""
from __future__ import annotations

import functools
import io
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageOps

from . import fonts, icons

KEY = (196, 196)
WIDE = (458, 196)

BG = "#15171c"
FG = "#eceef1"
MUTED = "#9aa0a8"
FAINT = "#3a3f48"
BLUE = "#60a5fa"     # working / in progress
AMBER = "#fbbf24"    # needs you
RED = "#f87171"      # failed / destructive
GREEN = "#4ade80"    # ready / active / on
STATE_COLORS = {"working": BLUE, "attention": AMBER, "error": RED, "ready": GREEN}


@dataclass(frozen=True)
class Blank:
    pass


@dataclass(frozen=True)
class AppTile:
    """An icon with a label: launchers, actions and page buttons."""
    icon: str | None
    label: str = ""
    sub: str = ""
    color: str = FG            # glyph colour (ignored by full-colour app icons)
    bg: str = BG
    accent: str | None = None  # bar along the bottom edge (selected page, toggled on)
    badge: str | None = None   # small pill in the top-right corner
    badge_color: str = AMBER
    dim: bool = False          # unavailable right now
    icon_scale: float = 0.46


@dataclass(frozen=True)
class ArtTile:
    """A full-bleed picture with a caption: album / playlist covers, the printer camera."""
    art: bytes | None
    label: str = ""
    sub: str = ""
    glyph: str | None = None        # icon drawn over the picture (play / pause)
    fallback: str = "lucide:music"  # shown when there's no picture
    accent: str | None = None


@dataclass(frozen=True)
class ThreadTile:
    """A T3 Code thread: which project, what it's about, and whether it needs you."""
    title: str
    project: str
    project_icon: str
    state: str          # working | attention | error | ready
    status: str         # short footer text, e.g. "Working 3m"
    phase: int = 0      # animation step for working threads


@dataclass(frozen=True)
class InfoTile:
    """A live readout: header, one big value, a couple of lines and an optional progress bar."""
    header: str
    value: str = ""
    lines: tuple[str, ...] = ()
    progress: float | None = None
    accent: str = BLUE
    icon: str | None = None


@dataclass(frozen=True)
class SwitchTile:
    """The wide key: two machines, the active one lit."""
    left: tuple[str, str]        # (label, icon ref)
    right: tuple[str, str]
    active: str | None           # "left" | "right" | None (unknown)
    busy: bool = False
    message: str = ""
    message_color: str = MUTED
    active_note: str = "ACTIVE"   # e.g. "keyboard away" when the state is only inferred


@dataclass(frozen=True)
class Flashed:
    """Feedback drawn over any tile while its action runs (busy) and just after (ok / fail), or while a long
    press fills up toward its action (charge: a ring at `progress` with `label`)."""
    tile: object
    kind: str  # busy | ok | fail | hold | charge
    progress: float = 0.0
    label: str = ""


@functools.lru_cache(maxsize=256)
def render(tile) -> bytes:
    img = _draw(tile).convert("RGB")
    base = tile.tile if isinstance(tile, Flashed) else tile
    if isinstance(base, ArtTile) and base.art:
        # photos (covers, webcam) as a 96-colour palette: about a quarter of the bytes, same look on a 2" key
        img = img.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _draw(tile) -> Image.Image:
    if isinstance(tile, Flashed):
        if tile.kind == "charge":
            return _charge(_draw(tile.tile), tile.progress, tile.label)
        return _flash(_draw(tile.tile), tile.kind)
    fn = {Blank: _blank, AppTile: _app, ArtTile: _art, ThreadTile: _thread, InfoTile: _info,
          SwitchTile: _switch}[type(tile)]
    return fn(tile)


# ---------- helpers ----------

def clean(text: str) -> str:
    """Drop characters Segoe UI can't draw (emoji, variation selectors) and tidy spaces."""
    out = "".join(ch for ch in text if ord(ch) <= 0xFFFF and not 0xFE00 <= ord(ch) <= 0xFE0F
                  and not 0x2600 <= ord(ch) <= 0x27BF and ch != "‍")
    return " ".join(out.split())


def _wrap(d: ImageDraw.ImageDraw, text: str, font, width: int, max_lines: int) -> list[str]:
    words, lines, cur = clean(text).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if d.textlength(trial, font=font) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = w
        while d.textlength(cur, font=font) > width:  # a single word wider than the key
            cut = len(cur)
            while cut > 1 and d.textlength(cur[:cut], font=font) > width:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and d.textlength(last + "…", font=font) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _ellipsize(d, text: str, font, width: int) -> str:
    return (_wrap(d, text, font, width, 1) or [""])[0]


def _fit_font(d, text: str, weight: str, size: int, width: int, min_size: int = 14):
    while size > min_size and d.textlength(text, font=fonts.get(weight, size)) > width:
        size -= 1
    return fonts.get(weight, size)


def _new(size=KEY, bg=BG) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", size, bg)
    return img, ImageDraw.Draw(img)


def _paste_icon(img: Image.Image, ref: str, size: int, center: tuple[int, int], color: str, dim=False):
    ic = icons.load(ref, size, color)
    if dim:
        ic.putalpha(ic.getchannel("A").point(lambda a: a * 0.35))
    img.alpha_composite(ic, (center[0] - size // 2, center[1] - size // 2))


def _badge(d, text: str, color: str, right: int):
    f = fonts.get("bold", 20)
    w = max(30, int(d.textlength(text, font=f)) + 16)
    d.rounded_rectangle((right - w - 8, 8, right - 8, 38), 15, fill=color)
    d.text((right - 8 - w / 2, 23), text, font=f, fill="#111318", anchor="mm")


def _mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


# ---------- tile kinds ----------

def _blank(t: Blank) -> Image.Image:
    return _new()[0]


def _app(t: AppTile) -> Image.Image:
    img, d = _new(bg=t.bg)
    w, h = KEY
    has_text = bool(t.label)
    size = int(w * t.icon_scale)
    cy = 78 if has_text else h // 2
    if t.icon:
        _paste_icon(img, t.icon, size, (w // 2, cy), t.color, t.dim)
    fg = MUTED if t.dim else FG
    if t.label:
        f = _fit_font(d, clean(t.label), "semibold", 26, w - 16)
        y = 150 if not t.sub else 140
        d.text((w / 2, y), _ellipsize(d, t.label, f, w - 14), font=f, fill=fg, anchor="mm")
    if t.sub:
        f = fonts.get("regular", 18)
        d.text((w / 2, 168), _ellipsize(d, t.sub, f, w - 14), font=f, fill=MUTED, anchor="mm")
    if t.accent:
        d.rounded_rectangle((28, h - 12, w - 28, h - 4), 4, fill=t.accent)
    if t.badge:
        _badge(d, t.badge, t.badge_color, w)
    return img


def _art(t: ArtTile) -> Image.Image:
    img, d = _new()
    w, h = KEY
    if t.art:
        try:
            pic = ImageOps.fit(Image.open(io.BytesIO(t.art)).convert("RGBA"), KEY, Image.LANCZOS)
            img.alpha_composite(pic)
        except Exception:
            _paste_icon(img, t.fallback, 90, (w // 2, 80), MUTED)
    else:
        _paste_icon(img, t.fallback, 90, (w // 2, 80), MUTED)
    if t.label or t.sub:  # readable caption band
        band = Image.new("RGBA", (w, 64 if t.sub else 46), (12, 13, 16, 205))
        img.alpha_composite(band, (0, h - band.height))
        f = fonts.get("semibold", 21)
        y = h - band.height + (20 if t.sub else 23)
        d.text((w / 2, y), _ellipsize(d, t.label, f, w - 14), font=f, fill=FG, anchor="mm")
        if t.sub:
            f2 = fonts.get("regular", 17)
            d.text((w / 2, h - 16), _ellipsize(d, t.sub, f2, w - 14), font=f2, fill=MUTED, anchor="mm")
    if t.glyph:
        d.ellipse((w / 2 - 36, 44, w / 2 + 36, 116), fill=(12, 13, 16, 170))
        _paste_icon(img, t.glyph, 40, (w // 2, 80), FG)
    if t.accent:
        d.rounded_rectangle((0, 0, w - 1, h - 1), 6, outline=t.accent, width=4)
    return img


def _thread(t: ThreadTile) -> Image.Image:
    img, d = _new()
    w, h = KEY
    color = STATE_COLORS.get(t.state, MUTED)
    # header: project icon + name, so it's clear where the thread lives
    _paste_icon(img, t.project_icon, 30, (25, 25), MUTED)
    f = fonts.get("semibold", 18)
    d.text((46, 25), _ellipsize(d, t.project, f, w - 56), font=f, fill=MUTED, anchor="lm")
    # title: what it's about
    ft = fonts.get("semibold", 23)
    lines = _wrap(d, t.title, ft, w - 22, 3)
    y = 52
    for line in lines:
        d.text((11, y), line, font=ft, fill=FG, anchor="la")
        y += 29
    # footer: state
    fs = fonts.get("bold", 19)
    if t.state == "working":
        d.rounded_rectangle((10, 160, w - 10, 186), 13, fill="#1c2a3d")
        seg = (w - 20) * 0.35
        x = 10 + ((t.phase % 8) / 8) * (w - 20 + seg) - seg
        d.rounded_rectangle((max(10, x), 160, min(w - 10, x + seg), 186), 13, fill="#2d4f7c")
        d.text((w / 2, 173), t.status, font=fs, fill=BLUE, anchor="mm")
        d.rounded_rectangle((1, 1, w - 2, h - 2), 8, outline=BLUE, width=3)
    else:
        pill_bg = {"attention": AMBER, "error": RED}.get(t.state)
        if pill_bg:
            d.rounded_rectangle((10, 160, w - 10, 186), 13, fill=pill_bg)
            d.text((w / 2, 173), t.status, font=fs, fill="#111318", anchor="mm")
            d.rounded_rectangle((1, 1, w - 2, h - 2), 8, outline=pill_bg, width=5)
        else:
            d.ellipse((14, 165, 30, 181), fill=color)
            d.text((38, 173), _ellipsize(d, t.status, fs, w - 48), font=fs, fill=color, anchor="lm")
    return img


def _info(t: InfoTile) -> Image.Image:
    img, d = _new()
    w, h = KEY
    x0 = 12
    if t.icon:
        _paste_icon(img, t.icon, 26, (24, 24), t.accent)
        x0 = 42
    f = fonts.get("bold", 20)
    d.text((x0, 24), _ellipsize(d, t.header, f, w - x0 - 10), font=f, fill=t.accent, anchor="lm")
    if t.value:
        fv = _fit_font(d, t.value, "bold", 50, w - 20, 24)
        d.text((w / 2, 78), t.value, font=fv, fill=FG, anchor="mm")
    fl = fonts.get("regular", 18)
    y = 116 if t.value else 56
    for line in t.lines[:3 if t.value else 5]:
        d.text((w / 2, y), _ellipsize(d, line, fl, w - 16), font=fl, fill=MUTED, anchor="mm")
        y += 23
    if t.progress is not None:
        p = max(0.0, min(1.0, t.progress))
        d.rounded_rectangle((14, 176, w - 14, 186), 5, fill=FAINT)
        if p > 0:
            d.rounded_rectangle((14, 176, 14 + (w - 28) * p, 186), 5, fill=t.accent)
    return img


def _switch(t: SwitchTile) -> Image.Image:
    img, d = _new(WIDE)
    w, h = WIDE
    half = (w - 58) // 2
    for side, (label, ref), x0 in (("left", t.left, 6), ("right", t.right, w - 6 - half)):
        on = t.active == side
        box = (x0, 8, x0 + half, h - 8)
        d.rounded_rectangle(box, 14, fill="#173524" if on else "#1d2027", outline=GREEN if on else None,
                            width=3)
        cx = x0 + half // 2
        _paste_icon(img, ref, 70, (cx, 72), GREEN if on else MUTED)
        f = fonts.get("bold", 30)
        d.text((cx, 140), label, font=f, fill=FG if on else MUTED, anchor="mm")
        fs = fonts.get("semibold", 17)
        d.text((cx, 168), t.active_note if on else "press to switch", font=fs, fill=GREEN if on else FAINT,
               anchor="mm")
    _paste_icon(img, "lucide:arrow-left-right", 40, (w // 2, 80), BLUE if t.busy else MUTED)
    if t.busy or t.message:
        text = "Switching…" if t.busy else t.message
        f = fonts.get("bold", 22)
        tw = d.textlength(text, font=f) + 30
        d.rounded_rectangle((w / 2 - tw / 2, h - 46, w / 2 + tw / 2, h - 12), 17,
                            fill="#1c2a3d" if t.busy else "#2a1d1f")
        d.text((w / 2, h - 29), text, font=f, fill=BLUE if t.busy else t.message_color, anchor="mm")
    return img


def _flash(img: Image.Image, kind: str) -> Image.Image:
    img = img.copy()
    d = ImageDraw.Draw(img)
    w, h = img.size
    color = {"busy": BLUE, "ok": GREEN, "fail": RED, "hold": AMBER}[kind]
    d.rounded_rectangle((1, 1, w - 2, h - 2), 8, outline=color, width=6)
    r = 22
    cx, cy = w - r - 8, r + 8
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    if kind == "busy":
        for i in range(3):
            a = math.radians(i * 120)
            d.ellipse((cx + 9 * math.cos(a) - 3, cy + 9 * math.sin(a) - 3, cx + 9 * math.cos(a) + 3,
                       cy + 9 * math.sin(a) + 3), fill="#111318")
    elif kind == "ok":
        d.line((cx - 10, cy, cx - 3, cy + 8, cx + 11, cy - 8), fill="#111318", width=5)
    elif kind == "fail":
        d.line((cx - 8, cy - 8, cx + 8, cy + 8), fill="#111318", width=5)
        d.line((cx - 8, cy + 8, cx + 8, cy - 8), fill="#111318", width=5)
    else:  # hold
        d.text((cx, cy), "…", font=fonts.get("bold", 26), fill="#111318", anchor="mm")
    return img


def _charge(img: Image.Image, progress: float, label: str) -> Image.Image:
    """The tile dimmed under a ring that fills clockwise from the top; full = it fires on this frame."""
    p = max(0.0, min(1.0, progress))
    img = Image.blend(img.convert("RGB"), Image.new("RGB", img.size, "#0b0c0f"), 0.72)
    d = ImageDraw.Draw(img)
    w, h = img.size
    cx, cy, r = w / 2, h / 2 - 16, 54
    box = (cx - r, cy - r, cx + r, cy + r)
    d.arc(box, 0, 360, fill=FAINT, width=12)
    if p > 0:
        d.arc(box, -90, -90 + 360 * p, fill=GREEN, width=12)
    done = p >= 1.0
    d.line((cx - 20, cy + 1, cx - 6, cy + 15, cx + 22, cy - 13), fill=GREEN if done else FG, width=8,
           joint="curve")
    d.text((cx, h - 30), label, font=fonts.get("bold", 24), fill=GREEN if done else FG, anchor="mm")
    d.rounded_rectangle((1, 1, w - 2, h - 2), 8, outline=GREEN if done else FAINT, width=4)
    return img
