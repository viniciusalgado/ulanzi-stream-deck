"""Optional Spotify Web API access, only so playlist keys can *start* a playlist (needs Spotify Premium).

Without it the playlist keys open the playlist in the Spotify app and you press play there.

One-time setup:
  1. https://developer.spotify.com/dashboard -> Create app, Redirect URI  http://127.0.0.1:8765/callback ,
     API "Web API". Copy the Client ID (no secret is needed).
  2. .venv\\Scripts\\python -m deck spotify-login <client id>

The refresh token is stored in %APPDATA%\\ulanzi-deck\\spotify.json.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser

from .. import winutil
from ..paths import STATE_DIR

FILE = STATE_DIR / "spotify.json"
REDIRECT = "http://127.0.0.1:8765/callback"
SCOPES = "user-modify-playback-state user-read-playback-state"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post_form(url: str, form: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with _opener.open(req, timeout=10) as r:
        return json.load(r)


def login(client_id: str) -> None:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and go back to the deck.")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8765), Handler)
    print("Opening Spotify in your browser to approve access...")
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        server.handle_request()
    if got.get("state") != state or "code" not in got:
        raise SystemExit(f"Spotify login failed: {got.get('error', 'state mismatch')}")
    tok = _post_form("https://accounts.spotify.com/api/token", {
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
        "client_id": client_id, "code_verifier": verifier})
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps({"client_id": client_id, "refresh_token": tok["refresh_token"]}))
    print(f"Saved. Playlist keys will now start playback. ({FILE})")


class SpotifyApi:
    def __init__(self, client_id: str, refresh_token: str):
        self.client_id, self.refresh_token = client_id, refresh_token
        self._access, self._expires = "", 0.0

    @classmethod
    def load(cls) -> "SpotifyApi | None":
        if not FILE.is_file():
            return None
        d = json.loads(FILE.read_text())
        return cls(d["client_id"], d["refresh_token"])

    def _token(self) -> str:
        if time.time() < self._expires - 30:
            return self._access
        tok = _post_form("https://accounts.spotify.com/api/token", {
            "grant_type": "refresh_token", "refresh_token": self.refresh_token, "client_id": self.client_id})
        self._access, self._expires = tok["access_token"], time.time() + tok.get("expires_in", 3600)
        if tok.get("refresh_token") and tok["refresh_token"] != self.refresh_token:
            self.refresh_token = tok["refresh_token"]
            FILE.write_text(json.dumps({"client_id": self.client_id, "refresh_token": self.refresh_token}))
        return self._access

    def _call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request("https://api.spotify.com/v1" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self._token()}",
                                              "Content-Type": "application/json"})
        with _opener.open(req, timeout=10) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def _device(self) -> str | None:
        devices = (self._call("GET", "/me/player/devices") or {}).get("devices", [])
        me = os.environ.get("COMPUTERNAME", "").lower()
        for pick in (lambda d: d.get("is_active"), lambda d: d.get("name", "").lower() == me,
                     lambda d: d.get("type") == "Computer"):
            for d in devices:
                if pick(d):
                    return d["id"]
        return devices[0]["id"] if devices else None

    def play_context(self, uri: str) -> None:
        device = self._device()
        if device is None:   # Spotify isn't open anywhere: open it here and wait for it to register
            winutil.launch("shell:AppsFolder\\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")
            for _ in range(12):
                time.sleep(1)
                if (device := self._device()) is not None:
                    break
        if device is None:
            raise RuntimeError("no Spotify device found")
        self._call("PUT", f"/me/player/play?device_id={device}", {"context_uri": uri})
