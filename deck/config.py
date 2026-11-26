"""deck.toml -> dict. The only place settings are read from disk."""
from __future__ import annotations

import tomllib
from pathlib import Path

from .paths import CONFIG_FILE, EXAMPLE_CONFIG

REQUIRED = ("deck", "pages")   # plugins check their own sections


def load_config(path: Path = CONFIG_FILE) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: copy {EXAMPLE_CONFIG.name} to {path.name} and edit it")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        raise ValueError(f"{path} is missing sections: {', '.join(missing)}")
    return cfg
