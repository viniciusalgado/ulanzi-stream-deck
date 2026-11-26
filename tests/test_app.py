import threading
import time

from deck.app import App
from deck.config import load_config
from deck.keys import Do, Key, View
from deck.paths import EXAMPLE_CONFIG
from deck.render import AppTile, Flashed, render


class FakeDeck:
    def __init__(self, on_key, **_):
        self.on_key = on_key
        self.shown = []

    def start(self):
        pass

    def close(self):
        pass

    def invalidate(self):
        pass

    def show(self, images):
        self.shown.append(images)


def app_with(keys=None):
    app = App(load_config(EXAMPLE_CONFIG), deck_factory=FakeDeck)
    app.view = View("ai")
    app._screen = keys or {}
    return app


def wait_for(cond, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# The press rules (tap / hold / long press / flashes) are tested in test_presses.py, the layout in test_layout.py
# and test_golden.py; these check the App wiring: a press reaches deck.presses, the named action runs on a
# worker, and the flash reaches the deck.

def screen_of(app, fn):
    app.actions.add("test.run", fn)
    tiles = {i: AppTile(None, str(i)) for i in range(14)}
    app.screen = lambda view=None: {i: Key(t, Do("test.run")) for i, t in tiles.items()}
    app._redraw()
    return tiles


def shows(app, index, tile):
    app._redraw()
    return app.deck.shown[-1][index] == render(tile)


def test_press_runs_the_named_action_and_the_deck_shows_busy_then_ok():
    gate, ran = threading.Event(), threading.Event()
    app = app_with()
    tiles = screen_of(app, lambda: (ran.set(), gate.wait(2)))
    try:
        app.deck.on_key(3, True)
        assert ran.wait(2)
        assert shows(app, 3, Flashed(tiles[3], "busy")) and shows(app, 4, tiles[4])
    finally:
        gate.set()
    assert wait_for(lambda: shows(app, 3, Flashed(tiles[3], "ok")))


def test_failed_action_shows_fail():
    def boom():
        raise RuntimeError("nope")
    app = app_with()
    tiles = screen_of(app, boom)
    app.deck.on_key(5, True)
    assert wait_for(lambda: shows(app, 5, Flashed(tiles[5], "fail")))


def test_main_loop_wakes_in_time_for_a_hold():
    app = app_with({8: Key(AppTile(None, "cancel"), Do("printer.cancel"), hold=0.3)})
    with app._cond:
        idle = app._wait_s()
    app.deck.on_key(8, True)
    with app._cond:
        assert app._wait_s() <= 0.31 < idle


def test_the_example_config_turns_on_every_built_in_plugin_and_starts_on_its_start_page():
    app = app_with()
    assert app.plugins == ["core", "t3", "media", "headphones", "printer", "kvm"]
    assert App(load_config(EXAMPLE_CONFIG), deck_factory=FakeDeck).view == View("ai")


def test_camera_still_shows_then_goes_back_to_the_icon(monkeypatch):
    from deck.sources.printer import Printer
    monkeypatch.setattr(Printer, "snapshot", lambda self: b"jpeg")
    app = app_with()
    app.actions.run("printer.camera")
    still = app.snapshot().camera
    assert still.jpeg == b"jpeg" and still.until > time.time()


def test_snapshot_carries_every_source_by_name():
    app = app_with()
    snap = app.snapshot()
    for name, value in app.sources.values().items():
        assert getattr(snap, name) is value
