"""Where the deck keeps things. Code and settings live next to the package; per-machine state in one folder.

STATE_DIR is the user's %APPDATA%\\ulanzi-deck (tokens, logs, caches) unless ULANZI_DECK_STATE says otherwise:
the deployed service and its monitor helper run as SYSTEM and keep theirs in SERVICE_STATE_DIR.
"""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent      # the repo, or C:\Program Files\UlanziDeck\app when deployed
CONFIG_FILE = REPO / "deck.toml"                 # yours; git ignores it
EXAMPLE_CONFIG = REPO / "deck.example.toml"     # the shared starting point (tests use it too)
STATE_DIR = Path(os.environ.get("ULANZI_DECK_STATE")
                 or Path(os.environ.get("APPDATA", Path.home())) / "ulanzi-deck")   # never in git
CACHE_DIR = STATE_DIR / "cache"
LOG_FILE = STATE_DIR / "deck.log"

PROGRAM_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "UlanziDeck"   # deck.deploy's copy
SERVICE_STATE_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ulanzi-deck"
DEPLOY_INFO = REPO / "deploy.json"                 # written by deck.deploy next to the deployed package
