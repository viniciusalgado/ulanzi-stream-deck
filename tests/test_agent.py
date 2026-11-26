"""The service <-> agent protocol end to end, over a real pipe pair: RemoteDesktop on one side, the agent's
serve() with a Desktop built from fakes on the other."""
import threading
import time
from multiprocessing import Pipe
from types import SimpleNamespace as NS

import pytest

from deck.agent import RemoteDesktop, presence, serve
from deck.desktop import ACTIONS, Desktop, usable
from deck.sources.headphones import HeadphoneState
from deck.sources.media import MediaState
from deck.sources.registry import Sources
from deck.sources.t3 import Environment, T3State
from deck.winpipe import Identity
from deck.winsession import Console

SID = "S-1-5-21-1-2-3-1001"
SERVICE_ENVS = (Environment("pc", "This PC", "http://127.0.0.1:3773", (), primary=True),)
HERE = Console(1, "Vinicius", False)


def wait_for(cond, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class FakeT3:
    envs = (Environment("pc", "Agent's PC", "u", ("evil.exe",), primary=True),)

    def __init__(self):
        self.calls = []

    def poll(self):
        return T3State({"pc": True}, (), (), self.envs)

    def open_thread(self, thread):
        self.calls.append(("open_thread", thread))

    def dictate(self):
        raise RuntimeError("T3 Code isn't running")


def agent_desktop():
    t3 = FakeT3()
    media = NS(poll=lambda: MediaState(True, True, "Song"), playlists=[], playlist=None,
               toggle=lambda: t3.calls.append(("toggle",)))
    hp = NS(poll=lambda: HeadphoneState(True, True, 80))
    return Desktop(t3, media, hp, launcher=lambda target: t3.calls.append(("launch", target)),
                   presence=lambda: "here"), t3


class Session:
    """A service-side RemoteDesktop with an agent connected to it."""

    def __init__(self, console=HERE, identity=Identity(42, 1, SID)):
        self.service_sources = Sources()
        self.remote = RemoteDesktop(SERVICE_ENVS, SID)
        self.remote.attach(self.service_sources)
        self.remote.follow(console)
        self.desktop, self.t3 = agent_desktop()
        self.agent_sources = Sources()
        self.desktop.attach(self.agent_sources)
        self.agent_sources.poll_all()
        self.stop, self.changed = threading.Event(), threading.Event()
        service_end, agent_end = Pipe()
        self.agent_end = agent_end
        self.remote.accept(service_end, identity)
        self.thread = threading.Thread(target=serve, args=(self.desktop, self.agent_sources, agent_end, self.stop,
                                                           self.changed), daemon=True)
        self.thread.start()

    def value(self, name):
        return self.service_sources.get(name)

    def close(self):
        self.stop.set()
        self.changed.set()
        self.thread.join(3)


def test_presence_from_the_console_and_the_agent():
    assert presence(Console(), None) == "signed_out"
    assert presence(Console(2, "", False), None) == "signed_out"         # login screen
    assert presence(Console(1, "V", False), None) == "away"              # signed in, no agent yet
    assert presence(Console(1, "V", False), 1) == "here"
    assert presence(Console(1, "V", True), 1) == "locked"
    assert presence(Console(3, "V", False), 1) == "away"                 # an agent from an old session


def test_agent_state_reaches_the_service_with_the_services_own_environments():
    s = Session()
    try:
        assert wait_for(lambda: s.value("desktop") == "here")
        assert wait_for(lambda: s.value("media").title == "Song")
        assert s.value("headphones").battery == 80
        assert s.value("t3").online == {"pc": True}
        assert s.value("t3").envs == SERVICE_ENVS          # never the agent's (or its command line)
    finally:
        s.close()


def test_actions_run_in_the_agent_and_errors_come_back():
    s = Session()
    try:
        assert wait_for(lambda: s.value("desktop") == "here")
        s.remote.launch("steam://rungameid/570")
        s.remote.media_toggle()
        assert ("launch", "steam://rungameid/570") in s.t3.calls and ("toggle",) in s.t3.calls
        with pytest.raises(RuntimeError, match="T3 Code isn't running"):
            s.remote.dictate()
    finally:
        s.close()


def test_without_an_agent_actions_fail_and_desktop_data_goes_back_to_empty():
    s = Session()
    assert wait_for(lambda: s.value("media").title == "Song")
    s.close()                                     # the agent goes away (signed out, crashed, ...)
    assert wait_for(lambda: s.value("desktop") == "away")
    assert s.value("media") == MediaState() and s.value("t3").threads == ()
    with pytest.raises(RuntimeError, match="agent isn't running"):
        s.remote.media_toggle()


def test_signing_out_drops_the_agent_and_shows_signed_out():
    s = Session()
    try:
        assert wait_for(lambda: s.value("desktop") == "here")
        s.remote.follow(Console(2, "", False))      # the login screen got a new session
        assert wait_for(lambda: s.value("desktop") == "signed_out")
        with pytest.raises(RuntimeError, match="signed out"):
            s.remote.launch("x")
        assert wait_for(lambda: not s.thread.is_alive())
    finally:
        s.close()


def test_locked_session_reports_locked():
    s = Session(console=Console(1, "V", True))
    try:
        assert wait_for(lambda: s.value("desktop") == "locked")
    finally:
        s.close()


@pytest.mark.parametrize("identity", [Identity(42, 1, "S-1-5-21-other"), Identity(42, 7, SID)])
def test_agents_of_another_user_or_session_are_refused(identity):
    s = Session(identity=identity)
    try:
        assert not wait_for(lambda: s.value("desktop") == "here", timeout=0.5)
        with pytest.raises(RuntimeError):
            s.remote.launch("x")
    finally:
        s.close()


def test_the_agent_refuses_anything_but_desktop_actions():
    s = Session()
    try:
        assert wait_for(lambda: s.value("desktop") == "here")
        with pytest.raises(RuntimeError, match="not a desktop action"):
            s.remote._call("attach", None)
    finally:
        s.close()


def test_remote_and_local_desktops_offer_the_same_actions_with_the_same_marks():
    for name, level in ACTIONS.items():
        local, remote = getattr(Desktop, name), getattr(RemoteDesktop, name)
        assert local.requires == remote.requires == level
    assert usable(Desktop.media_toggle, "locked") and not usable(Desktop.launch, "locked")
    assert not usable(Desktop.media_toggle, "signed_out") and usable(lambda: None, "signed_out")
