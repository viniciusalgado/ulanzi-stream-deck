"""The service <-> agent protocol, both ends: the deck's desktop features across the session boundary.

The service (SYSTEM, always running) owns the deck; the agent runs in the signed-in user's session and owns
the desktop (deck.desktop.Desktop). They talk over the pipe PIPE with deck.link.Peer:

    agent -> service   call  hello(PROTOCOL)            once, right after connecting
                       note  state(name, value)          a DATA value changed (all of them after hello)
    service -> agent   call  <action>(*args)             one of desktop.ACTIONS; the reply is its result/error

Service side:
    remote = RemoteDesktop(envs, user_sid)       # same interface as Desktop: attach(), every action
    remote.attach(sources)                        # DATA (pushed by the agent) + "desktop" (presence)
    remote.start()                                # listen on the pipe
    remote.follow(console)                        # winsession.Console, every second: who is at the PC
    remote.accept(conn, identity)                 # what the pipe server calls; tests call it directly
    remote.close()
An agent is only accepted if it runs as the deployed user, in the console session. A newer agent replaces an
older one. Without an agent every action raises (the key flashes red) and DATA goes back to its initial values,
so pages never show stale threads or music from a session that's gone.

Agent side:
    serve(desktop, sources, conn, stop)           # one connection: push DATA, run actions; returns when it ends
    run_agent(cfg, stop)                          # the agent process: connect, serve, reconnect forever
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import replace

from . import winpipe
from .desktop import ACTIONS, DATA, Desktop, initial_values, requires
from .link import Peer
from .sources.registry import Sources
from .winsession import Console

log = logging.getLogger(__name__)
PIPE = "ulanzi-deck-agent"
PROTOCOL = 1
CALL_TIMEOUT_S = 90       # resetting the headphones or starting a playlist can take a while


def presence(console: Console | None, agent_session: int | None) -> str:
    if console is None or console.session is None or not console.user:
        return "signed_out"
    if agent_session != console.session:
        return "away"
    return "locked" if console.locked else "here"


class RemoteDesktop:
    def __init__(self, envs, user_sid: str):
        self._envs = tuple(envs)
        self._user_sid = user_sid
        self._lock = threading.Lock()
        self._peer: Peer | None = None
        self._peer_session: int | None = None
        self._ready = False                     # the current peer said hello
        self._console: Console | None = None
        self._sources: Sources | None = None
        self._server = None

    def attach(self, sources: Sources) -> None:
        for name, value in initial_values(self._envs).items():
            sources.add(name, None, initial=value)
        sources.add("desktop", None, initial="signed_out")
        self._sources = sources

    def start(self) -> None:
        self._server = winpipe.PipeServer(PIPE, winpipe.sddl_for(self._user_sid), self.accept).start()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
        with self._lock:
            peer = self._peer
        if peer is not None:
            peer.close()

    def follow(self, console: Console) -> None:
        with self._lock:
            self._console = console
            stale = self._peer if self._peer is not None and self._peer_session != console.session else None
        if stale is not None:
            log.info("agent belongs to session %s, but the console is now %s: dropping it",
                     self._peer_session, console.session)
            stale.close()
        self._update_presence()

    def accept(self, conn, identity: winpipe.Identity) -> None:
        with self._lock:
            console = self._console
        if identity.sid != self._user_sid or console is None or identity.session != console.session:
            log.warning("refused an agent connection from %s (expected user %s in console session %s)",
                        identity, self._user_sid, getattr(console, "session", None))
            conn.close()
            return
        this: list[Peer] = []
        peer = Peer(conn, self._handle_factory(), on_close=lambda: self._gone(this[0]), name="agent")
        this.append(peer)
        with self._lock:
            old, self._peer, self._peer_session, self._ready = self._peer, peer, identity.session, False
        if old is not None:
            old.close()
        log.info("agent connected (pid %s, session %s)", identity.pid, identity.session)
        peer.start()

    # ----- actions: one forwarding method per desktop action -----

    def _call(self, name: str, *args):
        with self._lock:
            peer = self._peer if self._ready else None
        if peer is None:
            raise RuntimeError("signed out" if presence(self._console, None) == "signed_out"
                               else "deck agent isn't running")
        return peer.call(name, *args, timeout=CALL_TIMEOUT_S)

    # ----- internals -----

    def _handle_factory(self):
        def handle(name: str, args: tuple):
            if name == "hello":
                if args[:1] != (PROTOCOL,):
                    raise RuntimeError(f"protocol {args[:1]} != {PROTOCOL}: deploy the agent and service together")
                with self._lock:
                    self._ready = True
                self._update_presence()
                return PROTOCOL
            if name == "state" and len(args) == 2 and args[0] in DATA:
                value = args[1]
                if args[0] == "t3":   # environments come from the service's own deck.toml, never the agent's
                    value = replace(value, envs=self._envs)
                self._sources.put(args[0], value)
                return None
            raise RuntimeError(f"unknown message {name!r}")
        return handle

    def _gone(self, peer: Peer) -> None:
        with self._lock:
            if self._peer is not peer:
                return
            self._peer, self._peer_session, self._ready = None, None, False
        log.info("agent disconnected")
        for name, value in initial_values(self._envs).items():
            self._sources.put(name, value)
        self._update_presence()

    def _update_presence(self) -> None:
        with self._lock:
            agent = self._peer_session if self._ready else None
            console = self._console
        if self._sources is not None:
            self._sources.put("desktop", presence(console, agent))


def _forwarder(name: str):
    def forward(self, *args):
        return self._call(name, *args)
    forward.__name__ = name
    forward.__qualname__ = f"RemoteDesktop.{name}"
    return requires(ACTIONS[name])(forward)


for _name in ACTIONS:
    setattr(RemoteDesktop, _name, _forwarder(_name))


# ---------------- agent side ----------------

def serve(desktop: Desktop, sources: Sources, conn, stop: threading.Event, changed: threading.Event) -> None:
    """One connection: say hello, push every DATA value (then each change), run the service's calls."""
    def handle(name: str, args: tuple):
        if name not in ACTIONS:
            raise RuntimeError(f"not a desktop action: {name!r}")
        return getattr(desktop, name)(*args)

    peer = Peer(conn, handle, name="service").start()
    try:
        peer.call("hello", PROTOCOL, timeout=10)
        sent: dict[str, object] = {}
        while not stop.is_set() and not peer.closed:
            values = sources.values()
            for name in DATA:
                if name not in sent or values[name] is not sent[name]:
                    peer.notify("state", name, values[name])
                    sent[name] = values[name]
            changed.wait(1.0)
            changed.clear()
    except ConnectionError as e:
        log.info("service connection ended: %s", e)
    finally:
        peer.close()


def run_agent(cfg: dict, stop: threading.Event) -> None:
    changed = threading.Event()
    sources = Sources(on_change=changed.set)
    desktop = Desktop.from_config(cfg)
    desktop.attach(sources)
    sources.start()
    delay, waited = 0.5, False
    log.info("deck agent running (pid %s)", os.getpid())
    try:
        while not stop.is_set():
            try:
                conn = winpipe.connect(PIPE)
            except OSError as e:
                if not waited:
                    log.info("waiting for the deck service (%s)", e)
                    waited = True
                stop.wait(delay)
                delay = min(delay * 2, 5.0)
                continue
            log.info("connected to the deck service")
            delay, waited = 0.5, False
            started = time.monotonic()
            serve(desktop, sources, conn, stop, changed)
            if time.monotonic() - started < 2:   # refused or dropped at once: don't hammer the service
                stop.wait(5)
    finally:
        sources.stop()
