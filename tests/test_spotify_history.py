"""Played playlists: Spotify's own files (built here as protobuf bytes), the kept record, the shelf, and the keys."""
from types import SimpleNamespace as NS

from deck.keys import Do, Go, Snapshot, View
from deck.plugins import Setup, media as media_plugin
from deck.sources.media import Media, Playlist
from deck.sources.registry import Sources
from deck.sources.spotify_history import PlayedPlaylists, read_frecency, read_play_history
from deck.actions import Actions
from deck.widgets import Ctx, Widgets

A, B, C = (f"spotify:playlist:{c * 22}" for c in "abc")


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        out.append((n & 0x7F) | (0x80 if n > 0x7F else 0))
        n >>= 7
        if not n:
            return bytes(out)


def field(num: int, value) -> bytes:
    if isinstance(value, int):
        return varint(num << 3) + varint(value)
    value = value.encode() if isinstance(value, str) else value
    return varint(num << 3 | 2) + varint(len(value)) + value


def history(*plays) -> bytes:
    """list_player_play_history: (context uri, seconds) per track, newest first like Spotify writes it."""
    return b"".join(field(1, field(1, "spotify:track:" + "t" * 22) + field(2, uri) + field(3, "71c14129f0660b62")
                              + field(4, field(1, when) + field(2, 5)) + field(7, "stable:x") + field(8, 1))
                    for uri, when in plays)


def frecency(*entries) -> bytes:
    return b"".join(field(1, field(1, uri) + field(2, b"\t" + b"\0" * 8 + field(2, 3) + field(3, 4) + field(4, when)))
                    for uri, when in entries)


def test_reads_each_playlist_once_with_when_it_was_last_played():
    data = history((A, 300), (A, 200), (B, 100), ("spotify:album:" + "x" * 22, 400))
    assert read_play_history(data) == {A: 300.0, B: 100.0}
    assert read_frecency(frecency((C, 50), ("spotify:artist:" + "y" * 22, 60), (A, 10))) == {C: 50.0, A: 10.0}


def test_damaged_files_read_as_nothing_rather_than_failing():
    assert read_play_history(b"\x0a\xff\xff\xff") == {}
    assert read_frecency(history((A, 1))[:-7]) == {}


def test_lists_newest_first_and_keeps_what_spotify_forgets(tmp_path):
    user = tmp_path / "u-user"
    user.mkdir()
    (user / "list_player_play_history").write_bytes(history((A, 300), (B, 100)))
    (user / "frecency.pb").write_bytes(frecency((C, 200), (B, 50)))
    store = tmp_path / "played.json"
    assert PlayedPlaylists([user], store).recent() == [A, C, B]
    (user / "list_player_play_history").write_bytes(history((B, 400)))    # A dropped out of Spotify's history
    (user / "frecency.pb").unlink()
    again = PlayedPlaylists([user], store)                                # a fresh start reads the kept record
    assert again.recent() == [B, A, C]


def fake_media(played, configured=(), unknown=()):
    """A Media whose oEmbed lookup is replaced: `unknown` playlists can't be described; `asked` counts lookups."""
    m = Media(list(configured), played=NS(recent=lambda: list(played)))
    m.asked = []

    def lookup(uri):
        m.asked.append(uri)
        return None if uri in unknown else m._playlists.setdefault(uri, Playlist(uri, uri[-1], None))
    m._lookup = lookup
    return m


def test_the_shelf_is_played_first_then_configured_and_skips_what_spotify_cant_describe():
    m = fake_media([A, B], configured=[C, A], unknown={B})
    assert [p.uri for p in m.shelf()] == [A, C]
    assert [p.uri for p in m.shelf()] == [A, C]
    assert m.asked.count(B) == 1          # not asked again on every poll


def test_without_played_the_shelf_is_the_configured_list():
    m = Media([B, A])
    m.playlist = lambda uri: Playlist(uri, "x", None)
    assert [p.uri for p in m.shelf()] == [B, A]


def setup():
    s = Setup({}, Sources(), Actions(), Widgets(), section={}, desktop=NS())
    media_plugin.setup(s)
    return s


def build(s, name, view, snap, options=None, slots=(), page="fun"):
    w = s.widgets.get(name)
    opts, problems = s.widgets.options(w, options or {})
    assert not problems
    return w.build(Ctx(view, snap, tuple(slots), opts, NS(name=page, color="#f472b6"), ()))


def shelf(n):
    return tuple(Playlist(f"spotify:playlist:{i:022d}", f"P{i}", None) for i in range(n))


def test_with_all_the_last_slot_opens_the_playlist_screen():
    s = setup()
    keys = build(s, "media.playlists", View("fun"), Snapshot(playlists=shelf(5)), {"all": True}, (2, 3, 4))
    assert [keys[2].tile.label, keys[3].tile.label] == ["P0", "P1"]
    assert keys[4].action == Go(View("media.all_playlists", back="fun")) and keys[4].tile.sub == "all 5"


def test_the_screen_pages_every_playlist_and_a_tap_plays_it_and_goes_back():
    s, snap = setup(), Snapshot(playlists=shelf(20))
    screen = s.widgets.screens["media.all_playlists"]
    ctx = lambda view: Ctx(view, snap, (), {}, NS(name="fun", color="#f472b6"), ())
    first = screen(ctx(View("media.all_playlists", back="fun")))
    assert first[0].action == Go(View("fun"))
    assert first[1].action == Do("media.play_playlist", (shelf(20)[0].uri,), then=View("fun"))
    assert first[9].action == Go(View("media.all_playlists", offset=8, back="fun"))
    last = screen(ctx(View("media.all_playlists", offset=16, back="fun")))
    assert [last[i].tile.label for i in range(1, 5)] == ["P16", "P17", "P18", "P19"]
