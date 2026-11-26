# Glossary

The words this codebase uses for its domain. Code, docs and plans use these and nothing else.

**Deck**: the Ulanzi D200 itself: 13 square keys (0-12) and one wide key (13), each a small screen and a button.
`deck.device.Deck` is the module that talks to it over USB HID.

**Slot**: a position on the deck, 0-13. Slot 13 is the wide key. Layout code and `deck.toml` talk about slots;
the user talks about keys.

**Key**: what one slot shows and does right now: a *tile* plus the *actions* for a tap, a long press or a hold
(`deck.pages.Key`). Keys are values, rebuilt from the snapshot on every redraw.

**Tile**: the picture on a key, as data (`AppTile`, `ArtTile`, `InfoTile`, `ThreadTile`, `SwitchTile`); `deck.render`
turns a tile into a PNG. Tiles never run anything.

**Action**: something a key does when pressed, called by a *name* such as `media.toggle` or `launch`, with
arguments. The *action registry* maps names to functions. Keys and `deck.toml` only ever hold names.

**Press**: one interaction with a key: a *tap*, a *long press* (a key with a second action), or a *hold* (a
hold-to-confirm key that acts only after being held). `deck.presses` decides which one happened.

**Flash**: the feedback drawn over a key while its action runs (`busy`) and just after (`ok` / `fail`), or while
a hold-to-confirm key is held (`hold`).

**Source**: something polled for state (T3 Code, the media session, the printer, ...). Each source has a name, and
its latest value is the snapshot field of that name. `deck.sources.registry.Sources` runs them.

**Snapshot**: every source's latest value, plus the time, at one moment. Widgets read only the snapshot.

**Widget**: a kind of key, such as `launch`, `media.now_playing` or `t3.threads`. Given its options from the
layout, the slots it was placed on, and the snapshot, a widget returns the keys for those slots. Widgets are
where behaviour lives ("Pause turns into Resume while printing").

**Screen**: a full page that a widget opens and owns, rather than one the user lays out (the new-thread picker).

**Page**: a named set of placements (`fun`, `ai`, `print`, or whatever the user calls theirs). The *bottom* row is
shared by every page.

**Placement**: one entry of the layout: a widget, the slot or slots it covers, and its options.

**Layout**: the pages and their placements, read from `deck.toml` and checked by `python -m deck check`.

**View**: where the deck is right now: which page or screen, its paging offset, and where *Back* goes.

**Plugin**: one integration (T3 Code, media, printer, desk switch, headphones), bundled as one module: its
sources, actions, widgets and badges. A plugin is on when its section is in `deck.toml`.

**Badge**: the small counter or mark on a page's nav key (threads needing you, print progress).

**Presence**: what the Windows session allows right now: `here`, `locked`, `away`, `signed_out`
(see `deck.desktop`). An action marked `requires("input")` or `requires("session")` is disabled when presence
doesn't allow it.

**Desk switch**: the wide key. It moves both monitors (DDC/CI) and the UGREEN USB switch between this PC and the
laptop. Its plugin is `kvm`.
