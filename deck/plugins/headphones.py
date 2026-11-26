"""Bluetooth headphones: connected to this PC?, battery, and "come back here". On when deck.toml has [headphones].

    [headphones]
    name = "WH-1000XM5"          # as Windows' Bluetooth settings show it

Source: headphones (HeadphoneState, polled by the desktop: deck.desktop).
Actions: headphones.reclaim (reconnect / re-take playback), headphones.reset (drop and reconnect)
Widget: headphones (tap: reclaim, long press: reset).
"""
from __future__ import annotations

from ..keys import Do, Key
from ..render import AMBER, GREEN, AppTile
from ..widgets import Option

NAME = "headphones"
SECTION = "headphones"


def setup(s) -> None:
    s.actions.add("headphones.reclaim", lambda: s.desktop.reclaim_headphones(), requires="session",
                  doc="bring the headphones' music link back to this PC")
    s.actions.add("headphones.reset", lambda: s.desktop.reset_headphones(), requires="session",
                  doc="drop the headphones' link and reconnect it")

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
