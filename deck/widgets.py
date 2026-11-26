"""Widgets: the kinds of key the layout can place, each one a function of (options, snapshot) -> keys.

Plugins register them (deck.plugins); deck.toml places them on slots (deck.layout).

    widgets = Widgets()

    @widgets.widget("media.prev", needs=("media",), doc="previous track")
    def prev(ctx: Ctx) -> Key: ...

    @widgets.widget("launch", options=(Option("open", str, doc="URI, program or Start-menu app id"),
                                       Option("label", str, ""), Option("icon", str, "lucide:square")))
    def launch(ctx): return Key(AppTile(ctx.options["icon"], ctx.options["label"]), Do("launch", (ctx.options["open"],)))

    @widgets.widget("t3.threads", slots="many", ...)   # placed on several slots: returns {slot: Key}
    widgets.screen("t3.new_thread", build)            # a full page a widget opens (View(page="t3.new_thread"))
    widgets.badge("t3", fn)                           # nav badge: snapshot -> (text, colour) | None

A widget's `ctx` has: view, snap (deck.keys.Snapshot), slots (the slots it was placed on, in order), options
(checked against its Option list, defaults filled in), page (the PageSpec being shown; for a screen, the page
Back returns to), pages (all pages, for navigation) and badges. A one-slot widget may return a single Key.
Widgets are pure: they never run anything, they only bind action names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

REQUIRED = object()
KINDS = {str: "text", int: "integer", float: "number", bool: "true/false", list: "list"}


@dataclass(frozen=True)
class Option:
    name: str
    kind: type = str
    default: object = REQUIRED
    doc: str = ""

    @property
    def required(self) -> bool:
        return self.default is REQUIRED


@dataclass(frozen=True)
class Widget:
    name: str
    build: Callable
    slots: str = "one"            # one | many
    needs: tuple[str, ...] = ()   # snapshot fields it reads (documentation, and `deck widgets`)
    options: tuple[Option, ...] = ()
    doc: str = ""


@dataclass(frozen=True)
class Ctx:
    view: object
    snap: object
    slots: tuple[int, ...]
    options: dict = field(default_factory=dict)
    page: object = None
    pages: tuple = ()
    badges: dict = field(default_factory=dict)

    @property
    def color(self) -> str:
        return getattr(self.page, "color", "#a78bfa")


class Widgets:
    def __init__(self):
        self._widgets: dict[str, Widget] = {}
        self.screens: dict[str, Callable] = {}
        self.badges: dict[str, Callable] = {}

    def widget(self, name: str, *, slots: str = "one", needs: tuple = (), options: tuple = (), doc: str = ""):
        def register(fn):
            self.add(Widget(name, fn, slots, tuple(needs), tuple(options), doc or (fn.__doc__ or "").strip()))
            return fn
        return register

    def add(self, w: Widget) -> None:
        if w.name in self._widgets:
            raise ValueError(f"widget {w.name!r} registered twice")
        if w.slots not in ("one", "many"):
            raise ValueError(f"widget {w.name!r}: slots must be 'one' or 'many'")
        self._widgets[w.name] = w

    def screen(self, name: str, build: Callable) -> None:
        if name in self.screens:
            raise ValueError(f"screen {name!r} registered twice")
        self.screens[name] = build

    def badge(self, name: str, fn: Callable) -> None:
        if name in self.badges:
            raise ValueError(f"badge {name!r} registered twice")
        self.badges[name] = fn

    def get(self, name: str) -> Widget | None:
        return self._widgets.get(name)

    def all(self) -> list[Widget]:
        return [self._widgets[n] for n in sorted(self._widgets)]

    def options(self, w: Widget, given: dict) -> tuple[dict, list[str]]:
        """(options with defaults filled in, problems) for one placement of `w`."""
        problems, out = [], {}
        known = {o.name: o for o in w.options}
        for name in given:
            if name not in known:
                problems.append(f"unknown option {name!r} (it takes: {', '.join(known) or 'none'})")
        for o in w.options:
            if o.name in given:
                value = given[o.name]
                if o.kind is float and isinstance(value, int) and not isinstance(value, bool):
                    value = float(value)
                if not isinstance(value, o.kind) or (o.kind is int and isinstance(value, bool)):
                    problems.append(f"option {o.name!r} should be {KINDS.get(o.kind, o.kind.__name__)}")
                    continue
                out[o.name] = value
            elif o.required:
                problems.append(f"missing option {o.name!r}" + (f" ({o.doc})" if o.doc else ""))
            else:
                out[o.name] = list(o.default) if isinstance(o.default, list) else o.default
        return out, problems
