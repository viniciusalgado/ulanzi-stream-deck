"""The daemon: polls the sources, turns presses into actions, and keeps the deck showing the current page.

    App(load_config()).run()                                   # `deck run`: everything in this process
    App(cfg, desktop=RemoteDesktop(...), ddc=ConsoleDdc(...))  # the service: see deck.service

The App owns the deck, the pages and the sources that work with nobody signed in (printer, desk switch). The
desktop features (T3, music, headphones, launching) come from `desktop` (deck.desktop.Desktop, or the agent's
RemoteDesktop in the service), which also says whether anyone is signed in.

Flow: the sources (deck.sources.registry, one thread each) store their state and mark the screen dirty -> the
main loop builds the current page (deck.pages, pure) -> renders tiles (cached) -> Deck.show() sends only what
changed. A press looks up the key as it was on screen and goes to deck.presses, which decides what it means
(tap, hold-to-confirm, long press) and which flash the key shows; the App carries out what it returns:
navigate, or run the action on a worker. The main loop wakes when a hold or a flash is due.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import pages, winutil
from .device import Deck
from .pages import Go, Key, Snapshot, View
from .presses import Navigate, Presses, Run
from .render import Flashed, render
from .desktop import Desktop, requires
from .sources.kvm import Kvm
from .sources.printer import Printer
from .sources.registry import Sources

log = logging.getLogger(__name__)
CAMERA_SHOW_S = 60    # a camera snapshot stays on its key this long, then the icon is back
DRAFT_S = 180         # after "New thread", Dictate says it will type into that new draft for this long


class App:
    def __init__(self, cfg: dict, *, deck_factory=Deck, desktop=None, ddc=None):
        self.cfg = cfg
        self.desktop = desktop if desktop is not None else Desktop.from_config(cfg)
        pr = cfg["printer"]
        self.printer = Printer(pr["api"], pr.get("webcam"))
        self.kvm = Kvm.from_config(cfg["kvm"], ddc=ddc)
        self.view = View(cfg["deck"].get("start_page", "ai"))
        self.sources = Sources(on_change=self._mark_dirty)    # names = pages.Snapshot fields
        self.desktop.attach(self.sources)                     # t3, media, playlists, headphones, desktop
        self.sources.add("printer", self.printer.poll, every=lambda: 2.0 if self.view.page == "print" else 6.0)
        self.sources.add("kvm", self.kvm.poll, every=3.0)
        self.sources.task("guard", self._guard, every=30.0)
        self._kvm_busy = False
        self._kvm_message = ("", "info", 0.0)     # text, level, shown until (time.time)
        self._dictating_until = 0.0
        self._draft_until = 0.0
        self._camera = (None, 0.0)                # jpeg, shown until (time.time)
        self._cond = threading.Condition()
        self._dirty = True
        self._stop = threading.Event()
        self._screen: dict[int, Key] = {}
        self._presses = Presses()
        self._pressed_at = time.monotonic()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="deck-action")
        self.deck = deck_factory(on_key=self._on_key, brightness=int(cfg["deck"].get("brightness", 85)),
                                 on_connect=self._mark_dirty)

    # ----- services the pages call (on a worker thread) -----

    def toggle_kvm(self) -> None:
        """The desk key: go to the other side of where the monitors are *now* (not where they were when drawn)."""
        self.switch_kvm("laptop" if self.kvm.side == "pc" else "pc")

    def camera_snapshot(self) -> None:
        """One webcam still on the camera key for CAMERA_SHOW_S. Only on demand: a stream of new images is what
        froze the deck's firmware (2026-11-25, twice)."""
        jpeg = self.printer.snapshot()
        if jpeg is None:
            raise RuntimeError("webcam unreachable")
        self._camera = (jpeg, time.time() + CAMERA_SHOW_S)
        self._mark_dirty()

    def switch_kvm(self, to: str) -> None:
        """Monitors first, as fast as possible (this is what the key waits for); then the USB switch on its own."""
        self._kvm_busy = True
        self._mark_dirty()
        try:
            self.kvm.switch(to)
            log.info("monitors sent to %s %.0f ms after the key press", to,
                     (time.monotonic() - self._pressed_at) * 1000)
            self._kvm_message = ("", "info", 0.0)
        except Exception as e:
            self._set_kvm_message(_short(str(e)), "error")
            raise
        finally:
            self._kvm_busy = False
            self.sources.refresh("kvm")
            self._mark_dirty()
        threading.Thread(target=self._usb_follow, args=(to,), name="usb-follow", daemon=True).start()

    def _usb_follow(self, to: str) -> None:
        note = self.kvm.switch_usb(to)
        if note:
            log.info("desk switched to %s; %s", to, note)
            self._set_kvm_message(_short(note), "info")
        self.sources.refresh("kvm")
        self._mark_dirty()

    def _set_kvm_message(self, text: str, level: str) -> None:
        self._kvm_message = (text, level, time.time() + 10)

    @requires("input")
    def toggle_dictation(self) -> None:
        self.desktop.dictate()
        self._dictating_until = 0.0 if self._dictating_until > time.time() else time.time() + 120

    @requires("input")
    def new_thread(self, project) -> None:
        """Open a new draft in T3 Code; the next Dictate press types into it (T3 dictates into the open thread)."""
        self.desktop.new_thread(project)
        self._dictating_until = 0.0
        self._draft_until = time.time() + DRAFT_S

    # ----- running -----

    def run(self) -> None:
        if self.cfg["deck"].get("stop_ulanzi_app", True) and winutil.is_running("UlanziDeck.exe"):
            log.info("closing the Ulanzi app: only one program can drive the deck")
            winutil.kill("UlanziDeck.exe")
        self.deck.start()
        self.sources.start()
        log.info("deck running")
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
        """Read-only: whatever has expired is left out; nothing is cleared."""
        now = time.time()
        text, level, text_until = self._kvm_message
        jpeg, camera_until = self._camera
        shown = now <= text_until
        return Snapshot(**self.sources.values(), camera=jpeg if now <= camera_until else None,
                        kvm_busy=self._kvm_busy, kvm_message=text if shown else "",
                        kvm_message_error=shown and level == "error",
                        dictating=self._dictating_until > now, new_draft=self._draft_until > now, now=now)

    def screen(self, view: View | None = None) -> dict[int, Key]:
        view = view or self.view
        page_cfg = {**self.cfg["pages"].get({"new_thread": "ai"}.get(view.page, view.page), {})}
        return pages.build(view, self.snapshot(), self, page_cfg)

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
        threads = self.sources.get("t3").threads
        animating = self.view.page in ("ai", "print") and any(t.state == "working" for t in threads)
        lively = (animating or self._presses.overlay(self.view, now) or self._kvm_busy
                  or self._camera[1] > time.time())
        wait = 1.0 if lively else 2.0
        due = self._presses.next_deadline()
        return wait if due is None else max(0.0, min(wait, due - now + 0.01))

    def _mark_dirty(self) -> None:
        with self._cond:
            self._dirty = True
            self._cond.notify()

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
                future = self._pool.submit(e.action.fn, *e.action.args)
                future.add_done_callback(lambda f, run=e: self._done(run, f))

    def _done(self, run: Run, future) -> None:
        err = future.exception()
        if err is not None:
            log.warning("action on key %d failed: %s", run.index, err)
        with self._cond:
            self._presses.completed(run.index, run.view, err is None, time.monotonic())
            self._dirty = True
            self._cond.notify()


def _short(text: str, n: int = 34) -> str:
    text = text.split(";")[0].strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _describe(action) -> str:
    if isinstance(action, Go):
        return f"go {action.view.page}"
    name = getattr(action.fn, "__qualname__", str(action.fn))
    args = tuple(a if isinstance(a, (str, int, float)) else getattr(a, "title", a) for a in action.args)
    return f"{name}{args}"
