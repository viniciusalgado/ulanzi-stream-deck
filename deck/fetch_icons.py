"""Download the deck's icons into deck/assets/icons and record where each came from.

    .venv\\Scripts\\python -m deck.fetch_icons

Run it again after adding a name below. Icons are committed, so the daemon never needs the network for them.
Sources are pinned to exact versions so a re-run gives the same files.
"""
from __future__ import annotations

import io
import os
import sys
import urllib.request
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent / "assets" / "icons"

LUCIDE = "https://cdn.jsdelivr.net/npm/lucide-static@0.544.0/icons/{}.svg"        # ISC license
SIMPLE = "https://cdn.jsdelivr.net/npm/simple-icons@14.0.0/icons/{}.svg"          # CC0

# Generic UI glyphs (Lucide, stroke icons drawn in the tile's colour).
LUCIDE_NAMES = [
    "monitor", "laptop", "arrow-left-right", "gamepad-2", "bot", "printer", "mic", "mic-off", "square-plus",
    "message-square", "loader-circle", "circle-check", "circle-alert", "hand", "arrow-left", "server",
    "chevrons-right", "skip-back", "skip-forward", "play", "pause", "list-music", "music", "circle-stop",
    "thermometer", "camera", "layers", "volume-2", "volume-x", "unplug", "triangle-alert", "folder-git-2",
    "flame", "fan", "headphones", "bluetooth",
]
# Brand logos (Simple Icons, single-colour; the brand colour is applied when drawing).
SIMPLE_NAMES = ["dota2", "steam", "discord", "spotify", "youtube"]

def _mainsail() -> str:
    """The printer's Mainsail URL from deck.toml (it serves its own logo)."""
    try:
        from .config import load_config
        return load_config()["pages"]["print"]["mainsail"]
    except Exception:
        return "http://mainsail.local/"


# Official app icons taken from software already installed on this machine / the printer.
LOCAL = {
    "t3code": (os.path.expandvars(r"%LOCALAPPDATA%\Programs\t3code\resources\app.asar\apps\desktop\prod-resources\icon.ico"),
               "T3 Code desktop app icon (from the installed app's prod-resources/icon.ico)"),
    "cura": (r"C:\Program Files\UltiMaker Cura 5.12.0\share\cura\resources\images\cura-icon.png",
             "UltiMaker Cura icon (from the installed Cura 5.12 resources)"),
}
# Vector logos served by local devices (single-colour masks, tinted like the brand icons).
LOCAL_SVG = {
    "mainsail": (_mainsail() + "img/icons/safari-pinned-tab.svg",
                 "Mainsail logo mask (served by the printer's own Mainsail install)"),
}

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "deck-icon-fetch/1"})
    with _opener.open(req, timeout=15) as r:
        return r.read()


def _read_local(src: str) -> bytes:
    if src.startswith("http"):
        return _get(src)
    if ".asar" in src:  # files inside an Electron archive: read through Electron's own fs
        import subprocess
        exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\t3code\T3 Code (Beta).exe")
        js = f"process.stdout.write(require('fs').readFileSync({src!r}).toString('base64'))"
        import base64
        import os
        out = subprocess.run([exe, "-e", js], capture_output=True, env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"},
                             check=True).stdout
        return base64.b64decode(out)
    return Path(src).read_bytes()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    lines = ["# Icon sources", "", "Fetched by `python -m deck.fetch_icons`. Do not edit by hand; re-run the script.", "",
             "| file | source | license |", "|---|---|---|"]
    for n in LUCIDE_NAMES:
        (OUT / f"{n}.svg").write_bytes(_get(LUCIDE.format(n)))
        lines.append(f"| {n}.svg | {LUCIDE.format(n)} | ISC (Lucide) |")
    for n in SIMPLE_NAMES:
        (OUT / f"brand-{n}.svg").write_bytes(_get(SIMPLE.format(n)))
        lines.append(f"| brand-{n}.svg | {SIMPLE.format(n)} | CC0 (Simple Icons); trademarks belong to their owners |")
    for name, (src, what) in LOCAL.items():
        try:
            img = Image.open(io.BytesIO(_read_local(src)))
            if getattr(img, "n_frames", 1) > 1 or img.format == "ICO":
                img = max((img.ico.getimage(s) for s in img.ico.sizes()), key=lambda i: i.width) \
                    if img.format == "ICO" else img
            img.convert("RGBA").save(OUT / f"app-{name}.png")
            lines.append(f"| app-{name}.png | {what} | owner's trademark, local use only |")
        except Exception as e:  # keep going: a missing app icon falls back to a glyph
            print(f"skip {name}: {e}", file=sys.stderr)
    for name, (src, what) in LOCAL_SVG.items():
        try:
            (OUT / f"brand-{name}.svg").write_bytes(_read_local(src))
            lines.append(f"| brand-{name}.svg | {what} | GPL-3.0 project logo, local use only |")
        except Exception as e:
            print(f"skip {name}: {e}", file=sys.stderr)
    (OUT / "SOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
