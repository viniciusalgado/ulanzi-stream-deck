"""Plugins: one integration each (T3 Code, media, printer, ...), bundling its sources, actions, widgets and badges.

A plugin is a module with:

    NAME = "printer"
    SECTION = "printer"          # on when deck.toml has [printer]; None = always on
    def setup(s: Setup) -> None: # register everything with s.sources / s.actions / s.widgets

Built-in plugins are listed in BUILTIN. Others load from `[deck] plugins = ["my_package.my_plugin"]`.

What `setup` gets (Setup):
    s.cfg, s.section             the whole config, and this plugin's [section] ({} when SECTION is None)
    s.sources                    deck.sources.registry.Sources: add() polled sources, put() UI state
    s.actions                    deck.actions.Actions: add("printer.pause", fn, requires=None)
    s.widgets                    deck.widgets.Widgets: @s.widgets.widget(...), .screen(...), .badge(...)
    s.dirty()                    redraw soon (after changing something the snapshot doesn't see)
    s.showing(prefix)            is a widget whose name starts with `prefix` on the view shown now?
    s.pressed_at()               time.monotonic() of the last key press (for latency logs)
    s.lively(fn)                 fn(snapshot) -> bool: redraw every second while true (animations, timers)
    s.desktop                    deck.desktop.Desktop (or the service's RemoteDesktop): everything that needs the
                                 user's session goes through it, so it works the same in `deck run` and the service
    s.ddc                        monitor switching for the kvm plugin (None: its own Ddc)
Plugins never import the App: everything they need is in Setup, so each one can be tested alone.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field, replace
from typing import Callable

from ..actions import Actions
from ..sources.registry import Sources
from ..widgets import Widgets

log = logging.getLogger(__name__)
BUILTIN = ("core", "t3", "media", "headphones", "printer", "kvm")


@dataclass
class Setup:
    cfg: dict
    sources: Sources
    actions: Actions
    widgets: Widgets
    dirty: Callable[[], None] = lambda: None
    showing: Callable[[str], bool] = lambda prefix: False
    pressed_at: Callable[[], float] = lambda: 0.0
    section: dict = field(default_factory=dict)
    lively_checks: list = field(default_factory=list)
    desktop: object = None
    ddc: object = None

    def lively(self, fn: Callable[[object], bool]) -> None:
        self.lively_checks.append(fn)


def modules(cfg: dict) -> list:
    """The plugin modules deck.toml turns on, in setup order."""
    names = [f"{__name__}.{n}" for n in BUILTIN] + list(cfg.get("deck", {}).get("plugins", []))
    out = []
    for path in names:
        mod = importlib.import_module(path)
        if getattr(mod, "SECTION", None) is None or mod.SECTION in cfg:
            out.append(mod)
    return out


def load(cfg: dict, setup: Setup) -> list[str]:
    """Run every enabled plugin's setup against `setup`; returns their names."""
    names = []
    for mod in modules(cfg):
        section = cfg.get(mod.SECTION, {}) if getattr(mod, "SECTION", None) else {}
        mod.setup(replace(setup, section=section))
        names.append(mod.NAME)
    return names

