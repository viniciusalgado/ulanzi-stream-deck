"""Music: now playing (any app, Spotify preferred), transport, Spotify playlists. On when deck.toml has [media].

    [media]
    playlists = ["spotify:playlist:..."]       # for the media.playlists widget, in order
    played = true                              # also every playlist played in the Spotify app, newest first
    spotify_app = 'shell:AppsFolder\\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify'   # opened when nothing plays

Sources (polled by the desktop: deck.desktop): media (MediaState), playlists (title + cover of each: with
`played`, the played ones newest first, then the configured ones; see deck.sources.spotify_history).
Actions: media.toggle, media.next, media.prev, media.play_playlist(uri)
Widgets: media.now_playing, media.prev, media.next, media.playlists (several slots; `all = true`: its last slot
opens the playlist screen). Screen: media.all_playlists (every playlist, paged). Badge: media (playing).
"""
from __future__ import annotations

from ..keys import Do, Go, Key, View, paged
from ..render import GREEN, MUTED, AppTile, ArtTile
from ..sources.media import MediaState
from ..widgets import Option

NAME = "media"
SECTION = "media"
SCREEN = "media.all_playlists"
SCREEN_SLOTS = (1, 2, 3, 4, 5, 6, 7, 8, 9)   # 0 is Back; the bottom row is every page's


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

    def playlist_key(pl, then=None):
        return Key(ArtTile(pl.cover, pl.title, fallback="lucide:list-music"),
                   Do("media.play_playlist", (pl.uri,), then=then))

    @w.widget("media.playlists", slots="many", needs=("playlists",),
              options=(Option("all", bool, False, "the last slot opens a screen with every playlist"),),
              doc="playlists one per slot (with [media] played: the latest played first); a tap starts it")
    def playlists(ctx):
        items, slots = ctx.snap.playlists or (), list(ctx.slots)
        keys = {}
        if ctx.options["all"]:
            more = slots.pop()
            keys[more] = Key(AppTile("lucide:list-music", "Playlists", sub=f"all {len(items)}" if items else "all",
                                     color=ctx.color), Go(View(SCREEN, back=ctx.page.name)))
        keys.update({slot: playlist_key(pl) for slot, pl in zip(slots, items)})
        return keys

    def all_playlists(ctx):
        back_page = ctx.page.name if ctx.page is not None else None
        keys = {0: Key(AppTile("lucide:arrow-left", "Back", color=MUTED), Go(View(back_page)))}
        items = list(ctx.snap.playlists or ())
        if not items:
            keys[1] = Key(AppTile("lucide:list-music", "Playlists", sub="none played yet", dim=True))
            return keys
        keys.update(paged(items, SCREEN_SLOTS, ctx.view, lambda pl: playlist_key(pl, then=View(back_page)),
                          lambda o: View(SCREEN, offset=o, back=ctx.view.back)))
        return keys

    w.screen(SCREEN, all_playlists)

    w.badge("media", lambda snap: ("▶", GREEN) if getattr(snap.media, "playing", False) else None)
