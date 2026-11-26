# Working on this deck (for agents)

A Windows daemon that drives an Ulanzi D200 stream deck over USB. Read [GLOSSARY.md](GLOSSARY.md) first: slot,
key, tile, action, widget, layout, plugin and the rest mean exactly what it says. Decisions that are settled are in
[docs/adr/](docs/adr/). Don't re-open them without a new reason.

## Changing what the keys do: edit deck.toml, not code

`deck.toml` is the user's file. Git ignores it, and `deck.example.toml` is the shared default. Pages and keys
are data there:

```toml
[[pages]]
name = "fun"
label = "Fun"
icon = "lucide:gamepad-2"
color = "#f472b6"

  [[pages.keys]]
  slot = 0                  # 0-12 keys, 13 the wide key; `slots = [..]` for widgets that fill several
  widget = "launch"
  label = "Dota 2"
  icon = "brand:dota2"
  open = "steam://rungameid/570"
  long = ["hotkey", "ctrl+shift+m"]   # optional: replace a key's tap / long-press action
```

The loop, every time:

1. `.venv\Scripts\python -m deck widgets` lists every widget (with its options), action and badge you can use.
   Use only these names.
2. Edit `deck.toml`.
3. `.venv\Scripts\python -m deck check` lists every problem at once. Fix them all until it prints `OK`.
4. `.venv\Scripts\python -m deck preview <dir>` renders each page to PNG with live data. Look at the images.
5. `.venv\Scripts\python -m deck restart` makes the running daemon pick the changes up. Ask the user first: it
   redraws their deck.

A deck.toml from before layouts lived in it (it has `[pages.fun]` tables) is converted by
`python scripts/migrate_config.py`.

## Changing behaviour: write a widget or a plugin

- A **widget** is a pure function `build(ctx) -> Key | {slot: Key}`. It reads only `ctx.snap`, `ctx.options`,
  `ctx.slots` and `ctx.page`. It never runs anything: keys carry action *names* (`Do("media.toggle")`).
- A **plugin** is one module in `deck/plugins/` (or any importable module listed in `[deck] plugins`), with
  `NAME`, `SECTION` and `setup(s)`. It registers its sources, actions, widgets and badges there. See
  `deck/plugins/__init__.py` for what `s` offers, and `deck/plugins/printer.py` for a complete one.
- Anything that needs the user's Windows session goes through `s.desktop` (deck.desktop): the deck
  may run as a service with nobody signed in, and the desktop is then a proxy to the user's session.
- Actions that need the user's desktop are marked `requires="input"` (unlocked) or `"session"` (works while
  locked). The layout dims keys whose action the session can't run.

## Tests

`.venv\Scripts\python -m pytest -q` must stay green. Useful ones:

- `tests/test_golden.py` checks that the default layout draws exactly what `tests/golden/pages.json` recorded.
  If you change how a widget looks on purpose, say so, and regenerate the record deliberately. Never edit it
  by hand to make a failure go away.
- `tests/test_plugins.py` shows how to test a plugin with fakes and no hardware.

## Don't

- Don't send partial screen updates to the deck. The firmware hangs (ADR 1).
- Don't put personal values (paths with a user name, IPs, playlists) in tracked files. They go in `deck.toml`.
