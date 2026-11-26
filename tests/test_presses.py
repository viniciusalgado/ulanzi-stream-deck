import pytest

from deck.keys import Do, Go, Key, View
from deck.presses import CHARGE_STEPS, Charge, Navigate, Presses, Run
from deck.render import AppTile

AI, FUN = View("ai"), View("fun")


def key(action=None, **kw):
    return Key(AppTile(None, "x"), action, **kw)


_n = iter(range(10**6))


def act():
    return Do(f"action-{next(_n)}")


def test_a_plain_key_runs_on_press_and_shows_busy_then_ok_then_nothing():
    p, a = Presses(flash_s=1.0), act()
    assert p.press(5, key(a), AI, now=0) == [Run(5, AI, a)]
    assert p.overlay(AI, 0) == {5: "busy"}
    assert p.release(5, 0.1) == []
    p.completed(5, AI, ok=True, now=2)
    assert p.overlay(AI, 2.5) == {5: "ok"} and p.next_deadline() == 3
    p.tick(3)
    assert p.overlay(AI, 3) == {} and p.next_deadline() is None


def test_a_failed_action_flashes_fail():
    p = Presses()
    p.press(5, key(act()), AI, 0)
    p.completed(5, AI, ok=False, now=0)
    assert p.overlay(AI, 0.1) == {5: "fail"}


def test_hold_key_runs_only_when_held_long_enough():
    p, a = Presses(), act()
    assert p.press(8, key(a, hold=2.0), AI, now=0) == []
    assert p.overlay(AI, 0) == {8: "hold"} and p.next_deadline() == 2.0
    assert p.tick(1.0) == []
    assert p.release(8, 1.5) == []                # let go early: nothing runs, the hold mark goes
    assert p.overlay(AI, 1.5) == {} and p.tick(5) == []
    p.press(8, key(a, hold=2.0), AI, now=10)
    assert p.tick(12) == [Run(8, AI, a)]
    assert p.overlay(AI, 12) == {8: "busy"}
    assert p.release(8, 12.5) == []               # released after it fired: nothing more


def test_long_press_key_taps_and_holds_differently():
    p, tap, hold = Presses(long_press_s=1.5), act(), act()
    k = key(tap, long=hold)
    assert p.press(5, k, AI, now=0) == []
    assert p.overlay(AI, 0) == {}                 # no "hold" mark: a tap must look instant
    assert p.release(5, 0.2) == [Run(5, AI, tap)]
    p.press(5, k, AI, now=10)
    assert p.tick(11.4) == []
    assert p.tick(11.5) == [Run(5, AI, hold)]
    assert p.release(5, 12) == []


def test_go_navigates_and_clears_every_flash():
    p = Presses()
    p.press(1, key(act()), AI, 0)
    assert p.press(10, key(Go(FUN)), AI, 0) == [Navigate(FUN)]
    assert p.overlay(AI, 0) == {}


def test_then_navigates_at_once_and_the_flash_stays_on_the_view_it_was_pressed_on():
    p, a = Presses(), Do("x", then=FUN)
    assert p.press(2, key(a), AI, 0) == [Navigate(FUN), Run(2, AI, a)]
    assert p.overlay(FUN, 0) == {} and p.overlay(AI, 0) == {2: "busy"}


def test_keys_without_actions_and_empty_slots_do_nothing():
    p = Presses()
    assert p.press(3, None, AI, 0) == [] and p.press(3, key(), AI, 0) == []
    assert p.overlay(AI, 0) == {} and p.next_deadline() is None


def test_a_charging_long_press_fills_a_ring_then_fires():
    p, tap, settle = Presses(), act(), act()
    k = key(tap, long=settle, long_s=3.0, long_label="Settle")
    assert p.press(4, k, AI, now=0) == []
    assert p.overlay(AI, 0.1) == {}                       # a tap never shows the ring
    assert p.next_deadline(0.1) == 0.3                    # ... but it appears soon after
    assert p.overlay(AI, 0.35) == {4: Charge(0.1, "Settle")}
    assert p.overlay(AI, 1.5) == {4: Charge(0.5, "Settle")}
    assert p.next_deadline(1.5) == pytest.approx(3.0 * 6 / CHARGE_STEPS)  # the next step, so it animates
    assert p.overlay(FUN, 1.5) == {}                      # only on the view it was pressed on
    assert p.tick(2.9) == []
    assert p.tick(3.0) == [Run(4, AI, settle)]
    assert p.overlay(AI, 3.0) == {4: "busy"}
    assert p.release(4, 3.2) == []


def test_letting_go_of_a_charging_key_early_is_a_tap():
    p, tap, settle = Presses(), act(), act()
    k = key(tap, long=settle, long_s=3.0, long_label="Settle")
    p.press(4, k, AI, now=0)
    assert p.overlay(AI, 2.0) == {4: Charge(0.6, "Settle")}
    assert p.release(4, 2.0) == [Run(4, AI, tap)]
    assert p.tick(10) == []


def test_a_charging_hold_animates_but_has_no_tap_action():
    p, power_off = Presses(), act()
    view = View("print")
    k = key(power_off, hold=2.0, long_label="Power Off")

    assert p.press(8, k, view, now=0) == []
    assert p.next_deadline(0.1) == 0.3
    assert p.overlay(view, 1.0) == {8: Charge(0.5, "Power Off")}
    assert p.release(8, 1.0) == []
    assert p.overlay(view, 1.0) == {} and p.tick(2.0) == []

    assert p.press(8, k, view, now=3.0) == []
    assert p.tick(4.99) == []
    assert p.tick(5.0) == [Run(8, view, power_off)]


def test_a_quiet_action_shows_no_ok_but_still_shows_fail():
    p, a = Presses(), act()
    p.press(4, key(a), AI, 0)
    p.completed(4, AI, ok=True, now=1, quiet=True)
    assert p.overlay(AI, 1) == {} and p.next_deadline() is None
    p.press(4, key(a), AI, 2)
    p.completed(4, AI, ok=False, now=3, quiet=True)
    assert p.overlay(AI, 3) == {4: "fail"}
