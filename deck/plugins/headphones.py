"""Bluetooth headphones: connected to this PC?, battery, and "come back here". On when deck.toml has [headphones].

    [headphones]
    name = "WH-1000XM5"          # as Windows' Bluetooth settings show it

Source: headphones (HeadphoneState).
Actions: headphones.reclaim (reconnect / re-take playback), headphones.reset (drop and reconnect)
Widget: headphones (tap: reclaim, long press: reset).
"""
from __future__ import annotations

from ..keys import Do, Key
from ..render import AMBER, GREEN, AppTile
from ..sources.headphones import HeadphoneState, Headphones
from ..widgets import Option

NAME = "headphones"
SECTION = "headphones"


class _Playback:
    """What Headphones needs to re-take playback, through the media plugin's source and action (if it's on)."""

    def __init__(self, s):
        self._s = s

    def poll(self):
        try:
            return self._s.sources.get("media")
        except KeyError:
            return None

    def toggle(self) -> None:
        self._s.actions.run("media.toggle")


def setup(s, headphones: Headphones | None = None) -> None:
    hp = headphones or Headphones(s.section.get("name", "WH-1000XM5"))
    s.sources.add("headphones", hp.poll, every=3.0, initial=HeadphoneState())

    def playback():
        return _Playback(s) if "media.toggle" in s.actions else None

    def reclaim() -> None:
        try:
            hp.reclaim(playback())
        finally:
            s.sources.refresh("headphones")

    def reset() -> None:
        try:
            hp.reset(playback())
        finally:
            s.sources.refresh("headphones")

    s.actions.add("headphones.reclaim", reclaim, requires="session",
                  doc="bring the headphones' music link back to this PC")
    s.actions.add("headphones.reset", reset, requires="session", doc="drop the headphones' link and reconnect it")

    @s.widgets.widget("headphones", needs=("headphones",), options=(Option("label", str, "Headphones"),),
                      doc="connected / battery; tap: back to this PC, long press: hard reconnect")
    def key(ctx):
        h, label = ctx.snap.headphones, ctx.options["label"]
        if h is None or not h.found:
            return Key(AppTile("lucide:headphones", label, sub="not paired", dim=True))
        battery = f" · {h.battery}%" if h.battery is not None else ""
        if h.connected:
            tile = AppTile("lucide:headphones", label, sub=f"on this PC{battery}", color=GREEN)
        else:
            tile = AppTile("lucide:headphones", label, sub="tap to connect", color=AMBER, badge="!", badge_color=AMBER)
        return Key(tile, Do("headphones.reclaim"), long=Do("headphones.reset"))
