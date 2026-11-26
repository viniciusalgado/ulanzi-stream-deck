# 2. Keys carry action names, not functions

Status: accepted, 2026-11-26

## Context

Keys held Python callables bound when the page was built (`Do(svc.media.toggle)`), with `svc` the whole App. The
set of things a key could do existed nowhere as data:

- `deck.toml` could not say what a key does.
- Nothing checked the pages against the App. Renaming an App method broke the deck while tests passed.
- The service / agent split (docs/plans/service-mode.md) has to send actions across a pipe, and a bound method
  can't cross it.

## Decision

A key holds `Do(name, args)`. An action registry (`deck.actions.Actions`) maps each name to a function, together
with what it needs from the Windows session (`requires`, from `deck.desktop`). The App runs a press by looking
the name up. Unknown names are errors when the layout is loaded, not when the key is pressed.

## Consequences

- The registry has two adapters: local functions (`deck run`), and forwarding to the agent over the pipe
  (`RemoteDesktop`, service mode). So it is a real seam, not a hypothetical one.
- Action arguments must be values that `deck.wire` can encode (strings, numbers, the state dataclasses).
- Tests compare `Do("media.toggle")` by value. The page tests need no fake App.
