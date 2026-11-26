# 3. Layout lives in config, behaviour lives in widgets

Status: accepted, 2026-11-26

## Context

Pages were Python functions that placed keys by number and also decided what each key shows in each state. To
share the project, other people (and their agents) need to arrange keys without editing code. Personal values
(paths, IPs, playlists) must also stay out of git.

## Decision

- `deck.toml` (git-ignored) holds the layout: pages, and placements of widgets on slots, with options.
  `deck.example.toml` (tracked) is the default layout and the starting point.
- Widgets hold behaviour: how a key looks in each state and which action it binds. The layout never describes
  states, only which widget goes where and with what options.
- A screen that a widget opens (the new-thread picker) is code, not layout.
- `python -m deck check` validates the layout against the registered widgets and actions, and `deck preview`
  renders it. Together they are the edit loop for people and agents.

## Consequences

- Rearranging keys, or adding a launcher / hotkey / URL key, needs no code.
- A key that behaves in a new way is a new widget: a function in a plugin, testable on its own.
- Rendering stays out of config (no per-tile colours beyond widget options), so `deck.render` stays deep.
