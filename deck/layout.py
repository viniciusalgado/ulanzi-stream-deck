"""The layout: which widget sits on which slot of which page, read from deck.toml (docs/adr/0003).

    layout = parse(cfg, widgets, actions)       # raises LayoutError(problems) listing everything wrong at once
    keys = build(layout, widgets, view, snap)    # {0..13: Key} for that view; pure
    layout.page("fun"); layout.start_page; layout.pages

deck.toml:

    [deck]
    start_page = "ai"

    [[pages]]
    name = "fun"                 # what View.page and Go(...) use
    label = "Fun"                # nav key text
    icon = "lucide:gamepad-2"
    color = "#f472b6"            # nav highlight, and the accent widgets on this page use
    badge = "media"              # optional: a badge a plugin registered (shown on the nav key)

      [[pages.keys]]
      slot = 0                   # or: slots = [2, 3, 4] for widgets that fill several keys
      widget = "launch"
      label = "Dota 2"           # every other entry is an option of the widget
      open = "steam://rungameid/570"
      tap = "media.next"         # optional: replace what a one-slot key does on a tap ...
      long = ["launch", "spotify:"]   # ... or on a long press: an action name, or [name, args...]

    [[bottom]]                   # placed on every page (slots a page also uses: the page wins)
    slots = [10, 11, 12]
    widget = "nav"

A view whose page is a screen a widget registered (e.g. "t3.new_thread") shows that screen on slots 0-9, with the
bottom row of the page it came from (View.back). Keys whose action the Windows session doesn't allow right now
(snapshot.desktop, see deck.actions.usable) are drawn dimmed and do nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .actions import Actions
from .keys import SLOTS, Do, Key, View
from .render import Blank
from .widgets import Ctx, Widgets

PLACEMENT_KEYS = {"slot", "slots", "widget", "tap", "long"}


class LayoutError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("deck.toml layout problems:\n  - " + "\n  - ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Placement:
    widget: str
    slots: tuple[int, ...]
    options: dict
    tap: Do | None = None
    long: Do | None = None


@dataclass(frozen=True)
class PageSpec:
    name: str
    label: str
    icon: str
    color: str
    badge: str | None
    placements: tuple[Placement, ...]


@dataclass(frozen=True)
class Layout:
    pages: tuple[PageSpec, ...]
    bottom: tuple[Placement, ...]
    start_page: str

    def page(self, name: str | None) -> PageSpec | None:
        return next((p for p in self.pages if p.name == name), None)


# ----- reading deck.toml -----

def parse(cfg: dict, widgets: Widgets, actions: Actions) -> Layout:
    problems: list[str] = []
    raw_pages = cfg.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise LayoutError(["no [[pages]]: copy the layout from deck.example.toml (the old [pages.fun] tables "
                           "are from before layouts moved into deck.toml)"])
    bottom = tuple(_placements(cfg.get("bottom", []), "[[bottom]]", widgets, actions, problems))
    pages, seen = [], set()
    for i, raw in enumerate(raw_pages):
        where = f"[[pages]] #{i + 1}"
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            problems.append(f"{where}: needs a name")
            continue
        where = f"page {name!r}"
        if name in seen:
            problems.append(f"{where}: two pages with this name")
        if name in widgets.screens:
            problems.append(f"{where}: this name belongs to a screen; pick another")
        seen.add(name)
        extra = set(raw) - {"name", "label", "icon", "color", "badge", "keys"}
        if extra:
            problems.append(f"{where}: unknown settings {sorted(extra)}")
        badge = raw.get("badge")
        if badge is not None and badge not in widgets.badges:
            problems.append(f"{where}: unknown badge {badge!r} (known: {', '.join(sorted(widgets.badges)) or 'none'})")
        placements = _placements(raw.get("keys", []), where, widgets, actions, problems)
        used: dict[int, str] = {}
        for p in placements:
            for s in p.slots:
                if s in used:
                    problems.append(f"{where}: slot {s} used by both {used[s]!r} and {p.widget!r}")
                used[s] = p.widget
        pages.append(PageSpec(name, raw.get("label", name.title()), raw.get("icon", "lucide:square"),
                              raw.get("color", "#a78bfa"), badge, tuple(placements)))
    start = cfg.get("deck", {}).get("start_page") or (pages[0].name if pages else "")
    if pages and start not in seen:
        problems.append(f"[deck] start_page {start!r} is not a page (pages: {', '.join(sorted(seen))})")
    if problems:
        raise LayoutError(problems)
    return Layout(tuple(pages), bottom, start)


def _placements(raw_list, where: str, widgets: Widgets, actions: Actions, problems: list[str]) -> list[Placement]:
    out = []
    if not isinstance(raw_list, list):
        problems.append(f"{where}: keys must be a list of [[...keys]] tables")
        return out
    for j, raw in enumerate(raw_list):
        name = raw.get("widget")
        here = f"{where}, key #{j + 1} ({name or 'no widget'})"
        w = widgets.get(name) if isinstance(name, str) else None
        if w is None:
            known = ", ".join(x.name for x in widgets.all())
            problems.append(f"{here}: unknown widget {name!r}. Known: {known}")
            continue
        slots = _slots(raw, here, problems)
        if slots is None:
            continue
        if w.slots == "one" and len(slots) != 1:
            problems.append(f"{here}: takes exactly one slot (use slot = N)")
            continue
        options, bad = widgets.options(w, {k: v for k, v in raw.items() if k not in PLACEMENT_KEYS})
        problems += [f"{here}: {b}" for b in bad]
        tap = _action(raw.get("tap"), f"{here}: tap", actions, problems)
        long = _action(raw.get("long"), f"{here}: long", actions, problems)
        if (tap or long) and len(slots) != 1:
            problems.append(f"{here}: tap / long only work on one-slot keys")
        out.append(Placement(name, slots, options, tap, long))
    return out


def _slots(raw: dict, here: str, problems: list[str]) -> tuple[int, ...] | None:
    if ("slot" in raw) == ("slots" in raw):
        problems.append(f"{here}: give either slot = N or slots = [...]")
        return None
    slots = (raw["slot"],) if "slot" in raw else tuple(raw["slots"]) if isinstance(raw["slots"], list) else None
    if not slots or any(not isinstance(s, int) or isinstance(s, bool) or s not in SLOTS for s in slots):
        problems.append(f"{here}: slots are 0-13 (13 is the wide key)")
        return None
    if len(set(slots)) != len(slots):
        problems.append(f"{here}: a slot is listed twice")
        return None
    return slots


def _action(raw, here: str, actions: Actions, problems: list[str]) -> Do | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        name, args = raw, ()
    elif isinstance(raw, list) and raw and isinstance(raw[0], str):
        name, args = raw[0], tuple(raw[1:])
    else:
        problems.append(f"{here}: an action name, or [name, args...]")
        return None
    if name not in actions:
        problems.append(f"{here}: unknown action {name!r}")
        return None
    return Do(name, args)


# ----- building a view -----

def build(layout: Layout, widgets: Widgets, view: View, snap, actions: Actions | None = None) -> dict[int, Key]:
    keys: dict[int, Key] = {i: Key(Blank()) for i in SLOTS}
    screen = widgets.screens.get(view.page)
    if screen is not None:
        page = layout.page(view.back) or layout.page(layout.start_page)
        keys.update(_only(screen(_ctx(layout, widgets, view, snap, tuple(range(10)), {}, page)), range(10)))
        own: set[int] = set(range(10))
    else:
        page = layout.page(view.page) or layout.page(layout.start_page)
        own = set()
        for p in page.placements:
            keys.update(_place(p, layout, widgets, view, snap, page))
            own.update(p.slots)
    for p in layout.bottom:
        free = tuple(s for s in p.slots if s not in own)
        if free:
            keys.update(_only(_place(p, layout, widgets, view, snap, page), free))
    if actions is not None and snap.desktop is not None:
        keys = {i: _gate(k, actions, snap.desktop) for i, k in keys.items()}
    return keys


def _place(p: Placement, layout: Layout, widgets: Widgets, view: View, snap, page: PageSpec) -> dict[int, Key]:
    w = widgets.get(p.widget)
    out = w.build(_ctx(layout, widgets, view, snap, p.slots, p.options, page))
    if isinstance(out, Key):
        out = {p.slots[0]: out}
    out = _only(out, p.slots)
    if p.tap or p.long:
        s = p.slots[0]
        k = out.get(s, Key(Blank()))
        out[s] = replace(k, action=p.tap or k.action, long=p.long or k.long)
    return out


def _ctx(layout: Layout, widgets: Widgets, view: View, snap, slots, options, page) -> Ctx:
    return Ctx(view=view, snap=snap, slots=tuple(slots), options=options, page=page, pages=layout.pages,
               badges=widgets.badges)


def _only(keys: dict[int, Key], slots) -> dict[int, Key]:
    allowed = set(slots)
    return {i: k for i, k in keys.items() if i in allowed}


def _gate(key: Key, actions: Actions, presence: str) -> Key:
    """Dim a key whose actions the session doesn't allow now, and take its actions away."""
    def ok(a):
        return not isinstance(a, Do) or a.action not in actions or actions.usable(a.action, presence)
    if ok(key.action) and ok(key.long):
        return key
    tile = replace(key.tile, dim=True) if hasattr(key.tile, "dim") else key.tile
    return Key(tile, key.action if ok(key.action) else None, key.hold, key.long if ok(key.long) else None)
