import threading
from multiprocessing import Pipe

import pytest

from deck.link import Peer, RemoteError


def pair(handle_a=None, handle_b=None):
    a, b = Pipe()
    closed = {"a": threading.Event(), "b": threading.Event()}
    pa = Peer(a, handle_a or (lambda n, args: None), on_close=closed["a"].set, name="a").start()
    pb = Peer(b, handle_b or (lambda n, args: None), on_close=closed["b"].set, name="b").start()
    return pa, pb, closed


def test_call_returns_the_other_sides_result():
    pa, pb, _ = pair(handle_b=lambda name, args: (name, args))
    assert pa.call("add", 1, b"x") == ("add", (1, b"x"))
    pa.close()


def test_handler_errors_come_back_as_remote_errors():
    def boom(name, args):
        raise RuntimeError("T3 Code isn't running")
    pa, pb, _ = pair(handle_b=boom)
    with pytest.raises(RemoteError, match="T3 Code isn't running"):
        pa.call("dictate")
    pa.close()


def test_notifications_arrive_in_order():
    got, done = [], threading.Event()

    def handle(name, args):
        got.append(args[0])
        if len(got) == 50:
            done.set()
    pa, pb, _ = pair(handle_b=handle)
    for i in range(50):
        pa.notify("state", i)
    assert done.wait(3) and got == list(range(50))
    pa.close()


def test_a_slow_call_doesnt_block_other_messages():
    gate = threading.Event()

    def handle(name, args):
        if name == "slow":
            gate.wait(3)
        return name
    pa, pb, _ = pair(handle_b=handle)
    result = {}
    t = threading.Thread(target=lambda: result.update(slow=pa.call("slow")))
    t.start()
    assert pa.call("fast") == "fast"
    gate.set()
    t.join(3)
    assert result == {"slow": "slow"}
    pa.close()


def test_closing_fails_pending_calls_and_tells_both_sides():
    gate = threading.Event()
    pa, pb, closed = pair(handle_b=lambda n, a: gate.wait(5))
    err = {}

    def waiter():
        try:
            pa.call("hang", timeout=5)
        except Exception as e:
            err["e"] = e
    t = threading.Thread(target=waiter)
    t.start()
    pb.close()
    t.join(3)
    gate.set()
    assert isinstance(err.get("e"), ConnectionError)
    assert closed["a"].wait(3) and closed["b"].wait(3)
    with pytest.raises(ConnectionError):
        pa.call("again")


def test_call_times_out():
    gate = threading.Event()
    pa, pb, _ = pair(handle_b=lambda n, a: gate.wait(3))
    with pytest.raises(TimeoutError):
        pa.call("hang", timeout=0.2)
    gate.set()
    pa.close()


def test_garbage_on_the_wire_closes_the_connection():
    a, b = Pipe()
    closed = threading.Event()
    Peer(a, lambda n, args: None, on_close=closed.set).start()
    b.send_bytes(b'{"$o":"Popen","f":{}}')
    assert closed.wait(3)
