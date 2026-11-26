"""The sources the daemon polls, each on its own thread, and the latest value of each.

    sources = Sources(on_change=mark_dirty)
    sources.add("t3", t3.poll, every=2.0, initial=T3State())
    sources.add("printer", printer.poll, every=lambda: 2.0 if busy else 6.0)
    sources.task("guard", guard, every=30.0)    # runs periodically, stores nothing
    sources.add("t3", None, initial=T3State())  # pushed, not polled: changes only through put()
    sources.put("t3", value)                    # store a value that came from elsewhere (notifies if it changed)
    sources.start()
    sources.values()          # {"t3": T3State(...), ...}: one entry per source, names = Snapshot attributes (deck.keys)
    sources.refresh("kvm")    # poll one now (after an action changed it), store it, notify
    sources.poll_all()        # every source once, on this thread; tasks don't run (preview / status)
    sources.stop()

A poll returns the source's current state; `None` means "nothing new" and keeps the last value. A poll that
raises is logged and keeps the last value too, so a flaky source never takes the deck down. `on_change` is called
(from the polling thread) only when a value actually changed. Values are read and written under one lock.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

Interval = float | Callable[[], float]


@dataclass
class _Entry:
    poll: Callable[[], object] | None   # None: pushed with put(), never polled
    every: Callable[[], float]
    stores: bool


class Sources:
    def __init__(self, on_change: Callable[[], None] = lambda: None):
        self._on_change = on_change
        self._entries: dict[str, _Entry] = {}
        self._values: dict[str, object] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def add(self, name: str, poll: Callable[[], object] | None, *, every: Interval = 0.0,
            initial: object = None) -> None:
        self._register(name, poll, every, stores=True)
        self._values[name] = initial

    def put(self, name: str, value: object) -> None:
        if name not in self._entries or not self._entries[name].stores:
            raise KeyError(f"no source {name!r}")
        self._store(name, value)

    def task(self, name: str, fn: Callable[[], object], *, every: Interval) -> None:
        self._register(name, fn, every, stores=False)

    def values(self) -> dict[str, object]:
        with self._lock:
            return dict(self._values)

    def get(self, name: str) -> object:
        with self._lock:
            return self._values[name]

    def refresh(self, name: str) -> object:
        if self._entries[name].poll is not None:
            self._run(name)
        return self.get(name) if self._entries[name].stores else None

    def poll_all(self) -> None:
        for name, entry in self._entries.items():
            if entry.stores and entry.poll is not None:
                self._run(name)

    def start(self) -> None:
        for name in [n for n, e in self._entries.items() if e.poll is not None]:
            threading.Thread(target=self._loop, args=(name,), name=f"poll-{name}", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # ----- internals -----

    def _register(self, name: str, fn, every: Interval, stores: bool) -> None:
        if name in self._entries:
            raise ValueError(f"source {name!r} added twice")
        self._entries[name] = _Entry(fn, every if callable(every) else (lambda: every), stores)

    def _run(self, name: str) -> None:
        entry = self._entries[name]
        try:
            value = entry.poll()
        except Exception:
            log.exception("%s poll failed", name)
            return
        if not entry.stores or value is None:
            return
        self._store(name, value)

    def _store(self, name: str, value: object) -> None:
        with self._lock:
            changed = value != self._values.get(name)
            if changed:
                self._values[name] = value
        if changed:
            self._on_change()

    def _loop(self, name: str) -> None:
        entry = self._entries[name]
        while not self._stop.is_set():
            self._run(name)
            self._stop.wait(entry.every())
