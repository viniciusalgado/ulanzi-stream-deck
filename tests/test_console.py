"""The service's ConsoleDdc and the monitor helper, over a real pipe pair."""
import threading
from multiprocessing import Pipe

import pytest

from deck import console
from deck.console import ConsoleDdc, run_helper
from deck.winpipe import Identity
from deck.winsession import Console


class FakeChild:
    def __init__(self, pid):
        self.pid, self.alive_ = pid, True

    def alive(self):
        return self.alive_

    def kill(self):
        self.alive_ = False


class FakeDdc:
    def __init__(self):
        self.sent = []

    def models(self):
        return ["27G2", "24G2HE5"]

    def set_inputs(self, codes):
        self.sent.append(codes)
        return {m: True for m in codes}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def ddc_with_spawns():
    spawned = []

    def spawn(session, signed_in):
        child = FakeChild(1000 + len(spawned))
        spawned.append((session, signed_in, child))
        return child
    clock = Clock()
    return ConsoleDdc(spawn, clock=clock), spawned, clock


def connect_helper(ddc, pid, fake=None):
    service_end, helper_end = Pipe()
    fake = fake or FakeDdc()
    t = threading.Thread(target=run_helper, args=(fake, lambda name: helper_end), daemon=True)
    t.start()
    ddc.accept(service_end, Identity(pid, 1, "S-1-5-18"))
    return fake, t


def test_helper_follows_the_console_session():
    ddc, spawned, clock = ddc_with_spawns()
    ddc.follow(Console(2, "", False))                 # login screen
    ddc.follow(Console(2, "", False))                 # same session: nothing new
    ddc.follow(Console(None))                         # between sessions: keep it
    ddc.follow(Console(1, "Vinicius", False))         # signed in: a new helper on the user's desktop
    assert [(s, signed_in) for s, signed_in, _ in spawned] == [(2, False), (1, True)]
    assert not spawned[0][2].alive()


def test_a_dying_helper_is_restarted_but_not_in_a_tight_loop():
    ddc, spawned, clock = ddc_with_spawns()
    ddc.follow(Console(1, "V", False))
    spawned[0][2].kill()
    ddc.follow(Console(1, "V", False))
    assert len(spawned) == 1
    clock.t += console.RESPAWN_S
    ddc.follow(Console(1, "V", False))
    assert len(spawned) == 2


def test_monitor_calls_go_through_the_helper():
    ddc, spawned, _ = ddc_with_spawns()
    ddc.follow(Console(2, "", False))
    fake, _ = connect_helper(ddc, spawned[0][2].pid)
    assert ddc.models() == ["27G2", "24G2HE5"]
    assert ddc.set_inputs({"27G2": 0x12}) == {"27G2": True}
    assert fake.sent == [{"27G2": 0x12}]
    ddc.close()


def test_only_the_spawned_helper_is_accepted(monkeypatch):
    monkeypatch.setattr(console, "CONNECT_WAIT_S", 0.3)
    ddc, spawned, _ = ddc_with_spawns()
    ddc.follow(Console(2, "", False))
    connect_helper(ddc, 4242)                          # some other SYSTEM process
    with pytest.raises(ConnectionError):
        ddc.set_inputs({"27G2": 0x12})
