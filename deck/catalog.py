"""What `deck check` and `deck widgets` print: the layout's problems, and everything a layout can use.

    registries(cfg) -> (actions, widgets, plugin names)   # plugins set up, nothing started (no polling, no deck)
    check(cfg) -> (ok, text)
    describe(actions, widgets) -> text                     # markdown: widgets, actions, badges, screens
"""
from __future__ import annotations

from . import layout, plugins
from .actions import Actions
from .sources.registry import Sources
from .widgets import KINDS, Widgets


def registries(cfg: dict) -> tuple[Actions, Widgets, list[str]]:
    actions, widgets = Actions(), Widgets()
    names = plugins.load(cfg, plugins.Setup(cfg, Sources(), actions, widgets))
    return actions, widgets, names


def check(cfg: dict) -> tuple[bool, str]:
    try:
        actions, widgets, names = registries(cfg)
    except Exception as e:      # a plugin's own section is wrong (missing key, bad value)
        return False, f"a plugin couldn't start from deck.toml: {type(e).__name__}: {e}"
    try:
        lay = layout.parse(cfg, widgets, actions)
    except layout.LayoutError as e:
        return False, str(e)
    keys = sum(len(p.placements) for p in lay.pages)
    return True, (f"deck.toml OK: {len(lay.pages)} pages ({', '.join(p.name for p in lay.pages)}), {keys} keys, "
                  f"{len(lay.bottom)} on every page; plugins: {', '.join(names)}")


def describe(actions: Actions, widgets: Widgets) -> str:
    lines = ["# Widgets", "", "Place them in deck.toml under [[pages.keys]] (slot = N, or slots = [...] for "
             "'several') or [[bottom]].", ""]
    for w in widgets.all():
        lines.append(f"## {w.name}  ({'several slots' if w.slots == 'many' else 'one slot'})")
        if w.doc:
            lines.append(w.doc)
        for o in w.options:
            default = "required" if o.required else f"default {o.default!r}"
            lines.append(f"- `{o.name}` ({KINDS.get(o.kind, o.kind.__name__)}, {default}){': ' + o.doc if o.doc else ''}")
        lines.append("")
    lines += ["# Actions", "", "For a key's `tap` / `long`: \"name\", or [\"name\", args...].", ""]
    for name in actions.names():
        a = actions.get(name)
        needs = {"input": " (needs the unlocked desktop)", "session": " (needs a signed-in session)"}.get(a.requires, "")
        lines.append(f"- `{name}`{needs}: {a.doc}")
    lines += ["", "# Badges (a page's `badge`)", ""] + [f"- `{b}`" for b in sorted(widgets.badges)]
    lines += ["", "# Screens (opened by widgets, not placed)", ""] + [f"- `{s}`" for s in sorted(widgets.screens)]
    return "\n".join(lines) + "\n"
