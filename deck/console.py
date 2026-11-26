"""Monitor switching (DDC/CI) for the service, done by a helper process in the console session.

Session 0, where services run, has no display, so dxva2 finds no monitors there. The service keeps one small
SYSTEM process running in whichever session owns the console (the login screen's when nobody is signed in,
the user's otherwise) and asks it over the pipe PIPE. Only SYSTEM can open that pipe, and only the process the
service started is accepted.

Service side, same interface as deck.sources.kvm.Ddc, so deck.sources.kvm.Kvm takes it unchanged:
    ddc = ConsoleDdc(spawn)                  # spawn(session, signed_in) -> winsession.Child running the helper
    ddc.start()                              # listen on the pipe
    ddc.follow(console)                      # every second: (re)start the helper when the console session
                                             # changes or the helper died
    ddc.models() / ddc.set_inputs(codes)     # wait up to CONNECT_WAIT_S for a helper; raise if there's none
    ddc.accept(conn, identity)               # what the pipe server calls; tests call it directly
    ddc.close()

Helper side:
    run_helper(ddc=None)                     # connect, warm the monitors' caps, answer until the pipe closes
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from . import winpipe
from .link import Peer
from .winsession import Console

log = logging.getLogger(__name__)
PIPE = "ulanzi-deck-console"
CONNECT_WAIT_S = 6.0
RESPAWN_S = 10.0          # a helper that keeps dying is restarted at most this often


class ConsoleDdc:
    def __init__(self, spawn: Callable[[int, bool], object], *, clock=time.monotonic):
        self._spawn = spawn
        self._clock = clock
        self._lock = threading.Condition()
        self._child = None
        self._for: tuple | None = None          # (session, user) the helper was started for
        self._last_spawn = float("-inf")
        self._peer: Peer | None = None
        self._server = None

    def start(self) -> None:
        self._server = winpipe.PipeServer(PIPE, winpipe.SYSTEM_ONLY, self.accept).start()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
        with self._lock:
            child, peer = self._child, self._peer
            self._child = self._peer = None
        if peer is not None:
            peer.close()
        if child is not None:
            child.kill()

    def follow(self, console: Console) -> None:
        if console.session is None:          # between sessions: keep what we have until the next one exists
            return
        want = (console.session, console.user)
        with self._lock:
            current = self._child if self._for == want and self._child is not None and self._child.alive() else None
            if current is not None or self._clock() - self._last_spawn < RESPAWN_S:
                return
            old, self._child, self._for = self._child, None, want
            self._last_spawn = self._clock()
        if old is not None:
            old.kill()
        try:
            child = self._spawn(console.session, bool(console.user))
        except OSError:
            log.exception("couldn't start the monitor helper in session %s", console.session)
            return
        log.info("monitor helper started in session %s (%s), pid %s", console.session,
                 console.user or "login screen", child.pid)
        with self._lock:
            self._child = child

    def accept(self, conn, identity: winpipe.Identity) -> None:
        with self._lock:
            expected = self._child.pid if self._child is not None else None
        if identity.pid != expected:
            log.warning("refused a monitor-helper connection from %s (expected pid %s)", identity, expected)
            conn.close()
            return
        this: list[Peer] = []
        peer = Peer(conn, _no_calls, on_close=lambda: self._gone(this[0]), name="monitor helper")
        this.append(peer)
        with self._lock:
            old, self._peer = self._peer, peer
            self._lock.notify_all()
        if old is not None:
            old.close()
        peer.start()

    # ----- the Ddc interface -----

    def models(self) -> list[str]:
        return self._peer_now().call("models", timeout=15)

    def set_inputs(self, codes: dict[str, int]) -> dict[str, bool]:
        return self._peer_now().call("set_inputs", dict(codes), timeout=10)

    # ----- internals -----

    def _peer_now(self) -> Peer:
        end = self._clock() + CONNECT_WAIT_S
        with self._lock:
            while self._peer is None:
                left = end - self._clock()
                if left <= 0:
                    raise ConnectionError("monitor helper isn't running")
                self._lock.wait(min(left, 0.25))
            return self._peer

    def _gone(self, peer: Peer) -> None:
        with self._lock:
            if self._peer is peer:
                self._peer = None


def _no_calls(name: str, args: tuple):
    raise RuntimeError(f"the service doesn't answer {name!r}")


# ---------------- helper side ----------------

def run_helper(ddc=None, connect=winpipe.connect) -> None:
    from .sources.kvm import Ddc
    ddc = ddc or Ddc()
    try:
        log.info("monitor helper: %s", ddc.models())    # read the caps now (3 s cold), not on the first switch
    except Exception:
        log.exception("monitor helper: couldn't read the monitors")

    def handle(name: str, args: tuple):
        if name == "models":
            return ddc.models()
        if name == "set_inputs" and len(args) == 1 and isinstance(args[0], dict):
            return ddc.set_inputs({str(k): int(v) for k, v in args[0].items()})
        raise RuntimeError(f"unknown call {name!r}")

    conn = connect(PIPE)
    done = threading.Event()
    Peer(conn, handle, on_close=done.set, name="service").start()
    done.wait()
