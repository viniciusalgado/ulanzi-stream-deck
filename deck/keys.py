"""What a key is, as plain values: the tile it shows and the actions it runs (by name), plus where the deck is.

    Key(AppTile("lucide:pause", "Pause"), Do("media.toggle"))          # tap runs "media.toggle"
    Key(tile, Do("printer.cancel"), hold=2.0)                          # runs only after a 2 s hold
    Key(tile, Do("headphones.reclaim"), long=Do("headphones.reset"))    # tap / long press
    Key(tile, Go(View("print")))                                       # navigate
    Snapshot(now=..., t3=T3State(...), media=...)                      # every source's latest value, by name

Slots (the D200, 5 x 3; the wide key spans the last two cells of the bottom row):

     0   1   2   3   4
     5   6   7   8   9
    10  11  12  [ 13 ]

Keys never hold functions: actions are names looked up in deck.actions when pressed (docs/adr/0002).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .render import MUTED, AppTile, InfoTile

SLOTS = tuple(range(14))
WIDE = 13


@dataclass(frozen=True)
class View:
    page: str = "ai"          # a page from the layout, or a screen a widget owns (e.g. "t3.new_thread")
    offset: int = 0           # paging through threads / projects
    env: str | None = None    # environment chosen in the new-thread picker
    back: str | None = None   # for screens: the page Back returns to (and whose nav key stays lit)


@dataclass(frozen=True)
class Go:
    view: View


@dataclass(frozen=True)
class Do:
    action: str               # a name in deck.actions, e.g. "media.toggle"
    args: tuple = ()
    then: View | None = None  # navigate here as soon as the key is pressed
    quiet: bool = False       # no "ok" flash when it worked (the key's content changes instead)


@dataclass(frozen=True)
class Key:
    tile: object
    action: Go | Do | None = None
    hold: float = 0.0          # seconds the key must be held before the action runs
    long: Do | None = None     # optional second action for a long press (then `action` runs on a short tap)
    long_s: float = 0.0        # how long that long press is (0: deck.presses' default)
    long_label: str = ""       # set: holding shows a filling ring with this label until `long` fires


class Snapshot:
    """Every source's latest value, by source name, plus `now` (epoch seconds). A source that isn't running reads
    as None, so a widget works with whatever plugins are on."""

    def __init__(self, now: float = 0.0, **values):
        self.__dict__.update(values)
        self.now = now

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return None

    def __eq__(self, other) -> bool:
        return isinstance(other, Snapshot) and vars(self) == vars(other)

    def __repr__(self) -> str:
        return f"Snapshot({', '.join(f'{k}={v!r}' for k, v in vars(self).items())})"


# ----- helpers widgets share -----

def age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def duration(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def paged(items: list, slots: tuple[int, ...] | list[int], view: View, make: Callable,
          more_view: Callable[[int], View]) -> dict[int, Key]:
    """Fill slots with items; when they don't fit, the last slot pages through the rest."""
    keys = {}
    slots = list(slots)
    if len(items) <= len(slots):
        shown = items
    else:
        per = len(slots) - 1
        start = view.offset if view.offset < len(items) else 0
        shown = items[start: start + per]
        left = len(items) - start - len(shown)
        nxt = start + per if start + per < len(items) else 0
        keys[slots[-1]] = Key(AppTile("lucide:chevrons-right", f"+{left}" if left else "Back to first",
                                      sub="more", color=MUTED), Go(more_view(nxt)))
    for slot, item in zip(slots, shown):
        keys[slot] = make(item)
    return keys


def offline(name: str) -> Key:
    return Key(InfoTile(name, "Offline", ("not reachable",), accent=MUTED, icon="lucide:unplug"))
