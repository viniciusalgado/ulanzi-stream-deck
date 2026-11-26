# Plan: modular keys (named actions, widgets, layout in config, plugins)

Status: built on branch `modular-keys`, 2026-11-26 (steps 0-5). Terms: see [GLOSSARY.md](../../GLOSSARY.md). Decisions:
[ADR 2](../adr/0002-actions-are-named.md), [ADR 3](../adr/0003-layout-in-config-behaviour-in-widgets.md).

Goal: someone else (or their agent) can clone the repo, copy `deck.example.toml` to `deck.toml`, arrange their own
keys, turn integrations on or off, and check the result with `deck check` and `deck preview`, all without editing
Python.

## Review (architecture vocabulary: module, interface, depth, seam, adapter, leverage, locality)

What made keys hard to change, and what each part of this plan does about it.

| # | Friction today | Deepening | Strength |
|---|---|---|---|
| 1 | `pages.py` is four functions that mix *where* keys go with *how* each key behaves. Moving a key means editing a function, and the same behaviour (New thread, thread keys, paging) is written once per page. | **Widgets**: one module per kind of key, with interface `build(ctx) -> keys`. Pages become data. The paging, offline and empty-state behaviour lives in one widget each (locality). | Strong |
| 2 | Actions are callables reached through `svc` (the whole App, duck-typed). There is no list of what keys can do, and the service-mode plan needs actions that can cross a pipe. | **Named actions**: one registry, `run(name, args)`. Two adapters: local functions now, and the agent pipe later (`RemoteDesktop`), so the seam is real. It reuses `deck.desktop.requires`. | Strong |
| 3 | Settings are read with `cfg.get(...)` defaults spread across `app.py`, `pages.py` and `__main__.py`, so the config's real interface is nowhere. A typo is a silent default or a crash at press time. | **Layout module**: `parse(cfg) -> Layout` collects every problem at once (unknown widget or action, missing option, slot used twice). `deck check` is that function. | Strong |
| 4 | `App.__init__` builds every integration, and App methods hold plugin UI state (kvm busy/message, dictation and draft timers, camera still). Someone without a printer still runs a printer poller. | **Plugins**: each integration registers its sources, actions, widgets and badges in `setup()`. Its UI state is a source value it `put`s, so the snapshot stays the only thing widgets read. | Worth doing: N real adapters (one per plugin), and the App shrinks to wiring |
| 5 | `Snapshot` has a fixed field per integration. | Snapshot is generic: one attribute per source name, plus `now`. | Follows from 4 |

Rejected:
- **Configurable tiles or colours per state**: that would turn `render` into a shallow pass-through of config. Widgets
  take a few options (label, icon, colour) and decide states themselves.
- **Screens in config**: the new-thread picker is behaviour, not layout (ADR 3).
- **A Protocol class for `svc`**: the action registry replaces `svc` altogether.

## Interfaces

```python
# deck/actions.py
actions = Actions()
actions.add("media.toggle", media.toggle, requires="session", doc="play / pause")
actions.run("media.toggle")                # KeyError-like UnknownAction for unknown names
actions.usable("media.toggle", presence)   # deck.desktop presence rules
actions.names(); actions.get(name)         # for deck check / deck widgets

# deck/pages.py (key values)
Do("launch", ("steam://rungameid/570",))   # name + args; `then=` still navigates at press time
View(page, offset=0, env=None, back=None)  # back: where a screen's Back key (and its nav highlight) goes

# deck/widgets.py
@widget("printer.pause_resume", needs=("printer",), doc="...")
def pause_resume(ctx: Ctx) -> Key | dict[int, Key]: ...
#   ctx.view, ctx.snap, ctx.slots (tuple), ctx.options (checked, defaults filled), ctx.page, ctx.color
widgets.screen("t3.new_thread", build)     # a full page a widget opens
widgets.badge("t3", fn)                    # snapshot -> (text, colour) | None

# deck/layout.py
layout = parse(cfg, widgets, actions)      # raises LayoutError(problems: list[str])
keys = build(layout, widgets, view, snap)  # {0..13: Key}, pure

# deck/plugins/<name>.py
NAME = "printer"; SECTION = "printer"      # on when [printer] is in deck.toml (core is always on)
def setup(s: Setup) -> None: ...           # s.cfg, s.section, s.sources, s.actions, s.widgets, s.dirty()
```

## deck.toml shape

```toml
[deck]
start_page = "ai"
plugins = []                 # extra plugin modules (import paths) beyond the built-in ones

[[pages]]
name = "fun"
label = "Fun"
icon = "lucide:gamepad-2"
color = "#f472b6"
badge = "media"

  [[pages.keys]]
  slot = 0
  widget = "launch"
  label = "Dota 2"
  icon = "brand:dota2"
  open = "steam://rungameid/570"

  [[pages.keys]]
  slots = [2, 3, 4]
  widget = "media.playlists"

[[bottom]]                   # on every page
slots = [10, 11, 12]
widget = "nav"

[[bottom]]
slot = 13
widget = "kvm.switch"

[media]                      # plugin settings stay in their own sections
playlists = ["spotify:playlist:..."]
```

## Steps (each keeps `pytest` green and the deck looking the same)

0. **Golden test first.** Record every key (tile, action, hold) that today's `pages.build` produces for a set of
   snapshots and views. After the refactor the default layout must produce the same tiles, with actions mapped
   to their new names. This is what makes "looks the same" checkable.
1. **Named actions.** Add `deck/actions.py`; `Do` carries a name; App runs by name. Tests compare by value.
2. **Widgets.** Split the page functions into widgets. Add generic `Snapshot`, `Sources.put`, and `View.back`.
3. **Layout in config.** Add `layout.parse` / `layout.build`. `deck.example.toml` becomes the default layout, and
   the page functions are deleted.
4. **Plugins.** Move each integration's sources, actions, widgets, badges and UI state out of `App` into
   `deck/plugins/`. Each is enabled by its config section; third-party plugins load via `[deck] plugins`.
5. **Tools for people and agents.** `python -m deck check`, `python -m deck widgets`, and `AGENTS.md` (the edit
   loop: edit `deck.toml` -> `deck check` -> `deck preview` -> look at the PNGs).

## Coordination with service mode (docs/plans/service-mode.md)

- Its `desktop.ACTIONS` and `requires` marks carry over: desktop-side actions keep their marks in the registry,
  so presence gating is a registry query.
- `RemoteDesktop` can forward by action name instead of by method. Desktop-side plugins (t3, media,
  headphones, `launch`) are the ones the agent runs. Host-side plugins (printer, kvm) stay in the service.
- Both plans edit `app.py` and `pages.py`. This branch lands first, and service mode rebases onto it.

## Behaviour changes (deliberate)

- The 3D Print page's thread keys show "Offline" when T3 Code is unreachable, the same as the AI page. Before,
  they showed "none active".
- The new-thread picker's Back key and nav highlight return to the page you opened it from, rather than to a
  page hard-coded as `ai`.
