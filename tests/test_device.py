import io
import json
import threading
import time
import zipfile

from deck import device
from deck.device import Deck


class FakeHidDevice:
    def __init__(self, hidmod):
        self.mod = hidmod

    def open_path(self, path):
        if self.mod.fail_open:
            raise OSError("busy")
        self.mod.opened += 1

    def write(self, data):
        if self.mod.unplugged:
            raise OSError("gone")
        if self.mod.frozen:            # what hidapi does when the firmware hangs: 1 s timeout, then -1
            return -1
        assert len(data) == device.PACKET + 1 and data[0] == 0
        self.mod.written.append(bytes(data[1:]))
        return len(data)

    def read(self, n, timeout):
        if self.mod.unplugged:
            raise OSError("gone")
        try:
            return self.mod.inbox.pop(0)
        except IndexError:
            time.sleep(timeout / 1000)
            return []

    def close(self):
        pass


class FakeHid:
    def __init__(self):
        self.written, self.inbox = [], []
        self.opened, self.fail_open, self.unplugged, self.present = 0, False, False, True
        self.frozen = False

    def enumerate(self, vid=0, pid=0):
        if not self.present:
            return []
        return [{"interface_number": 0, "usage_page": 0x0C, "path": b"p0"},
                {"interface_number": 1, "usage_page": 1, "path": b"p1"}]

    def device(self):
        return FakeHidDevice(self)


def screens(written):
    """Decode the zips sent so far: [(cmd, manifest, files)]."""
    out, i = [], 0
    while i < len(written):
        p = written[i]
        cmd = int.from_bytes(p[2:4], "big")
        length = int.from_bytes(p[4:8], "little")
        if p[:2] != b"\x7c\x7c" or cmd != device.CMD_SET_BUTTONS:
            i += 1
            continue
        data = p[8:]
        i += 1
        while len(data) < length:
            data += written[i]
            i += 1
        z = zipfile.ZipFile(io.BytesIO(data[:length]))
        out.append((cmd, json.loads(z.read("manifest.json")), {n: z.read(n) for n in z.namelist()}))
    return out


def png(n):
    return f"png-{n}".encode() * 50


def test_only_whole_screens_and_only_when_something_changed():
    hid = FakeHid()
    deck = Deck(on_key=lambda i, p: None, hid_module=hid)
    deck._try_open()
    images = {i: png(i) for i in range(14)}
    deck.show(images)
    deck.show(images)          # unchanged: nothing sent
    time.sleep(1 / device.MAX_FPS + 0.05)
    images[4] = png("changed")
    deck.show(images)
    sent = screens(hid.written)
    assert [s[0] for s in sent] == [device.CMD_SET_BUTTONS, device.CMD_SET_BUTTONS]
    full = sent[1][1]
    assert set(full) == {f"{c}_{r}" for r in range(3) for c in range(5)} - {"4_2"}
    assert full["3_2"]["SmallViewMode"] == device.SMALL_WINDOW_IMAGE   # the wide key shows our image
    assert sent[1][2][full["4_0"]["ViewParam"][0]["Icon"]] == png("changed")


def test_fast_updates_are_coalesced_to_the_latest_screen():
    hid = FakeHid()
    deck = Deck(on_key=lambda i, p: None, hid_module=hid)
    deck.start()
    time.sleep(device.SETTLE_S + 0.2)
    for n in range(10):                   # ten screens in a burst
        deck.show({i: png(f"{n}-{i}") for i in range(14)})
    time.sleep(1 / device.MAX_FPS + 0.3)
    sent = screens(hid.written)
    assert len(sent) <= 3                 # the first, then only the newest one
    last = sent[-1]
    assert last[2][last[1]["0_0"]["ViewParam"][0]["Icon"]] == png("9-0")
    deck.close()


def test_zip_avoids_the_firmware_byte_bug():
    images = {i: bytes(range(256)) * 40 + png(i) for i in range(14)}
    payload = device.screen_zip(images)
    assert all(payload[i] not in (0x00, 0x7C) for i in range(1016, len(payload), 1024))


def test_key_events_reach_the_callback_and_reconnect_resends_screen():
    hid = FakeHid()
    got = []
    connected = threading.Event()
    deck = Deck(on_key=lambda i, p: got.append((i, p)), hid_module=hid, on_connect=connected.set)
    hid.inbox.append(device.frame(device.IN_BUTTON, 4, bytes([0, 13, 1, 1])))
    hid.inbox.append(device.frame(device.IN_BUTTON, 4, bytes([0, 13, 1, 1])))   # a repeat report: ignored
    hid.inbox.append(device.frame(device.IN_BUTTON, 4, bytes([0, 13, 1, 0])))
    deck.start()
    deck.show({i: png(i) for i in range(14)})
    assert connected.wait(3)
    time.sleep(0.5)
    assert got == [(13, True), (13, False)]
    # unplug, then plug back in: the whole screen is sent again without the caller doing anything
    connected.clear()
    hid.unplugged = True
    time.sleep(0.5)
    assert not deck.connected
    hid.unplugged, before = False, len(hid.written)
    assert connected.wait(device.RECONNECT_POLL_S + device.SETTLE_S + 2)
    resent = screens(hid.written[before:])
    assert resent and resent[-1][0] == device.CMD_SET_BUTTONS and len(resent[-1][1]) == 14
    deck.close()


def test_a_frozen_deck_is_noticed_not_treated_as_shown():
    hid = FakeHid()
    d = device.Deck(on_key=lambda i, p: None, hid_module=hid)
    assert d._try_open()
    hid.frozen = True
    d.show({0: b"png-a"})
    assert not d.connected and d._sent == {}      # dropped, so it reconnects and resends everything
    hid.frozen = False
    assert d._try_open()
    d.show({0: b"png-a"})
    assert d._sent == {0: b"png-a"} and not d._unresponsive


# ----- on a fake clock: no sleeps, no threads -----

class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class FakePnp:
    def __init__(self, stuck=False):
        self.calls, self.is_stuck = 0, stuck

    def stuck(self):
        self.calls += 1
        return self.is_stuck


def test_an_unplugged_deck_asks_windows_whether_it_is_stuck_at_most_every_30_s():
    hid, pnp, clock = FakeHid(), FakePnp(), Clock()
    hid.present = False
    d = Deck(on_key=lambda i, p: None, hid_module=hid, pnp=pnp, clock=clock)
    for _ in range(120):                  # a minute of reconnect attempts, every 0.5 s
        assert not d._try_open()
        clock.t += device.RECONNECT_POLL_S
    assert pnp.calls == 2


def test_a_stuck_deck_is_reported_once_until_it_connects_again():
    hid, pnp, clock = FakeHid(), FakePnp(stuck=True), Clock()
    hid.present = False
    d = Deck(on_key=lambda i, p: None, hid_module=hid, pnp=pnp, clock=clock)
    for _ in range(3):
        d._try_open()
        clock.t += device.STUCK_CHECK_S
    assert pnp.calls == 1
    hid.present = True
    assert d._try_open()
    hid.present = False
    clock.t += device.STUCK_CHECK_S
    d._try_open()
    assert pnp.calls == 2


def test_screens_within_the_frame_rate_limit_wait_and_only_the_newest_is_sent():
    hid, clock = FakeHid(), Clock()
    d = Deck(on_key=lambda i, p: None, hid_module=hid, pnp=FakePnp(), clock=clock)
    d._try_open()
    d.show({0: png("a")})
    for n in range(5):                     # a burst in the same instant: held back
        d.show({0: png(f"burst-{n}")})
    assert len(screens(hid.written)) == 1
    wait = d._service(clock.t)
    assert 0 < wait <= 1 / device.MAX_FPS and len(screens(hid.written)) == 1
    clock.t += wait
    d._service(clock.t)
    sent = screens(hid.written)
    assert len(sent) == 2 and sent[-1][2][sent[-1][1]["0_0"]["ViewParam"][0]["Icon"]] == png("burst-4")


def test_keepalive_goes_out_every_few_seconds_and_not_more_often():
    hid, clock = FakeHid(), Clock()
    d = Deck(on_key=lambda i, p: None, hid_module=hid, pnp=FakePnp(), clock=clock)
    d._try_open()
    small = lambda: sum(1 for p in hid.written if int.from_bytes(p[2:4], "big") == device.CMD_SMALL_WINDOW)
    assert d._service(clock.t) == device.KEEPALIVE_S and small() == 0
    clock.t += device.KEEPALIVE_S
    assert d._service(clock.t) == device.KEEPALIVE_S and small() == 1
    d._service(clock.t + 1)
    assert small() == 1


def test_a_repeated_press_report_within_the_debounce_is_ignored():
    got, clock = [], Clock()
    d = Deck(on_key=lambda i, p: got.append((i, p)), hid_module=FakeHid(), pnp=FakePnp(), clock=clock)
    press = device.frame(device.IN_BUTTON, 4, bytes([0, 2, 1, 1]))
    d._handle_report(press)
    clock.t += device.DEBOUNCE_S / 2
    d._handle_report(press)
    clock.t += device.DEBOUNCE_S
    d._handle_report(press)
    assert got == [(2, True), (2, True)]
