"""Music: what's playing (any app, Spotify preferred), transport keys, and playlist shortcuts.

Interface:
    media = Media(playlists=["spotify:playlist:..."], spotify=SpotifyApi.load() or None,
                  played=PlayedPlaylists() or None)   # optional: start_playlist= (spotify_ui's), launcher=, sleep=
    state = media.poll()                 # MediaState from Windows' media session (title, artist, cover, playing)
    media.toggle() / next() / prev()
    media.play_playlist(uri)             # starts it via the Spotify Web API if logged in, else opens it in Spotify
    media.playlist(uri) -> Playlist      # title + cover, from Spotify's public oEmbed, cached on disk
    media.shelf() -> (Playlist, ...)     # with `played`: what you've played, newest first, then `playlists`;
                                         # else just `playlists`

Now-playing needs no account: it reads the Windows media session that Spotify (and browsers) publish.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass

from .. import winutil
from ..paths import CACHE_DIR

log = logging.getLogger(__name__)
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
RETRY_S = 3600.0   # a playlist Spotify couldn't describe (deleted, private, offline) is asked about again after this


@dataclass(frozen=True)
class MediaState:
    available: bool = False
    playing: bool = False
    title: str = ""
    artist: str = ""
    art: bytes | None = None
    app: str = ""


@dataclass(frozen=True)
class Playlist:
    uri: str
    title: str
    cover: bytes | None


class Media:
    def __init__(self, playlists: list[str], spotify=None, played=None, *, start_playlist=None, launcher=None,
                 sleep=time.sleep):
        self.playlists = playlists
        self.spotify = spotify
        self.played = played
        self._start_playlist = start_playlist
        self._launch = launcher or winutil.launch
        self._sleep = sleep
        self._art_key = None
        self._art = None
        self._loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        self._playlists: dict[str, Playlist] = {}
        self._failed: dict[str, float] = {}
        self._pending: set[str] = set()
        self._pool = ThreadPoolExecutor(6, thread_name_prefix="playlist")

    # ----- now playing -----

    def poll(self) -> MediaState:
        try:
            with self._lock:
                return self._loop.run_until_complete(self._poll())
        except Exception as e:
            log.debug("media poll failed: %s", e)
            return MediaState()

    async def _session(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        mgr = await Manager.request_async()
        sessions = list(mgr.get_sessions())
        spotify = [s for s in sessions if "spotify" in (s.source_app_user_model_id or "").lower()]
        if spotify:
            return spotify[0]
        return mgr.get_current_session() or (sessions[0] if sessions else None)

    async def _poll(self) -> MediaState:
        s = await self._session()
        if s is None:
            return MediaState()
        props = await s.try_get_media_properties_async()
        playing = s.get_playback_info().playback_status == PLAYING
        key = (props.title, props.artist)
        if key != self._art_key:
            self._art_key, self._art = key, await self._read_thumbnail(props.thumbnail)
        return MediaState(True, playing, props.title or "", props.artist or "", self._art,
                          s.source_app_user_model_id or "")

    @staticmethod
    async def _read_thumbnail(ref) -> bytes | None:
        if ref is None:
            return None
        try:
            from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
            stream = await ref.open_read_async()
            buf = Buffer(stream.size)
            await stream.read_async(buf, stream.size, InputStreamOptions.READ_AHEAD)
            data = bytearray(buf.length)
            DataReader.from_buffer(buf).read_bytes(data)
            return bytes(data)
        except Exception as e:
            log.debug("thumbnail read failed: %s", e)
            return None

    def _transport(self, method: str, fallback_key: str) -> None:
        async def go():
            s = await self._session()
            return bool(s) and await getattr(s, method)()
        try:
            with self._lock:
                if self._loop.run_until_complete(go()):
                    return
        except Exception as e:
            log.debug("%s failed: %s", method, e)
        winutil.media_key(fallback_key)   # no session yet (e.g. Spotify just opened): the media key still works

    def toggle(self) -> None:
        self._transport("try_toggle_play_pause_async", "media_play_pause")

    def next(self) -> None:
        self._transport("try_skip_next_async", "media_next")

    def prev(self) -> None:
        self._transport("try_skip_previous_async", "media_prev")

    # ----- playlists -----

    def play_playlist(self, uri: str) -> None:
        if self.spotify is not None:
            try:
                self.spotify.play_context(uri)
                return
            except Exception as e:
                log.warning("Spotify Web API play failed (%s); opening the playlist instead", e)
        # Without the Web API: open the playlist and press its Play button on screen (see spotify_ui).
        cover = self.playlist(uri).cover
        if cover is None:
            # The cover is how spotify_ui knows the page switched to this playlist; clicking without it can press
            # Play on the previous page. Open it and let the user press Play.
            self._launch(uri)
            raise RuntimeError("opened the playlist, but couldn't check its page (no cover): press Play in Spotify")
        start = self._start_playlist
        if start is None:
            from .spotify_ui import start_playlist as start
        before = self.poll()         # available=False: Windows couldn't say what was playing (unknown, not paused)
        if not start(uri, cover):
            if before.available and not before.playing:
                self.toggle()        # its page showed Pause but playback is paused: resume
            return
        for _ in range(16):          # confirm it really started
            self._sleep(0.25)
            now = self.poll()
            if now.playing and (not before.playing or (now.title, now.artist) != (before.title, before.artist)):
                return
        raise RuntimeError("pressed Play, but Spotify didn't start the playlist")

    @property
    def can_start_playlists(self) -> bool:
        return True   # via the Web API when logged in, else by pressing Play in the Spotify window

    def shelf(self, budget: float = 8.0) -> tuple[Playlist, ...]:
        """The playlists keys can offer. With `played`: every one you've played in Spotify, newest first, then the
        configured ones not among them. Played ones are looked up in parallel for up to `budget` seconds; those
        not back yet show on a later call, and those Spotify can't describe are left out."""
        if self.played is None:
            return tuple(self.playlist(uri) for uri in self.playlists)
        try:
            played = self.played.recent()
        except Exception as e:
            log.info("couldn't read Spotify's play history: %s", e)
            played = []
        now = time.time()
        todo = [u for u in played if u not in self._playlists and u not in self._pending
                and now - self._failed.get(u, -RETRY_S) >= RETRY_S]
        if todo:
            self._pending.update(todo)

            def look(uri):
                try:
                    if self._lookup(uri) is None:
                        self._failed[uri] = time.time()
                finally:
                    self._pending.discard(uri)
            wait([self._pool.submit(look, u) for u in todo], timeout=budget)
        out = [self._playlists[u] for u in played if u in self._playlists]
        shown = {p.uri for p in out}
        return tuple(out + [self.playlist(u) for u in self.playlists if u not in shown])

    def playlist(self, uri: str) -> Playlist:
        """Title and cover for a playlist; cached on disk after the first lookup."""
        return self._playlists.get(uri) or self._lookup(uri) or Playlist(uri, _short(uri), None)

    def _lookup(self, uri: str) -> Playlist | None:
        """Title and cover from the disk cache or Spotify's oEmbed; None when Spotify couldn't say (not cached)."""
        if uri in self._playlists:
            return self._playlists[uri]
        cache = CACHE_DIR / "playlists"
        name = hashlib.sha1(uri.encode()).hexdigest()[:16]
        meta_file, cover_file = cache / f"{name}.json", cache / f"{name}.img"
        title, cover = None, None
        if meta_file.is_file():
            title = json.loads(meta_file.read_text(encoding="utf-8")).get("title")
            cover = cover_file.read_bytes() if cover_file.is_file() else None
        else:
            try:
                url = "https://open.spotify.com/oembed?url=" + urllib.parse.quote(uri, safe="")
                with _opener.open(url, timeout=6) as r:
                    meta = json.load(r)
                title = meta.get("title")
                if meta.get("thumbnail_url"):
                    with _opener.open(meta["thumbnail_url"], timeout=6) as r:
                        cover = r.read()
                cache.mkdir(parents=True, exist_ok=True)
                meta_file.write_text(json.dumps({"title": title}), encoding="utf-8")
                if cover:
                    cover_file.write_bytes(cover)
            except Exception as e:
                gone = isinstance(e, urllib.error.HTTPError) and e.code == 404   # deleted or private: expected
                log.log(logging.DEBUG if gone else logging.INFO, "playlist lookup failed for %s: %s", uri, e)
                return None   # not cached: try again next time
        pl = Playlist(uri, title or _short(uri), cover)
        self._playlists[uri] = pl
        return pl


def _short(uri: str) -> str:
    m = re.search(r"playlist[:/]([A-Za-z0-9]+)", uri)
    return f"Playlist {m.group(1)[:5]}" if m else "Playlist"
