"""The Spotify playlists you've played, newest first, read from the Spotify app's own files (no account needed).

    played = PlayedPlaylists()        # finds the Spotify app's user folders (Store app or classic install)
    played.recent() -> ["spotify:playlist:...", ...]     # newest first

Spotify keeps two small protobuf files per user:
    list_player_play_history   the last ~100 tracks played, each with its context (the playlist) and a time
    frecency.pb                the "Recents" shelf: every context played, with when it was last played
Neither goes back forever (the play history drops old tracks, and newer app versions stop updating frecency.pb),
so what they say is merged into STATE_DIR/played-playlists.json and kept: a playlist stays listed once seen.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
from pathlib import Path

from ..paths import STATE_DIR

log = logging.getLogger(__name__)
FILE = STATE_DIR / "played-playlists.json"
PLAYLIST = re.compile(r"^spotify:playlist:[A-Za-z0-9]{22}$")
USER_DIRS = (r"%LOCALAPPDATA%\Packages\SpotifyAB.SpotifyMusic_*\LocalState\Spotify\Users\*-user",   # Store app
             r"%APPDATA%\Spotify\Users\*-user", r"%LOCALAPPDATA%\Spotify\Users\*-user")         # classic install


# ----- a minimal protobuf reader (wire format only; the two files are flat enough for that) -----

def _varint(b: bytes, i: int) -> tuple[int, int]:
    out = shift = 0
    while True:
        c = b[i]
        i += 1
        out |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return out, i


def _fields(b: bytes) -> list[tuple[int, object]]:
    """(field number, value) pairs; a length-delimited value comes back as bytes."""
    i, out = 0, []
    while i < len(b):
        key, i = _varint(b, i)
        num, kind = key >> 3, key & 7
        if kind == 0:
            value, i = _varint(b, i)
        elif kind == 1:
            value, i = b[i:i + 8], i + 8
        elif kind == 5:
            value, i = b[i:i + 4], i + 4
        elif kind == 2:
            n, i = _varint(b, i)
            value, i = b[i:i + n], i + n
        else:
            raise ValueError(f"unsupported wire type {kind}")
        if i > len(b):
            raise ValueError("truncated")
        out.append((num, value))
    return out


def _entries(data: bytes):
    """Field 1 of the top-level message, repeated: one dict {field: value} per entry; damaged ones skipped."""
    try:
        top = _fields(data)
    except (ValueError, IndexError):
        return
    for num, value in top:
        if num == 1 and isinstance(value, bytes):
            try:
                yield dict(_fields(value))
            except (ValueError, IndexError):
                continue


def _latest(found: dict[str, float], uri: object, when: object) -> None:
    if isinstance(uri, bytes):
        uri = uri.decode("utf-8", "replace")
    if isinstance(uri, str) and PLAYLIST.match(uri) and isinstance(when, int) and when > 0:
        found[uri] = max(found.get(uri, 0.0), float(when))


def read_play_history(data: bytes) -> dict[str, float]:
    """list_player_play_history: entry = {1: track uri, 2: context uri, 4: {1: seconds, 2: nanos}, ...}."""
    found: dict[str, float] = {}
    for e in _entries(data):
        try:
            when = dict(_fields(e[4])).get(1) if isinstance(e.get(4), bytes) else None
        except (ValueError, IndexError):
            continue
        _latest(found, e.get(2), when)
    return found


def read_frecency(data: bytes) -> dict[str, float]:
    """frecency.pb: entry = {1: context uri, 2: {1: score, 2: plays, 3: ?, 4: last played (seconds)}}."""
    found: dict[str, float] = {}
    for e in _entries(data):
        try:
            when = dict(_fields(e[2])).get(4) if isinstance(e.get(2), bytes) else None
        except (ValueError, IndexError):
            continue
        _latest(found, e.get(1), when)
    return found


def user_dirs() -> list[Path]:
    return [Path(p) for pattern in USER_DIRS for p in glob.glob(os.path.expandvars(pattern))]


class PlayedPlaylists:
    def __init__(self, dirs: list[Path] | None = None, store: Path = FILE):
        self._dirs = dirs
        self.store = store
        self._known: dict[str, float] | None = None

    def recent(self) -> list[str]:
        """Every playlist played so far, newest first."""
        known = self._load()
        seen: dict[str, float] = {}
        for d in (self._dirs if self._dirs is not None else user_dirs()):
            for name, read in (("list_player_play_history", read_play_history), ("frecency.pb", read_frecency)):
                try:
                    found = read((d / name).read_bytes())
                except OSError:
                    continue
                for uri, when in found.items():
                    seen[uri] = max(seen.get(uri, 0.0), when)
        grew = {u: t for u, t in seen.items() if t > known.get(u, 0.0)}
        if grew:
            known.update(grew)
            self._save(known)
        return sorted(known, key=lambda u: (-known[u], u))

    def _load(self) -> dict[str, float]:
        if self._known is None:
            try:
                raw = json.loads(self.store.read_text(encoding="utf-8"))
                self._known = {u: float(t) for u, t in raw.items() if PLAYLIST.match(u)}
            except (OSError, ValueError, AttributeError):
                self._known = {}
        return self._known

    def _save(self, known: dict[str, float]) -> None:
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store.with_suffix(".tmp")
            tmp.write_text(json.dumps(known, indent=0, sort_keys=True), encoding="utf-8")
            tmp.replace(self.store)
        except OSError as e:
            log.info("couldn't save the played playlists: %s", e)
