from deck.sources.t3 import Environment, parse_shell

ENV = Environment("pc", "This PC", "http://x", ("t3",), primary=True)
NOW = 1_800_000_000.0


def thread(id, **kw):
    base = {"id": id, "projectId": "p1", "title": id, "status": "completed", "lineage": {},
            "createdAt": "2026-11-25T10:00:00Z", "updatedAt": "2026-11-25T10:00:00Z"}
    base.update(kw)
    return base


def shell(*threads):
    return {"projects": [{"id": "p1", "title": "3d-modelling", "workspaceRoot": "C:\\nowhere\\3d"}],
            "threads": list(threads)}


def visible(*threads):
    _, ts = parse_shell(shell(*threads), ENV, "env-1", NOW)
    return {t.id: t.state for t in ts}


def test_only_live_top_level_threads_are_shown():
    assert visible(
        thread("live"),
        thread("settled", settledOverride="settled", settledAt="2026-11-25T11:00:00Z"),
        thread("reopened", settledAt="2026-11-25T11:00:00Z", unsettledAt="2026-11-25T12:00:00Z"),
        thread("subagent", lineage={"parentThreadId": "live", "relationshipToParent": "subagent"}),
        thread("archived", archivedAt="2026-11-25T11:00:00Z"),
        thread("snoozed", snoozedUntil="2099-01-01T00:00:00Z"),
    ).keys() == {"live", "reopened"}


def test_states():
    assert visible(
        thread("w", status="running", activityRunStatus="running"),
        thread("a", status="running", pendingRuntimeRequest={"kind": "approval"}),
        thread("e", status="failed", lastError="boom"),
        thread("r", status="completed"),
        thread("i", status="idle"),
    ) == {"w": "working", "a": "attention", "e": "error", "r": "ready", "i": "ready"}


def test_newest_first_and_project_attached():
    projects, ts = parse_shell(shell(thread("old", createdAt="2026-11-01T00:00:00Z"),
                                     thread("new", createdAt="2026-11-20T00:00:00Z")), ENV, "env-1", NOW)
    assert [t.id for t in ts] == ["new", "old"]
    assert ts[0].project.title == "3d-modelling" and ts[0].env_id == "env-1"
    assert projects[0].icon.startswith("mono:3M:")   # no favicon in the folder: coloured monogram


# ----- desktop actions: keys only ever go to T3 Code -----

import pytest

from deck.sources.t3 import T3

WSL = Environment("wsl", "WSL", "http://y", ("t3",))


class Desktop:
    """T3 Code's window as the actions see it: running?, in front?, and the keys pressed into it."""

    def __init__(self, running=True, focus_after=0, loses_focus_after_keys=None):
        self.running, self.focus_after, self.loses_after = running, focus_after, loses_focus_after_keys
        self.front, self.pressed, self.focus_calls = False, [], 0

    def focus(self, exe):
        self.focus_calls += 1
        if not self.running or self.focus_calls <= self.focus_after:
            return False
        self.front = True
        return True

    def in_front(self, exe):
        return self.front

    def keys(self, combo):
        assert self.front, f"{combo} pressed while T3 Code wasn't in front"
        self.pressed.append(combo)
        if self.loses_after is not None and len(self.pressed) >= self.loses_after:
            self.front = False             # e.g. a toast took the foreground


def t3(desk, envs=(ENV, WSL), app_rc=0):
    runner = lambda *a, **k: type("R", (), {"returncode": app_rc, "stderr": "", "stdout": ""})()
    return T3(list(envs), runner=runner, launcher=lambda t: None, focus=desk.focus, in_front=desk.in_front,
              keys=desk.keys, sleep=lambda s: None)


def project(env):
    from deck.sources.t3 import Project
    return Project("p", env, "e", "proj", r"C:\x", "mono:P:#fff")


def test_dictate_presses_the_shortcut_in_t3():
    desk = Desktop()
    t3(desk).dictate()
    assert desk.pressed == ["ctrl+shift+d"]


def test_dictate_refuses_when_t3_is_not_running():
    desk = Desktop(running=False)
    with pytest.raises(RuntimeError, match="isn't running"):
        t3(desk).dictate()
    assert desk.pressed == []


def test_new_thread_elsewhere_stops_when_t3_loses_focus_between_shortcuts():
    desk = Desktop(loses_focus_after_keys=1)
    with pytest.raises(RuntimeError, match="lost focus"):
        t3(desk).new_thread(project("wsl"))
    assert desk.pressed == ["ctrl+n"]       # ctrl+shift+h never reached whatever took the foreground


def test_new_thread_on_this_pc_waits_for_t3_to_come_up():
    desk = Desktop(focus_after=3)           # T3 Code was starting: its window appears on the 4th try
    t3(desk).new_thread(project("pc"))
    assert desk.front and desk.focus_calls == 4


def test_new_thread_on_this_pc_fails_if_t3_never_comes_up():
    with pytest.raises(RuntimeError, match="didn't come up"):
        t3(Desktop(running=False)).new_thread(project("pc"))


def test_poll_state_carries_the_configured_environments_even_offline():
    t = t3(Desktop())
    t._retry_at = {"pc": 9e18, "wsl": 9e18}   # both in their offline backoff: no network
    assert t.poll().envs == (ENV, WSL)
