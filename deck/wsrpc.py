"""One request over T3 Code's WebSocket RPC (`GET /ws`), with nothing but the standard library.

    call("http://127.0.0.1:3773", token, "orchestration.dispatchCommand", {...})  -> the result value

T3's server speaks Effect RPC with JSON serialization: the client sends
`{"_tag": "Request", "id", "tag", "payload", "headers"}` and the server answers with
`{"_tag": "Exit", "requestId", "exit": {"_tag": "Success", "value"} | {"_tag": "Failure", "cause"}}`,
and may send `{"_tag": "Ping"}` (answered with Pong) meanwhile. The bearer token goes on the upgrade request.
Only plain http on loopback/LAN is supported (that's what deck.toml's environments are).

Raises RpcAuthError on 401/403 (the caller re-issues its token), RpcError for everything else.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.parse

PROTOCOL_QUERY = "orchestrationProtocol=2"


class RpcError(RuntimeError):
    pass


class RpcAuthError(RpcError):
    pass


def call(base_url: str, token: str, tag: str, payload: dict, *, timeout: float = 8.0):
    url = urllib.parse.urlsplit(base_url)
    if url.scheme != "http":
        raise RpcError(f"only http environments are supported, not {url.scheme}")
    host, port = url.hostname or "127.0.0.1", url.port or 80
    with socket.create_connection((host, port), timeout=timeout) as s:
        rest = _handshake(s, f"{host}:{port}", token)
        conn = _Conn(s, rest)
        conn.send({"_tag": "Request", "id": "1", "tag": tag, "payload": payload, "headers": []})
        while True:
            msg = conn.recv()
            kind = msg.get("_tag")
            if kind == "Ping":
                conn.send({"_tag": "Pong"})
            elif kind == "Exit" and str(msg.get("requestId")) == "1":
                exit_ = msg.get("exit") or {}
                if exit_.get("_tag") == "Success":
                    return exit_.get("value")
                raise RpcError(_failure_text(exit_))
            elif kind == "Defect":
                raise RpcError(f"server defect: {str(msg.get('defect'))[:200]}")
            elif kind == "ClientProtocolError":
                raise RpcError(f"protocol error: {str(msg.get('error'))[:200]}")


def _failure_text(exit_: dict) -> str:
    for c in exit_.get("cause") or []:
        err = c.get("error") or c.get("defect")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("_tag") or err)[:200]
        if err:
            return str(err)[:200]
    return "request failed"


def _handshake(s: socket.socket, host: str, token: str) -> bytes:
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /ws?{PROTOCOL_QUERY} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"Authorization: Bearer {token}\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise RpcError("connection closed during the WebSocket handshake")
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    status = head.split(b"\r\n", 1)[0].decode(errors="replace")
    code = status.split(" ")[1] if " " in status else ""
    if code in ("401", "403"):
        raise RpcAuthError(f"token rejected ({status})")
    if code != "101":
        raise RpcError(f"WebSocket upgrade refused: {status}")
    return rest


class _Conn:
    def __init__(self, s: socket.socket, buf: bytes):
        self._s, self._buf, self._parts = s, buf, b""

    def send(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        n = len(data)
        if n < 126:
            head = bytes([0x81, 0x80 | n])
        elif n < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self._s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> dict:
        """Next complete text message as JSON (continuation frames joined; pings answered)."""
        while True:
            fin, op, data = self._frame()
            if op == 0x9:                                   # ping -> pong
                self._control(0xA, data)
                continue
            if op == 0xA:
                continue
            if op == 0x8:
                reason = data[2:].decode(errors="replace") if len(data) > 2 else ""
                raise RpcError(f"server closed the connection {reason}".strip())
            self._parts += data
            if fin:
                text, self._parts = self._parts, b""
                return json.loads(text)

    def _control(self, op: int, data: bytes) -> None:
        mask = os.urandom(4)
        self._s.sendall(bytes([0x80 | op, 0x80 | len(data)]) + mask
                        + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _frame(self) -> tuple[bool, int, bytes]:
        self._need(2)
        b0, b1 = self._buf[0], self._buf[1]
        n, off = b1 & 0x7F, 2
        if n == 126:
            self._need(4)
            n, off = struct.unpack(">H", self._buf[2:4])[0], 4
        elif n == 127:
            self._need(10)
            n, off = struct.unpack(">Q", self._buf[2:10])[0], 10
        if b1 & 0x80:                                       # servers don't mask, but be lenient
            self._need(off + 4)
            mask, off = self._buf[off:off + 4], off + 4
        else:
            mask = None
        self._need(off + n)
        data, self._buf = self._buf[off:off + n], self._buf[off + n:]
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return bool(b0 & 0x80), b0 & 0x0F, data

    def _need(self, n: int) -> None:
        while len(self._buf) < n:
            chunk = self._s.recv(65536)
            if not chunk:
                raise RpcError("connection closed")
            self._buf += chunk
