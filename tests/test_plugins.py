"""Plugins on their own: each setup() against fresh registries and a fake integration, no App, no hardware."""
from types import SimpleNamespace as NS

import pytest

from deck.actions import Actions
from deck.keys import Do, Go, Snapshot, View
from deck.plugins import Setup, kvm, t3
from deck.plugins.kvm import KvmUi
from deck.sources.kvm import KvmState
from deck.sources.registry import Sources
from deck.sources.t3 import Environment, T3State
from deck.widgets import Ctx, Widgets


def setup_for(section=None, desktop=None):
    return Setup({}, Sources(), Actions(), Widgets(), section=section or {}, desktop=desktop)


def build(s, name, snap, options=None, slots=(0,), page="ai"):
    w = s.widgets.get(name)
    opts, problems = s.widgets.options(w, options or {})
    assert not problems
    return w.build(Ctx(View(page), snap, tuple(slots), opts, NS(name=page, color="#a78bfa"), ()))


class FakeKvm:
    def __init__(self, fail=None):
        self.side, self.sent, self.fail = "pc", [], fail

    def poll(self):
        return KvmState(self.side, self.side, "usb", True)

    def switch(self, to):
        if self.fail:
            raise RuntimeError(self.fail)
        self.sent.append(to)
        self.side = to

    def switch_usb(self, to):
        return None


def test_desk_key_toggles_from_where_the_monitors_are_now():
    s, k = setup_for(), FakeKvm()
    kvm.setup(s, kvm=k)
    s.actions.run("kvm.toggle")
    s.actions.run("kvm.toggle")
    assert k.sent == ["laptop", "pc"]


def test_a_failed_desk_switch_shows_its_error_on_the_key_for_a_while():
    s = setup_for()
    kvm.setup(s, kvm=FakeKvm(fail="DDC failed; monitor 2"))
    with pytest.raises(RuntimeError):
        s.actions.run("kvm.toggle")
    ui = s.sources.get("kvm_ui")
    assert ui == KvmUi(False, "DDC failed", True, ui.until)
    key = build(s, "kvm.switch", Snapshot(now=ui.until - 1, kvm=KvmState("pc"), kvm_ui=ui))
    assert key.tile.message == "DDC failed" and key.action == Do("kvm.toggle")
    later = build(s, "kvm.switch", Snapshot(now=ui.until + 1, kvm=KvmState("pc"), kvm_ui=ui))
    assert later.tile.message == ""


class FakeDesktop:   # the T3 part of deck.desktop.Desktop
    envs = [Environment("pc", "This PC", "u", ("t3",), primary=True)]

    def __init__(self, fail_send=False):
        self.calls, self.fail_send, self.sending = [], fail_send, lambda: None

    def poll(self):
        return T3State(envs=tuple(self.envs))

    def new_thread(self, project):
        self.calls.append(("new", project))

    def dictate(self, send=False):
        if send:
            seen = self.sending()          # what the key showed while the send was on its way
            self.calls.append(("send", seen))
            if self.fail_send:
                raise RuntimeError("T3 didn't send the dictation within 60 s")
        else:
            self.calls.append("dictate")

    def settle_thread(self, thread):
        self.calls.append(("settle", thread))

    def open_thread(self, t):
        pass

    def open_app(self):
        pass


def test_new_thread_points_dictate_at_the_draft_and_dictate_toggles():
    fake = FakeDesktop()
    s = setup_for(desktop=fake)
    t3.setup(s)
    dictate = lambda: build(s, "t3.dictate", Snapshot(now=__import__("time").time(), t3_ui=s.sources.get("t3_ui")))
    assert dictate().tile.sub == "into open thread"
    s.actions.run("t3.new_thread", "project")
    assert dictate().tile.sub == "into new thread"
    s.actions.run("t3.dictate")
    assert dictate().tile.sub == "listening… tap to send"
    fake.sending = lambda: dictate().tile.sub
    s.actions.run("t3.dictate")                       # stop: send, and the key says so until it's sent
    assert fake.calls == [("new", "project"), "dictate", ("send", "sending…")]
    assert dictate().tile.sub == "into open thread"   # the draft went out with it


def test_a_dictation_that_never_sends_fails_and_the_key_comes_back():
    fake = FakeDesktop(fail_send=True)
    s = setup_for(desktop=fake)
    t3.setup(s)
    s.actions.run("t3.dictate")
    with pytest.raises(RuntimeError, match="didn't send"):
        s.actions.run("t3.dictate")
    assert not s.sources.get("t3_ui").sending and s.sources.get("t3_ui").dictating_until == 0.0


def test_settle_goes_through_the_desktop_and_works_while_locked():
    fake = FakeDesktop()
    s = setup_for(desktop=fake)
    t3.setup(s)
    s.actions.run("t3.settle", "thread")
    assert fake.calls == [("settle", "thread")] and s.actions.usable("t3.settle", "locked")


def test_t3_actions_need_the_unlocked_desktop():
    s = setup_for(desktop=FakeDesktop())
    t3.setup(s)
    assert all(s.actions.get(n).requires == "input" for n in s.actions.names() if n != "t3.settle")
    assert not s.actions.usable("t3.dictate", "locked") and s.actions.usable("t3.dictate", "here")


def test_new_thread_without_a_project_opens_the_picker_and_comes_back():
    s = setup_for(desktop=FakeDesktop())
    t3.setup(s)
    key = build(s, "t3.new_thread", Snapshot(t3=T3State()), page="work")
    assert key.action == Go(View("t3.new_thread", back="work"))

