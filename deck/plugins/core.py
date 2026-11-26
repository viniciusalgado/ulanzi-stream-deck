"""Always on: launching things, sending hotkeys, plain buttons, and the page buttons (nav).

Actions:
    launch(target)    open a URI, file, program path or Start-menu app id (shell:AppsFolder\\...)
    hotkey(combo)     press a key combination in the window in front, e.g. "ctrl+shift+m"
Widgets:
    launch            icon + label; tap opens `open`
    button            icon + label that does whatever its `tap` / `long` say (any action)
    nav               one key per page (on as many slots as you give it), with the page's badge
    blank             an empty key
"""
from __future__ import annotations

from .. import winutil
from ..desktop import requires
from ..keys import Do, Go, Key, View
from ..render import AMBER, FG, MUTED, AppTile, Blank
from ..widgets import Option

NAME = "core"
SECTION = None

TILE_OPTIONS = (Option("label", str, "", "text under the icon"),
                Option("icon", str, "lucide:square", "lucide:<name>, brand:<name> or app:<name>"),
                Option("color", str, FG, "icon colour, #rrggbb"),
                Option("sub", str, "", "small second line"))


@requires("input")
def launch(target: str) -> None:
    winutil.launch(target)


@requires("input")
def hotkey(combo: str) -> None:
    winutil.hotkey(combo)


def setup(s) -> None:
    s.actions.add("launch", launch, doc="open a URI, file, program or Start-menu app id")
    s.actions.add("hotkey", hotkey, doc='press a key combination in the window in front ("ctrl+shift+m")')
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
        current = ctx.page.name if ctx.page is not None else None
        for slot, page in zip(ctx.slots, ctx.pages):
            on = page.name == current
            badge = ctx.badges[page.badge](ctx.snap) if page.badge in ctx.badges else None
            text, color = badge or (None, AMBER)
            keys[slot] = Key(AppTile(page.icon, page.label, color=page.color if on else MUTED,
                                     bg="#1f2229" if on else "#15171c", accent=page.color if on else None,
                                     badge=text, badge_color=color),
                             Go(View(page.name)))
        return keys
