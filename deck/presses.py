"""What presses on the deck mean: taps, hold-to-confirm, long presses, and the busy / ok / fail flash on a key.

Pure and clock-driven: it never sleeps, starts threads or runs actions. The caller feeds it events with the
time they happened and carries out the effects it returns.

    presses = Presses()
    presses.press(index, key, view, now)      -> [Run(...)] | [Navigate(...)] | []
    presses.release(index, now)               -> same
    presses.tick(now)                         -> effects of holds that reached their time; call by next_deadline()
    presses.completed(index, view, ok, now)   # a Run finished: ok / fail flash for FLASH_S (no ok if quiet)
    presses.overlay(view, now)                -> {index: "hold" | "busy" | "ok" | "fail" | Charge} for that view
    presses.next_deadline(now)                -> monotonic time tick() or overlay() next changes, or None

Rules:
  - a plain key acts on press;
  - a `hold` key acts only after being held that long; letting go early cancels it;
  - a key with `long` runs `action` on a short tap (on release) and `long` when held for its long_s (or
    long_press_s);
  - a held key with a `long_label` shows a Charge filling up from CHARGE_SHOW_S on, in CHARGE_STEPS steps.
    A hold key without a label shows "hold" immediately;
  - a Do with `quiet` shows no "ok" flash when it worked (e.g. settling: the thread leaves the key anyway);
  - a Go navigates and clears every flash; a Do shows "busy" on its key until completed(), and its `then`
    navigates right away. Flashes belong to the view they were made on and only show there.
"""
from __future__ import annotations

from dataclasses import dataclass

from .pages import Do, Go, Key, View

FLASH_S = 1.2
LONG_PRESS_S = 1.5
CHARGE_SHOW_S = 0.3    # a tap never shows the ring
CHARGE_STEPS = 10


@dataclass(frozen=True)
class Charge:
    """A long press on its way: how far along (0..1, in steps) and what it will do."""
    progress: float
    label: str


@dataclass(frozen=True)
class Run:
    index: int
    view: View        # the view the key was pressed on (completed() needs it back)
    action: Do


@dataclass(frozen=True)
class Navigate:
    view: View


@dataclass(frozen=True)
class _Flash:
    kind: str         # hold | busy | ok | fail
    until: float      # only for ok / fail; hold and busy last until replaced
    view: View


@dataclass(frozen=True)
class _Hold:
    key: Key
    view: View
    at: float         # when it fires
    start: float = 0.0


class Presses:
    def __init__(self, *, long_press_s: float = LONG_PRESS_S, flash_s: float = FLASH_S):
        self._long_press_s = long_press_s
        self._flash_s = flash_s
        self._holds: dict[int, _Hold] = {}
        self._flash: dict[int, _Flash] = {}

    def press(self, index: int, key: Key | None, view: View, now: float) -> list:
        if key is None or (key.action is None and key.long is None):
            return []
        if key.hold > 0 or key.long is not None:
            if key.hold > 0 and not key.long_label:
                self._flash[index] = _Flash("hold", 0.0, view)
            self._holds[index] = _Hold(key, view, now + (key.hold or key.long_s or self._long_press_s), now)
            return []
        return self._fire(index, key.action, view)

    def release(self, index: int, now: float) -> list:
        held = self._holds.pop(index, None)
        if held is None:
            return []
        self._flash.pop(index, None)
        if held.key.long is not None and held.key.action is not None:
            return self._fire(index, held.key.action, held.view)    # a short tap on a key that also has a long press
        return []

    def tick(self, now: float) -> list:
        effects = []
        for index, held in sorted(self._holds.items()):
            if held.at <= now:
                del self._holds[index]
                effects += self._fire(index, held.key.long or held.key.action, held.view)
        for index, f in list(self._flash.items()):
            if f.kind in ("ok", "fail") and f.until <= now:
                del self._flash[index]
        return effects

    def completed(self, index: int, view: View, ok: bool, now: float, quiet: bool = False) -> None:
        if ok and quiet:
            f = self._flash.get(index)
            if f is not None and f.kind == "busy" and f.view == view:
                del self._flash[index]
            return
        self._flash[index] = _Flash("ok" if ok else "fail", now + self._flash_s, view)

    def overlay(self, view: View, now: float) -> dict[int, object]:
        shown: dict[int, object] = {i: f.kind for i, f in self._flash.items()
                                    if f.view == view and (f.kind in ("busy", "hold") or f.until > now)}
        for i, h in self._holds.items():
            if h.view == view and self._charging(h, now):
                step = int((now - h.start) / (h.at - h.start) * CHARGE_STEPS + 1e-9)
                shown[i] = Charge(min(step, CHARGE_STEPS) / CHARGE_STEPS, h.key.long_label)
        return shown

    def next_deadline(self, now: float | None = None) -> float | None:
        """With `now`, also the next step of a filling ring (each one needs a redraw)."""
        times = [h.at for h in self._holds.values()]
        times += [f.until for f in self._flash.values() if f.kind in ("ok", "fail")]
        if now is not None:
            for h in self._holds.values():
                if h.key.long_label:
                    span = (h.at - h.start) / CHARGE_STEPS
                    show_at = h.start + CHARGE_SHOW_S
                    steps = [show_at] + [h.start + k * span for k in range(1, CHARGE_STEPS)]
                    times.extend(t for t in steps if t > now and t >= show_at)
        return min(times, default=None)

    @staticmethod
    def _charging(h: _Hold, now: float) -> bool:
        return bool(h.key.long_label) and now >= h.start + CHARGE_SHOW_S

    def _fire(self, index: int, action, view: View) -> list:
        if action is None:
            return []
        if isinstance(action, Go):
            self._flash.clear()
            return [Navigate(action.view)]
        self._flash[index] = _Flash("busy", 0.0, view)
        effects = [Run(index, view, action)]
        if action.then is not None:
            effects.insert(0, Navigate(action.then))
        return effects
