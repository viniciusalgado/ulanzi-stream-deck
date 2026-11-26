"""Where the deck keeps things. Code and settings live in the repo; per-machine state in one APPDATA folder."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO / "deck.toml"                 # yours; git ignores it
EXAMPLE_CONFIG = REPO / "deck.example.toml"     # the shared starting point (tests use it too)
STATE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ulanzi-deck"   # tokens, logs, caches; never in git
CACHE_DIR = STATE_DIR / "cache"
LOG_FILE = STATE_DIR / "deck.log"
