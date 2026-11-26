"""The daemon: wires the plugins, the layout, presses and the deck together, and keeps the deck showing the view.

    App(load_config()).run()                                   # `deck run`: everything in this process
    App(cfg, desktop=RemoteDesktop(...), ddc=ConsoleDdc(...))  # the service: see deck.service

`desktop` is where everything that needs the signed-in user's session happens (T3 Code, music, headphones,
launching, hotkeys): deck.desktop.Desktop in this process, or the agent's RemoteDesktop in the service, which also
says whether anyone is signed in (the "desktop" source). `ddc` switches the monitors (the console helper's in the
service). Plugins reach both through deck.plugins.Setup.

Flow:
  - Plugins (deck.plugins) register sources, actions, widgets and badges. deck.layout reads the pages from
    deck.toml and checks them against what the plugins registered.
  - The sources (one thread each) store their state and mark the screen dirty. The main loop builds the current
    view with deck.layout.build (pure), renders tiles (cached), and Deck.show() sends only what changed.
  - A press looks up the key as it was on screen and goes to deck.presses, which decides what it means (tap,
    hold-to-confirm, long press) and which flash the key shows. The App carries out what it returns: navigate,
    or run the named action (deck.actions) on a worker. The main loop also wakes when a hold or a flash is due.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import layout, plugins, winutil
from .actions import Actions
from .desktop import Desktop
from .device import Deck
from .keys import Do, Go, Key, Snapshot, View
from .presses import Navigate, Presses, Run
from .render import Flashed, render
from .sources.registry import Sources
from .widgets import Widgets

log = logging.getLogger(__name__)


class App:
    def __init__(self, cfg: dict, *, deck_factory=Deck, desktop=None, ddc=None):
        self.cfg = cfg
        self.desktop = desktop if desktop is not None else Desktop.from_config(cfg)
        self._cond = threading.Condition()
        self._dirty = True
        self._stop = threading.Event()
        self._pressed_at = time.monotonic()
        self.sources = Sources(on_change=self._mark_dirty)    # names = Snapshot attributes
        self.actions = Actions()
        self.widgets = Widgets()
        self.desktop.attach(self.sources)              # t3, media, playlists, headphones (as configured), desktop
        self._setup = plugins.Setup(cfg, self.sources, self.actions, self.widgets, dirty=self._mark_dirty,
                                    showing=self._showing, pressed_at=lambda: self._pressed_at,
                                    desktop=self.desktop, ddc=ddc)
        self.plugins = plugins.load(cfg, self._setup)
        self.layout = layout.parse(cfg, self.widgets, self.actions)      # LayoutError: deck check says why
        self.view = View(self.layout.start_page)
        self.sources.task("guard", self._guard, every=30.0)
        self._screen: dict[int, Key] = {}
        self._presses = Presses()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="deck-action")
        self.deck = deck_factory(on_key=self._on_key, brightness=int(cfg["deck"].get("brightness", 85)),
                                 on_connect=self._mark_dirty)

    # ----- running -----

    def run(self) -> None:
        if self.cfg["deck"].get("stop_ulanzi_app", True) and winutil.is_running("UlanziDeck.exe"):
            log.info("closing the Ulanzi app: only one program can drive the deck")
            winutil.kill("UlanziDeck.exe")
        self.deck.start()
        self.sources.start()
        log.info("deck running (plugins: %s)", ", ".join(self.plugins))
        try:
            while not self._stop.is_set():
                with self._cond:
                    if not self._dirty:
                        self._cond.wait(timeout=self._wait_s())
                    self._dirty = False
                    self._apply(self._presses.tick(time.monotonic()))
                try:
                    self._redraw()
                except Exception:
                    log.exception("redraw failed")
                    time.sleep(1)
        finally:
            self.sources.stop()
            self.deck.close()
            self._pool.shutdown(wait=False, cancel_futures=True)

    def stop(self) -> None:
        self._stop.set()
        self._mark_dirty()

    def snapshot(self) -> Snapshot:
        return Snapshot(now=time.time(), **self.sources.values())

    def screen(self, view: View | None = None) -> dict[int, Key]:
        return layout.build(self.layout, self.widgets, view or self.view, self.snapshot(), self.actions)

    def _redraw(self) -> None:
        with self._cond:
            view = self.view
        screen = self.screen(view)
        with self._cond:
            overlay = self._presses.overlay(view, time.monotonic())
        tiles = {i: Flashed(key.tile, overlay[i]) if i in overlay else key.tile for i, key in screen.items()}
        with self._cond:
            if self.view == view:
                self._screen = screen
        self.deck.show({i: render(t) for i, t in tiles.items()})

    def _wait_s(self) -> float:
        """Called with self._cond held: how long the main loop may sleep before a redraw or a hold is due."""
        now = time.monotonic()
        snap = self.snapshot()
        lively = self._presses.overlay(self.view, now) or any(check(snap) for check in self._setup.lively_checks)
        wait = 1.0 if lively else 2.0
        due = self._presses.next_deadline()
        return wait if due is None else max(0.0, min(wait, due - now + 0.01))

    def _mark_dirty(self) -> None:
        with self._cond:
            self._dirty = True
            self._cond.notify()

    def _showing(self, prefix: str) -> bool:
        page = self.layout.page(self.view.page)
        return page is not None and any(p.widget.startswith(prefix) for p in page.placements)

    def _guard(self):
        if self.cfg["deck"].get("stop_ulanzi_app", True) and winutil.is_running("UlanziDeck.exe"):
            log.info("the Ulanzi app started again; closing it so the screens don't fight")
            winutil.kill("UlanziDeck.exe")
            self.deck.invalidate()
            self._mark_dirty()
        return None

    # ----- input -----

    def _on_key(self, index: int, pressed: bool) -> None:
        now = time.monotonic()
        with self._cond:
            if pressed:
                self._pressed_at = now
                effects = self._presses.press(index, self._screen.get(index), self.view, now)
            else:
                effects = self._presses.release(index, now)
            self._apply(effects)
            self._dirty = True
            self._cond.notify()

    def _apply(self, effects: list) -> None:
        """Called with self._cond held: carry out what deck.presses decided."""
        for e in effects:
            if isinstance(e, Navigate):
                self.view = e.view
            elif isinstance(e, Run):
                log.info("key %d on %s: %s", e.index, e.view.page, _describe(e.action))
                future = self._pool.submit(self.actions.run, e.action.action, *e.action.args)
                future.add_done_callback(lambda f, run=e: self._done(run, f))

    def _done(self, run: Run, future) -> None:
        err = future.exception()
        if err is not None:
            log.warning("action on key %d failed: %s", run.index, err)
        with self._cond:
            self._presses.completed(run.index, run.view, err is None, time.monotonic())
            self._dirty = True
            self._cond.notify()


def _describe(action) -> str:
    if isinstance(action, Go):
        return f"go {action.view.page}"
    assert isinstance(action, Do)
    args = tuple(a if isinstance(a, (str, int, float)) else getattr(a, "title", a) for a in action.args)
    return f"{action.action}{args}"
