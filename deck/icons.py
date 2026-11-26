"""Icon references -> RGBA images.

An icon is named by a short, hashable reference string, so tiles stay plain values:

    "lucide:<name>"            stroke glyph from assets/icons, drawn in the requested colour
    "brand:<name>"             Simple Icons logo, drawn in the requested colour
    "app:<name>"               full-colour app icon PNG from assets/icons
    "file:<path>"              any png/ico/svg on disk (project favicons)
    "mono:<letters>:<#rrggbb>" a coloured rounded square with letters (project fallback)
    "lucide-remote:<name>"     a Lucide glyph that isn't vendored; fetched once into the cache dir

`load(ref, size, color)` never raises: an unknown or broken icon comes back as a neutral placeholder.
"""
from __future__ import annotations

import functools
import hashlib
import io
import logging
import re
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

from . import fonts
from .paths import CACHE_DIR

ASSETS = Path(__file__).resolve().parent / "assets" / "icons"
LUCIDE_URL = "https://cdn.jsdelivr.net/npm/lucide-static@0.544.0/icons/{}.svg"
log = logging.getLogger(__name__)

# Colours T3 Code uses for project icons ("projectIcon": {"color": "gray"}).
NAMED_COLORS = {
    "gray": "#9ca3af", "red": "#f87171", "orange": "#fb923c", "amber": "#fbbf24", "yellow": "#facc15",
    "lime": "#a3e635", "green": "#4ade80", "emerald": "#34d399", "teal": "#2dd4bf", "cyan": "#22d3ee",
    "sky": "#38bdf8", "blue": "#60a5fa", "indigo": "#818cf8", "violet": "#a78bfa", "purple": "#c084fc",
    "fuchsia": "#e879f9", "pink": "#f472b6", "rose": "#fb7185",
}

# Same lookup order T3 Code uses to find a project's icon inside its folder.
FAVICON_CANDIDATES = [
    "favicon.svg", "favicon.ico", "favicon.png", "public/favicon.svg", "public/favicon.ico", "public/favicon.png",
    "app/favicon.ico", "app/favicon.png", "app/icon.svg", "app/icon.png", "app/icon.ico", "src/favicon.ico",
    "src/favicon.svg", "src/app/favicon.ico", "src/app/icon.svg", "src/app/icon.png", "assets/icon.svg",
    "assets/icon.png", "assets/logo.svg", "assets/logo.png", ".idea/icon.svg",
]

MONO_PALETTE = ["#f87171", "#fb923c", "#fbbf24", "#a3e635", "#34d399", "#22d3ee", "#60a5fa", "#a78bfa",
                "#f472b6", "#2dd4bf"]


def project_icon_ref(project_id: str, title: str, workspace_root: str | None,
                     project_icon: dict | None = None, favicon_path: str | None = None) -> str:
    """Pick the icon T3 Code would show for a project, falling back to a stable coloured monogram."""
    if project_icon and project_icon.get("kind") == "lucide" and project_icon.get("name"):
        color = NAMED_COLORS.get(project_icon.get("color") or "", project_icon.get("color") or "#9ca3af")
        return f"lucide-remote:{project_icon['name']}:{color}"
    root = Path(workspace_root) if workspace_root else None
    if favicon_path:
        p = Path(favicon_path)
        p = p if p.is_absolute() or root is None else root / p
        if p.is_file():
            return f"file:{p}"
    if root is not None:
        for c in FAVICON_CANDIDATES:
            p = root / c
            try:
                if p.is_file():
                    return f"file:{p}"
            except OSError:  # e.g. a WSL path that isn't reachable from Windows
                break
    words = re.findall(r"[A-Za-z0-9]+", title) or ["?"]
    letters = (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()
    color = MONO_PALETTE[int(hashlib.sha1(project_id.encode()).hexdigest(), 16) % len(MONO_PALETTE)]
    return f"mono:{letters}:{color}"


def load(ref: str, size: int, color: str = "#ffffff") -> Image.Image:
    """The icon as a size x size RGBA image (cached)."""
    try:
        return _load(ref, size, color).copy()
    except Exception as e:  # a bad icon must never break a whole screen
        log.warning("icon %s failed: %s", ref, e)
        return _placeholder(size)


@functools.lru_cache(maxsize=512)
def _load(ref: str, size: int, color: str) -> Image.Image:
    kind, _, rest = ref.partition(":")
    if kind == "lucide":
        return _svg((ASSETS / f"{rest}.svg").read_text(encoding="utf-8"), size, color, stroke=True)
    if kind == "lucide-remote":
        name, _, c = rest.partition(":")
        return _svg(_remote_lucide(name), size, c or color, stroke=True)
    if kind == "brand":
        return _svg((ASSETS / f"brand-{rest}.svg").read_text(encoding="utf-8"), size, color, stroke=False)
    if kind == "app":
        return _fit(Image.open(ASSETS / f"app-{rest}.png"), size)
    if kind == "file":
        p = Path(rest)
        if p.suffix.lower() == ".svg":
            return _svg(p.read_text(encoding="utf-8", errors="replace"), size, None, stroke=False)
        img = Image.open(p)
        if img.format == "ICO":
            img = max((img.ico.getimage(s) for s in img.ico.sizes()), key=lambda i: i.width)
        return _fit(img, size)
    if kind == "mono":
        letters, _, c = rest.partition(":")
        return _monogram(letters, size, c or "#9ca3af")
    raise ValueError(f"unknown icon ref {ref!r}")


def _svg(svg: str, size: int, color: str | None, stroke: bool) -> Image.Image:
    import resvg_py

    head, sep, body = svg.partition(">")
    if "viewBox" in head:  # size comes from us; drop width/height like "599pt" that resvg rejects
        head = re.sub(r'\s(width|height)="[^"]*"', "", head)
    svg = head + sep + body
    if color:
        if stroke:
            svg = svg.replace("currentColor", color)
        elif "fill=" not in svg.split(">", 1)[0]:
            svg = svg.replace("<svg", f'<svg fill="{color}"', 1)
    png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
    return _fit(Image.open(io.BytesIO(png)), size)


def _fit(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGBA")
    if img.size != (size, size):
        img.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
        img = canvas
    return img


def _monogram(letters: str, size: int, color: str) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 4, fill=color)
    f = fonts.get("bold", int(size * (0.5 if len(letters) > 1 else 0.6)))
    d.text((size / 2, size / 2 + 1), letters, font=f, fill="#111318", anchor="mm")
    return img


def _placeholder(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((size * .15, size * .15, size * .85, size * .85), radius=size // 6,
                                          outline="#6b7280", width=max(2, size // 16))
    return img


def _remote_lucide(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        raise ValueError(f"bad lucide name {name!r}")
    cached = CACHE_DIR / "icons" / f"{name}.svg"
    if cached.is_file():
        return cached.read_text(encoding="utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(LUCIDE_URL.format(name), timeout=10) as r:
        svg = r.read().decode("utf-8")
    if "<svg" not in svg:
        raise ValueError("not an svg")
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(svg, encoding="utf-8")
    return svg
