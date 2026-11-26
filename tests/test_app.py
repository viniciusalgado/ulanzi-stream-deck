import threading
import time

from deck.app import App
from deck.config import load_config
from deck.paths import EXAMPLE_CONFIG
from deck.pages import Do, Key, View
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


def app_with(keys):
    app = App(load_config(EXAMPLE_CONFIG), deck_factory=FakeDeck)
    app.view = View("ai")
    app._screen = keys
    return app


def wait_for(cond, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# The press rules (tap / hold / long press / flashes) are tested in test_presses.py; these check the App wiring:
# a press reaches deck.presses, its action runs on a worker, and the flash reaches the deck.

def screen_of(app, fn):
    tiles = {i: AppTile(None, str(i)) for i in range(14)}
    app.screen = lambda view=None: {i: Key(t, Do(fn)) for i, t in tiles.items()}
    app._redraw()
    return tiles


def shows(app, index, tile):
    app._redraw()
    return app.deck.shown[-1][index] == render(tile)


def test_press_runs_the_action_and_the_deck_shows_busy_then_ok():
    gate, ran = threading.Event(), threading.Event()
    app = app_with({})
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
    app = app_with({})
    tiles = screen_of(app, boom)
    app.deck.on_key(5, True)
    assert wait_for(lambda: shows(app, 5, Flashed(tiles[5], "fail")))


def test_main_loop_wakes_in_time_for_a_hold():
    app = app_with({8: Key(AppTile(None, "cancel"), Do(lambda: None), hold=0.3)})
    with app._cond:
        idle = app._wait_s()
    app.deck.on_key(8, True)
    with app._cond:
        assert app._wait_s() <= 0.31 < idle


def test_desk_key_toggles_from_where_the_monitors_are_now():
    app = app_with({})
    sent = []
    app.switch_kvm = sent.append
    app.kvm.side = "pc"
    app.toggle_kvm()
    app.kvm.side = "laptop"
    app.toggle_kvm()
    assert sent == ["laptop", "pc"]


def test_camera_snapshot_shows_then_goes_back_to_the_icon():
    app = app_with({})
    app.printer.snapshot = lambda: b"jpeg"
    app.camera_snapshot()
    assert app.snapshot().camera == b"jpeg"
    app._camera = (b"jpeg", 0.0)                  # its time is up
    assert app.snapshot().camera is None


def test_snapshot_carries_every_source_by_name():
    app = app_with({})
    snap = app.snapshot()
    for name, value in app.sources.values().items():
        assert getattr(snap, name) is value
