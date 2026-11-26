"""Fonts for tile text: Segoe UI from Windows, with a safe fallback."""
from __future__ import annotations

import functools
import os

from PIL import ImageFont

_WIN = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_FILES = {
    "regular": ["segoeui.ttf", "arial.ttf"],
    "semibold": ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"],
    "bold": ["segoeuib.ttf", "arialbd.ttf"],
}


@functools.lru_cache(maxsize=128)
def get(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for name in _FILES[weight]:
        path = os.path.join(_WIN, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)
