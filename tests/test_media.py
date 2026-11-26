import pytest

from deck.sources.media import Media, MediaState, Playlist

URI = "spotify:playlist:abc"
PLAYING_OTHER = MediaState(True, True, "Other song", "Someone")
PAUSED = MediaState(True, False, "Other song", "Someone")
UNKNOWN = MediaState()                      # what poll() returns when Windows' media session can't be read


def media(cover=b"cover", polls=(), clicked=True):
    """A Media without Windows: playlist cover, a scripted sequence of poll() results, and the on-screen click."""
    m = Media([URI], start_playlist=lambda uri, c: (m.started.append((uri, c)), clicked)[1],
              launcher=lambda t: m.launched.append(t), sleep=lambda s: None)
    m.started, m.launched, m.toggles = [], [], 0
    seq = list(polls)
    m.poll = lambda: seq.pop(0) if len(seq) > 1 else seq[0]
    m.playlist = lambda uri: Playlist(uri, "P", cover)
    m.toggle = lambda: setattr(m, "toggles", m.toggles + 1)
    return m


def test_clicks_play_and_confirms_the_new_playlist_started():
    m = media(polls=[PLAYING_OTHER, PLAYING_OTHER, MediaState(True, True, "First track", "Band")])
    m.play_playlist(URI)
    assert m.started == [(URI, b"cover")]


def test_raises_when_play_was_pressed_but_nothing_changed():
    m = media(polls=[PLAYING_OTHER])
    with pytest.raises(RuntimeError, match="didn't start"):
        m.play_playlist(URI)


def test_without_a_cover_it_opens_the_playlist_but_never_clicks():
    m = media(cover=None, polls=[PAUSED])
    with pytest.raises(RuntimeError, match="no cover"):
        m.play_playlist(URI)
    assert m.launched == [URI] and m.started == []


def test_already_showing_pause_but_paused_resumes():
    m = media(polls=[PAUSED], clicked=False)
    m.play_playlist(URI)
    assert m.toggles == 1


def test_unknown_playback_state_is_not_taken_for_paused():
    m = media(polls=[UNKNOWN], clicked=False)    # toggling here could pause music that is playing
    m.play_playlist(URI)
    assert m.toggles == 0
