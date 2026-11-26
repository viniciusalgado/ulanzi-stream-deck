"""Always on: launching things, sending hotkeys, plain buttons, and the page buttons (nav).

Actions:
    launch(target)    open a URI, file, program path or Start-menu app id (shell:AppsFolder\\...)
    hotkey(combo)     press a key combination in the window in front, e.g. "ctrl+shift+m"
Widgets:
    launch            icon + label; tap opens `open`
    button            icon + label that does whatever its `tap` / `long` say (any action)
    nav               one key per page (on as many slots as you give it), with the page's badge
    blank             an empty key
    session           who is at the PC (signed out / locked / agent missing): for the signed-out page
    clock             the time and date
"""
from __future__ import annotations

import time

from ..keys import Do, Go, Key, View
from ..render import AMBER, FG, MUTED, AppTile, Blank, InfoTile
from ..widgets import Option

NAME = "core"
SECTION = None

TILE_OPTIONS = (Option("label", str, "", "text under the icon"),
                Option("icon", str, "lucide:square", "lucide:<name>, brand:<name> or app:<name>"),
                Option("color", str, FG, "icon colour, #rrggbb"),
                Option("sub", str, "", "small second line"))


def setup(s) -> None:
    s.actions.add("launch", lambda target: s.desktop.launch(target), requires="input",
                  doc="open a URI, file, program or Start-menu app id")
    s.actions.add("hotkey", lambda combo: s.desktop.hotkey(combo), requires="input",
                  doc='press a key combination in the window in front ("ctrl+shift+m")')
    w = s.widgets

    def tile(o) -> AppTile:
        return AppTile(o["icon"], o["label"], sub=o["sub"], color=o["color"])

    @w.widget("launch", options=(Option("open", str, doc="what to open: URI, program path or app id"),
                                 *TILE_OPTIONS), doc="opens something on a tap")
    def launch_key(ctx):
        return Key(tile(ctx.options), Do("launch", (ctx.options["open"],)))

    @w.widget("button", options=TILE_OPTIONS, doc="a key that does what its tap / long say")
    def button(ctx):
        return Key(tile(ctx.options))

    @w.widget("blank", doc="an empty key")
    def blank(ctx):
        return Key(Blank())

    @w.widget("nav", slots="many", doc="one key per page, in layout order; the page shown is lit")
    def nav(ctx):
        keys = {}
        current = (ctx.page.nav_as or ctx.page.name) if ctx.page is not None else None
        signed_out = ctx.snap.desktop == "signed_out"
        for slot, page in zip(ctx.slots, [p for p in ctx.pages if p.nav]):
            on = page.name == current
            if signed_out and not on:        # only the signed-out page works without a session
                keys[slot] = Key(AppTile(page.icon, page.label, sub="sign in", color=MUTED, bg="#15171c", dim=True))
                continue
            badge = ctx.badges[page.badge](ctx.snap) if page.badge in ctx.badges else None
            text, color = badge or (None, AMBER)
            keys[slot] = Key(AppTile(page.icon, page.label, color=page.color if on else MUTED,
                                     bg="#1f2229" if on else "#15171c", accent=page.color if on else None,
                                     badge=text, badge_color=color),
                             Go(View(page.name)))
        return keys

    @w.widget("session", options=(Option("label", str, "This PC"),),
              doc="who is at the PC: signed out, locked, or the deck agent not running")
    def session(ctx):
        value, lines = {"signed_out": ("Signed out", ("sign in for T3,", "music & apps")),
                        "locked": ("Locked", ("music and the", "printer still work")),
                        "away": ("No agent", ("the deck agent", "isn't running"))}.get(
            ctx.snap.desktop, ("Signed in", ("everything works",)))
        return Key(InfoTile(ctx.options["label"], value, lines, accent=MUTED, icon="lucide:monitor"))

    @w.widget("clock", doc="the time and date")
    def clock(ctx):
        t = time.localtime(ctx.snap.now)
        return Key(InfoTile("Clock", time.strftime("%H:%M", t), (time.strftime("%a %d %b", t),), accent=MUTED))
