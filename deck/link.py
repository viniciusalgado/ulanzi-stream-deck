"""Calls and notifications both ways between two deck processes over one connection.

    peer = Peer(conn, handle, on_close=None, name="agent")
    peer.start()
    peer.call("open_thread", thread, timeout=60)    # -> the other side's return value
    peer.notify("state", "t3", value)               # fire and forget, delivered in order
    peer.close()
    peer.closed                                     # True once either side closed or the connection broke

`conn` is a multiprocessing.connection.Connection (production: a named pipe from deck.winpipe; tests:
multiprocessing.Pipe()). `handle(name, args)` answers the other side: calls run on worker threads (a slow
action never holds up the stream), notifications run in order on the reader thread. Values go through
deck.wire, so only known types cross. Errors:
    call() raises RemoteError (the handler raised: its message), TimeoutError, or ConnectionError (closed).
`on_close()` runs once, on whichever thread noticed, after pending calls have failed.
"""
from __future__ import annotations

import itertools
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable

from . import wire

log = logging.getLogger(__name__)
MAX_MESSAGE = 16 * 1024 * 1024


class RemoteError(RuntimeError):
    """The other side's handler raised; the message is its error text."""


class Peer:
    def __init__(self, conn, handle: Callable[[str, tuple], object], *, on_close: Callable[[], None] | None = None,
                 name: str = "peer", workers: int = 4):
        self._conn = conn
        self._handle = handle
        self._on_close = on_close
        self._name = name
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()        # pending + closed
        self._send_lock = threading.Lock()   # one writer at a time
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"{name}-call")
        self.closed = False

    def start(self) -> "Peer":
        threading.Thread(target=self._read_loop, name=f"{self._name}-read", daemon=True).start()
        return self

    def call(self, name: str, *args, timeout: float = 30.0):
        fut: Future = Future()
        with self._lock:
            if self.closed:
                raise ConnectionError(f"{self._name} isn't connected")
            cid = next(self._ids)
            self._pending[cid] = fut
        try:
            self._send({"c": cid, "n": name, "a": args})
            return fut.result(timeout)
        except FutureTimeout:
            raise TimeoutError(f"{self._name} didn't answer {name} in {timeout:.0f} s") from None
        finally:
            with self._lock:
                self._pending.pop(cid, None)

    def notify(self, name: str, *args) -> None:
        self._send({"n": name, "a": args})

    def close(self) -> None:
        self._shutdown()

    # ----- internals -----

    def _send(self, msg: dict) -> None:
        data = wire.dumps(msg)
        try:
            with self._send_lock:
                self._conn.send_bytes(data)
        except (OSError, EOFError, ValueError) as e:
            self._shutdown()
            raise ConnectionError(f"{self._name}: connection lost ({e})") from None

    def _read_loop(self) -> None:
        try:
            while True:
                msg = wire.loads(self._conn.recv_bytes(MAX_MESSAGE))
                if not isinstance(msg, dict):
                    raise wire.WireError("message isn't a dict")
                if "r" in msg:
                    self._settle(msg)
                elif "c" in msg:
                    self._pool.submit(self._answer, msg["c"], msg.get("n"), msg.get("a", ()))
                else:
                    self._run_note(msg.get("n"), msg.get("a", ()))
        except Exception as e:  # EOF / broken pipe, garbage (WireError), or a read aborted by close() (RuntimeError)
            if not self.closed:
                log.info("%s: connection closed (%s)", self._name, str(e) or type(e).__name__)
        finally:
            self._shutdown()

    def _settle(self, msg: dict) -> None:
        with self._lock:
            fut = self._pending.get(msg["r"])
        if fut is None or fut.done():
            return
        if "e" in msg:
            fut.set_exception(RemoteError(str(msg["e"])))
        else:
            fut.set_result(msg.get("v"))

    def _answer(self, cid, name, args) -> None:
        try:
            reply = {"r": cid, "v": self._handle(str(name), tuple(args))}
        except Exception as e:  # the caller gets the message; the traceback stays in this side's log
            log.warning("%s: %s failed: %s", self._name, name, e, exc_info=not isinstance(e, (RuntimeError,
                                                                                             ConnectionError)))
            reply = {"r": cid, "e": str(e) or type(e).__name__}
        try:
            self._send(reply)
        except ConnectionError:
            pass
        except wire.WireError as e:   # the handler returned something that can't cross
            self._send({"r": cid, "e": f"bad reply: {e}"})

    def _run_note(self, name, args) -> None:
        try:
            self._handle(str(name), tuple(args))
        except Exception:
            log.exception("%s: notification %s failed", self._name, name)

    def _shutdown(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            pending, self._pending = list(self._pending.values()), {}
        try:
            self._conn.close()
        except OSError:
            pass
        for fut in pending:
            if not fut.done():
                fut.set_exception(ConnectionError(f"{self._name} disconnected"))
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                log.exception("%s: on_close failed", self._name)
