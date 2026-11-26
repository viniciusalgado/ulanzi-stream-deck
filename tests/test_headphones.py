import pytest

from deck.sources.headphones import (DEVICE_STATE_ACTIVE, DEVICE_STATE_NOTPRESENT, DEVICE_STATE_UNPLUGGED,
                                     ONESHOT_DISCONNECT, ONESHOT_RECONNECT, Endpoint, HeadphoneState, Headphones,
                                     music_endpoint)

A2DP = r"{2}.\\?\bthenum#{0000110b-0000-1000-8000-00805f9b34fb}_vid&0001_88c9e8aabbcc"
A2DP_OLD = r"{2}.\\?\bthenum#{0000110b-0000-1000-8000-00805f9b34fb}_vid&0001_stale0000000"
HFP = r"{2}.\\?\bthhfenum#{0000111e-0000-1000-8000-00805f9b34fb}_88c9e8aabbcc"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Audio:
    """The headset as Windows reports it. `connects_after` = RECONNECT calls it takes before the link comes up."""

    def __init__(self, connected=False, connects_after=1, extra=(), fail=False):
        self.connected, self.connects_after, self.extra, self.fail = connected, connects_after, list(extra), fail
        self.sent = []

    def endpoints(self, name):
        if self.fail:
            raise OSError("COM not available")
        state = DEVICE_STATE_ACTIVE if self.connected else DEVICE_STATE_UNPLUGGED
        return [*self.extra, Endpoint("Headset (WH-1000XM5)", DEVICE_STATE_ACTIVE, HFP),
                Endpoint("Fones (WH-1000XM5)", state, A2DP)]

    def oneshot(self, filter_id, op):
        self.sent.append((filter_id, op))
        if op == ONESHOT_DISCONNECT:
            self.connected = False
        elif sum(1 for _, o in self.sent if o == ONESHOT_RECONNECT) >= self.connects_after:
            self.connected = True

    def battery(self, filter_id):
        return 60


class Media:
    def __init__(self, playing):
        self.playing, self.toggles = playing, 0

    def poll(self):
        return type("S", (), {"playing": self.playing})()

    def toggle(self):
        self.toggles += 1


def headphones(audio, clock=None):
    clock = clock or Clock()
    return Headphones("WH-1000XM5", audio=audio, sleep=clock.sleep, clock=clock)


def test_the_active_music_endpoint_wins_over_stale_duplicates():
    stale = Endpoint("Fones (2- WH-1000XM5)", DEVICE_STATE_NOTPRESENT, A2DP_OLD)
    live = Endpoint("Fones (WH-1000XM5)", DEVICE_STATE_ACTIVE, A2DP)
    assert music_endpoint([stale, live]) == live
    off = Endpoint("Fones (WH-1000XM5)", DEVICE_STATE_UNPLUGGED, A2DP)
    assert music_endpoint([stale, off]) == off
    assert music_endpoint([Endpoint("Headset", DEVICE_STATE_ACTIVE, HFP)]) is None


def test_poll_reports_the_music_link_and_never_raises():
    assert headphones(Audio(connected=True)).poll() == HeadphoneState(True, True, 60)
    assert headphones(Audio(fail=True)).poll() == HeadphoneState()


def test_reclaim_reconnects_the_music_endpoint_and_retakes_playback():
    audio, media = Audio(extra=[Endpoint("Fones (2- WH-1000XM5)", DEVICE_STATE_NOTPRESENT, A2DP_OLD)]), Media(True)
    headphones(audio).reclaim(media)
    assert audio.sent == [(A2DP, ONESHOT_RECONNECT)] and media.toggles == 2


def test_reclaim_when_connected_only_retakes_and_skips_when_nothing_plays():
    audio = Audio(connected=True)
    headphones(audio).reclaim(Media(False))
    assert audio.sent == []


def test_reclaim_does_not_reconnect_when_windows_cant_be_asked():
    audio = Audio(fail=True)
    with pytest.raises(OSError):
        headphones(audio).reclaim(Media(True))
    assert audio.sent == []


def test_reclaim_gives_up_after_10_s():
    clock = Clock()
    with pytest.raises(RuntimeError, match="didn't connect"):
        headphones(Audio(connects_after=99), clock).reclaim()
    assert 10 <= clock.t <= 11


def test_reset_asks_again_when_the_first_reconnect_is_ignored():
    audio, media = Audio(connected=True, connects_after=2), Media(True)
    headphones(audio).reset(media)
    assert [op for _, op in audio.sent] == [ONESHOT_DISCONNECT, ONESHOT_RECONNECT, ONESHOT_RECONNECT]
    assert audio.connected and media.toggles == 2


def test_reset_fails_when_it_never_comes_back():
    with pytest.raises(RuntimeError, match="didn't reconnect"):
        headphones(Audio(connected=True, connects_after=99)).reset()
