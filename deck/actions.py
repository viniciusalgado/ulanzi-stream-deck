"""Named actions: everything a key can do, registered once by the plugins and run by name (docs/adr/0002).

    actions = Actions()
    actions.add("media.toggle", media.toggle, requires="session", doc="play / pause")
    actions.run("media.toggle")              # -> whatever the function returns; raises what it raises
    actions.run("launch", "steam://...")     # arguments as the key's Do carried them
    "media.toggle" in actions; actions.names(); actions.get("launch").doc
    actions.usable("launch", presence)       # may it run with the Windows session as it is now?

`requires` says what the action needs from the signed-in session (see deck.desktop): "input" (the unlocked
desktop: it opens windows or types), "session" (works while locked), or None (doesn't need the desktop: the
printer, the desk switch). A function already marked with deck.desktop.requires keeps its mark.
Unknown names raise UnknownAction; the layout checks every name it uses when it loads (deck check).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

LEVELS = (None, "session", "input")
ALLOWED = {"here": {"input", "session"}, "locked": {"session"}, "away": set(), "signed_out": set()}


class UnknownAction(KeyError):
    pass


@dataclass(frozen=True)
class Action:
    name: str
    fn: Callable
    requires: str | None = None
    doc: str = ""


class Actions:
    def __init__(self):
        self._by_name: dict[str, Action] = {}

    def add(self, name: str, fn: Callable, *, requires: str | None = None, doc: str = "") -> None:
        if name in self._by_name:
            raise ValueError(f"action {name!r} registered twice")
        requires = requires or getattr(fn, "requires", None)
        if requires not in LEVELS:
            raise ValueError(f"action {name!r}: requires must be one of {LEVELS}")
        self._by_name[name] = Action(name, fn, requires, doc or (fn.__doc__ or "").strip().split("\n")[0])

    def run(self, name: str, *args):
        return self.get(name).fn(*args)

    def get(self, name: str) -> Action:
        try:
            return self._by_name[name]
        except KeyError:
            raise UnknownAction(name) from None

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def usable(self, name: str, presence: str | None) -> bool:
        level = self.get(name).requires
        return level is None or presence is None or level in ALLOWED.get(presence, set())
