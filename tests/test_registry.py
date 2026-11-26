import threading

import pytest

from deck.sources.registry import Sources


class Counter:
    def __init__(self, values):
        self.values, self.calls = list(values), 0

    def poll(self):
        self.calls += 1
        v = self.values.pop(0)
        if isinstance(v, Exception):
            raise v
        return v


def test_values_start_at_initial_and_change_only_on_new_state():
    changes = []
    src = Counter(["a", "a", None, "b"])
    s = Sources(on_change=lambda: changes.append(1))
    s.add("x", src.poll, every=1.0, initial="-")
    assert s.values() == {"x": "-"}
    assert s.refresh("x") == "a" and changes == [1]
    s.refresh("x")                       # same value: no change notification
    s.refresh("x")                       # None: "nothing new", keeps the last value
    assert s.get("x") == "a" and changes == [1]
    assert s.refresh("x") == "b" and changes == [1, 1]


def test_a_failing_poll_keeps_the_last_value():
    s = Sources()
    s.add("x", Counter(["ok", RuntimeError("down")]).poll, every=1.0)
    s.refresh("x")
    assert s.refresh("x") == "ok"


def test_poll_all_polls_sources_but_not_tasks():
    ran = []
    s = Sources()
    s.add("x", lambda: 1, every=1.0)
    s.task("guard", lambda: ran.append(1), every=1.0)
    s.poll_all()
    assert s.values() == {"x": 1} and ran == []


def test_names_are_unique():
    s = Sources()
    s.add("x", lambda: 1, every=1.0)
    with pytest.raises(ValueError):
        s.task("x", lambda: None, every=1.0)


def test_started_sources_poll_on_their_own_until_stopped():
    changed = threading.Event()
    s = Sources(on_change=changed.set)
    s.add("x", lambda: "live", every=lambda: 0.01)
    s.start()
    assert changed.wait(2) and s.get("x") == "live"
    s.stop()
