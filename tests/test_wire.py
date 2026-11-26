import pytest

from deck import wire
from deck.sources.headphones import HeadphoneState
from deck.sources.media import MediaState, Playlist
from deck.sources.t3 import Environment, Project, T3State, Thread

ENV = Environment("pc", "This PC", "http://127.0.0.1:3773", ("t3.cmd", "--x"), primary=True)
PROJ = Project("p", "pc", "e", "3d", r"C:\x\3d", "mono:3D:#fff", 5.0)
STATE = T3State({"pc": True}, (PROJ,), (Thread("t", "e", PROJ, "Gearbox", "working", 1.0, 2.0),), (ENV,))


@pytest.mark.parametrize("value", [
    None, True, 3, 2.5, "x", b"\x00\xffjpeg", (1, "a"), [1, (2,)], {"a": {"b": (1,)}},
    MediaState(True, True, "Song", "Artist", b"art", "Spotify"), (Playlist("spotify:x", "X", None),),
    HeadphoneState(True, False, 40),
])
def test_values_round_trip(value):
    assert wire.loads(wire.dumps(value)) == value


def test_t3_state_round_trips_without_the_environments_command_line():
    back = wire.loads(wire.dumps(STATE))
    assert back.threads == STATE.threads and back.projects == STATE.projects
    assert back.envs[0].cli == () and back.envs[0].url == ENV.url
    assert b"t3.cmd" not in wire.dumps(STATE)


def test_a_cli_sneaked_into_the_data_is_dropped():
    raw = b'{"$o":"Environment","f":{"key":"pc","name":"n","url":"u","cli":{"$t":["calc.exe"]},"primary":true}}'
    assert wire.loads(raw).cli == ()


@pytest.mark.parametrize("raw", [
    b'{"$o":"Popen","f":{"args":"calc"}}',          # not an allowed type
    b'{"$o":"Project","f":{"nope":1}}',             # wrong fields
    b'{"x":1}', b'{"$t":1}', b"not json",
])
def test_anything_unknown_is_refused(raw):
    with pytest.raises(wire.WireError):
        wire.loads(raw)


def test_unknown_objects_cant_be_sent():
    with pytest.raises(wire.WireError):
        wire.dumps(object())
    with pytest.raises(wire.WireError):
        wire.dumps({1: "x"})
