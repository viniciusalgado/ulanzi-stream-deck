"""Music: now playing (any app, Spotify preferred), transport, Spotify playlists. On when deck.toml has [media].

    [media]
    playlists = ["spotify:playlist:..."]       # for the media.playlists widget, in order
    spotify_app = 'shell:AppsFolder\\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify'   # opened when nothing plays

Sources (polled by the desktop: deck.desktop): media (MediaState), playlists (title + cover of each).
Actions: media.toggle, media.next, media.prev, media.play_playlist(uri)
Widgets: media.now_playing, media.prev, media.next, media.playlists (several slots). Badge: media (playing).
"""
from __future__ import annotations

from ..keys import Do, Key
from ..render import GREEN, AppTile, ArtTile
from ..sources.media import MediaState
from ..widgets import Option

NAME = "media"
SECTION = "media"


def setup(s) -> None:
    s.actions.add("media.toggle", lambda: s.desktop.media_toggle(), requires="session", doc="play / pause")
    s.actions.add("media.next", lambda: s.desktop.media_next(), requires="session", doc="next track")
    s.actions.add("media.prev", lambda: s.desktop.media_prev(), requires="session", doc="previous track")
    s.actions.add("media.play_playlist", lambda uri: s.desktop.play_playlist(uri), requires="input",
                  doc="start a Spotify playlist")
    w = s.widgets

    @w.widget("media.now_playing", needs=("media",),
              options=(Option("app", str, s.section.get("spotify_app", "spotify:"), "opened when nothing plays"),),
              doc="cover, title and play / pause; opens the music app when nothing plays")
    def now_playing(ctx):
        m = ctx.snap.media or MediaState()
        if m.available:
            return Key(ArtTile(m.art, m.title or "Playing", m.artist,
                               glyph="lucide:pause" if m.playing else "lucide:play",
                               accent=GREEN if m.playing else None), Do("media.toggle"))
        return Key(AppTile("brand:spotify", "Spotify", sub="not playing", color="#1ed760"),
                   Do("launch", (ctx.options["app"],)))

    def transport(icon: str, label: str, action: str):
        def build(ctx):
            has = getattr(ctx.snap.media, "available", False)
            return Key(AppTile(icon, label, dim=not has), Do(action) if has else None)
        return build

    w.widget("media.prev", needs=("media",), doc="previous track")(transport("lucide:skip-back", "Previous",
                                                                             "media.prev"))
    w.widget("media.next", needs=("media",), doc="next track")(transport("lucide:skip-forward", "Next", "media.next"))

    @w.widget("media.playlists", slots="many", needs=("playlists",),
              doc="the [media] playlists, one per slot; a tap starts it")
    def playlists(ctx):
        return {slot: Key(ArtTile(pl.cover, pl.title, fallback="lucide:list-music"),
                          Do("media.play_playlist", (pl.uri,)))
                for slot, pl in zip(ctx.slots, ctx.snap.playlists or ())}

    w.badge("media", lambda snap: ("▶", GREEN) if getattr(snap.media, "playing", False) else None)
