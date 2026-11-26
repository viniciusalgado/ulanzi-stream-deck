"""Real named pipes (Windows only; no admin needed: the access list names this test's own user)."""
import os
import threading
import uuid

import pytest

from deck import winpipe
from deck.link import Peer


@pytest.fixture
def name():
    return f"ulanzi-deck-test-{uuid.uuid4().hex[:8]}"


def me():
    return winpipe.identity_of(os.getpid())


def test_a_client_is_identified_by_its_process_token_and_can_talk(name):
    got, ready = {}, threading.Event()

    def on_client(conn, ident):
        got["ident"] = ident
        got["peer"] = Peer(conn, lambda n, args: f"pong {args[0]}").start()
        ready.set()
    server = winpipe.PipeServer(name, winpipe.sddl_for(me().sid), on_client).start()
    try:
        client = Peer(winpipe.connect(name), lambda n, a: None).start()
        assert ready.wait(3)
        assert got["ident"] == me()
        assert client.call("ping", 1) == "pong 1"
        client.close()
    finally:
        server.close()


def test_a_second_server_cant_take_a_name_already_in_use(name):
    server = winpipe.PipeServer(name, winpipe.sddl_for(me().sid), lambda c, i: c.close()).start()
    try:
        with pytest.raises(OSError):
            winpipe.PipeServer(name, winpipe.sddl_for(me().sid), lambda c, i: c.close()).start()
    finally:
        server.close()


def test_users_not_on_the_access_list_cant_connect(name):
    server = winpipe.PipeServer(name, winpipe.SYSTEM_ONLY, lambda c, i: c.close()).start()
    try:
        with pytest.raises(PermissionError):
            winpipe.connect(name)
    finally:
        server.close()


def test_nothing_listening_is_an_os_error():
    with pytest.raises(OSError):
        winpipe.connect(f"ulanzi-deck-test-{uuid.uuid4().hex[:8]}")
