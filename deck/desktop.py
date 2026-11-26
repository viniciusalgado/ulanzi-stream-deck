"""The signed-in user's desktop: everything the deck does that needs someone's Windows session.

T3 Code, music (Windows media session, Spotify), Bluetooth headphones, and launching apps or links only exist
inside a user's session. The service that owns the deck runs with nobody signed in, so it reaches these through
the agent (deck.agent.RemoteDesktop, same interface); `deck run` uses this class directly.

    desktop = Desktop.from_config(cfg)   # T3 Code / music / headphones only when deck.toml has [t3] / [media] /
                                         # [headphones]; launching and hotkeys always
    desktop.attach(sources)      # provides the Snapshot fields in DATA (those configured), plus "desktop": presence
    desktop.open_thread(thread)  # ... every name in ACTIONS; each raises on failure (the key flashes red)

Presence ("desktop" in the Snapshot) says what the session can do right now:
    "here"        signed in and unlocked: everything works
    "locked"      signed in but locked: only actions marked requires("session") work (music, headphones)
    "away"        signed in, but the agent isn't connected (starting, or crashed): no desktop actions
    "signed_out"  nobody signed in: no desktop actions; pages show the signed-out page

Every desktop action is marked with `requires("input")` (needs the unlocked desktop: it opens windows or types)
or `requires("session")` (works while locked). Pages disable keys whose action the presence doesn't allow.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from . import winutil, winsession
from .sources.headphones import HeadphoneState, Headphones
from .sources.media import Media, MediaState
from .sources.spotify import SpotifyApi
from .sources.t3 import T3, T3State, environments_from_config

log = logging.getLogger(__name__)

PRESENCES = ("here", "locked", "away", "signed_out")
DATA = ("t3", "media", "playlists", "headphones")   # Snapshot fields the desktop provides
ALLOWED = {"here": {"input", "session"}, "locked": {"session"}, "away": set(), "signed_out": set()}


def requires(level: str):
    """Mark an action with what it needs from the session: "input" (unlocked desktop) or "session"."""
    assert level in ("input", "session")

    def mark(fn):
        fn.requires = level
        return fn
    return mark


def usable(fn: Callable, presence: str) -> bool:
    """Can this action run now? Actions without a mark don't need the desktop."""
    level = getattr(fn, "requires", None)
    return level is None or level in ALLOWED.get(presence, set())


def initial_values(envs) -> dict:
    """What DATA holds before the desktop has said anything (and again after the agent goes away)."""
    return {"t3": T3State(envs=tuple(envs)), "media": MediaState(), "playlists": (), "headphones": HeadphoneState()}


class Desktop:
    def __init__(self, t3: T3 | None, media: Media | None, headphones: Headphones | None, *,
                 launcher=winutil.launch, keys=winutil.hotkey,
                 presence: Callable[[], str] = winsession.presence_here):
        self.t3 = t3
        self.media = media
        self.headphones = headphones
        self._launch = launcher
        self._keys = keys
        self._presence = presence
        self._sources = None

    @classmethod
    def from_config(cls, cfg: dict) -> "Desktop":
        """The parts deck.toml turns on: [t3], [media], [headphones] (their plugins' sections)."""
        t3 = T3(environments_from_config(cfg["t3"].get("environments", []))) if "t3" in cfg else None
        media = Media(cfg["media"].get("playlists", []), SpotifyApi.load()) if "media" in cfg else None
        headphones = Headphones(cfg["headphones"].get("name", "WH-1000XM5")) if "headphones" in cfg else None
        return cls(t3, media, headphones)

    def attach(self, sources) -> None:
        initial = initial_values(self.t3.envs if self.t3 else ())
        if self.t3 is not None:
            sources.add("t3", self.t3.poll, every=2.0, initial=initial["t3"])
        if self.media is not None:
            sources.add("media", self.media.poll, every=1.0, initial=initial["media"])
            sources.add("playlists", self._playlists, every=600.0, initial=initial["playlists"])
        if self.headphones is not None:
            sources.add("headphones", self.headphones.poll, every=3.0, initial=initial["headphones"])
        sources.add("desktop", self._presence, every=2.0, initial="here")
        self._sources = sources

    # ----- actions (names listed in ACTIONS) -----

    @requires("input")
    def launch(self, target: str) -> None:
        self._launch(target)

    @requires("input")
    def hotkey(self, combo: str) -> None:
        self._keys(combo)

    @requires("input")
    def open_thread(self, thread) -> None:
        self.t3.open_thread(thread)

    @requires("input")
    def open_app(self) -> None:
        self.t3.open_app()

    @requires("input")
    def new_thread(self, project) -> None:
        self.t3.new_thread(project)

    @requires("input")
    def dictate(self, send: bool = False) -> None:
        """Toggle T3's dictation. send=True (stopping): also press Send, and return only once the server has the
        new message (raises if it never arrives, so the key flashes red)."""
        since = time.time() - 1.0          # a little slack for the server's clock
        if send:
            self.t3.dictate(send=True)
        else:
            self.t3.dictate()
        if send:
            self.t3.wait_for_sent(since)

    @requires("session")
    def settle_thread(self, thread) -> None:
        """Settle a done or failed thread on its server; it leaves the deck on the refresh right after."""
        try:
            self.t3.settle(thread)
        finally:
            self._refresh("t3")

    @requires("input")
    def play_playlist(self, uri: str) -> None:
        self.media.play_playlist(uri)

    @requires("session")
    def media_toggle(self) -> None:
        self.media.toggle()

    @requires("session")
    def media_next(self) -> None:
        self.media.next()

    @requires("session")
    def media_prev(self) -> None:
        self.media.prev()

    @requires("session")
    def reclaim_headphones(self) -> None:
        try:
            self.headphones.reclaim(self.media)
        finally:
            self._refresh("headphones")

    @requires("session")
    def reset_headphones(self) -> None:
        try:
            self.headphones.reset(self.media)
        finally:
            self._refresh("headphones")

    # ----- internals -----

    def _playlists(self):
        return tuple(self.media.playlist(uri) for uri in self.media.playlists)

    def _refresh(self, name: str) -> None:
        if self._sources is not None:
            self._sources.refresh(name)


ACTIONS = {name: fn.requires for name, fn in vars(Desktop).items() if hasattr(fn, "requires")}
