from types import SimpleNamespace as NS

from deck import pages
from deck.pages import Do, Go, Snapshot, View
from deck.render import Blank, SwitchTile, ThreadTile, render
from deck.sources.headphones import HeadphoneState
from deck.sources.kvm import KvmState
from deck.sources.media import MediaState, Playlist
from deck.sources.printer import PrinterState
from deck.sources.t3 import Environment, Project, T3State, Thread
from deck.sources.tuya import PowerState

PC = Environment("pc", "This PC", "u", ("t3",), primary=True)
WSL = Environment("wsl", "WSL", "u2", ("t3",))
P3D = Project("p3", "pc", "e", "3d-modelling", r"C:\x\3d-modelling", "mono:3M:#fbbf24")
PX = Project("px", "pc", "e", "other", r"C:\x\other", "mono:OT:#60a5fa")
PW = Project("pw", "wsl", "e2", "wsl-proj", "/home/x", "mono:WP:#60a5fa")

CFG = {"apps": [{"name": "Dota 2", "icon": "brand:dota2", "open": "steam://rungameid/570"}],
       "project": "3d-modelling", "mainsail": "http://m/", "cura": "cura.exe", "camera_page": "http://cam"}


def svc():
    """Exactly what pages may call on the App; test_pages_bind_only_what_the_real_app_has keeps it honest."""
    return NS(t3=NS(open_thread=lambda t: None, open_app=lambda: None),
              media=NS(prev=lambda: None, next=lambda: None, toggle=lambda: None, play_playlist=lambda u: None),
              printer=NS(pause=lambda: None, resume=lambda: None, cancel=lambda: None, filament_change=lambda: None,
                         power_on=lambda: None, power_off=lambda: None),
              launch=lambda t: None, toggle_kvm=lambda: None,
              camera_snapshot=lambda: None, toggle_dictation=lambda: None, settle_thread=lambda t: None,
              new_thread=lambda p: None, reclaim_headphones=lambda: None, reset_headphones=lambda: None)


def threads(n, project=PX, state="ready"):
    return tuple(Thread(f"t{i}", "e", project, f"Thread {i}", state, 0.0, 100.0 - i) for i in range(n))


def snap(**kw):
    base = dict(t3=T3State({"pc": True, "wsl": True}, (P3D, PX, PW), (), (PC, WSL)), media=MediaState(),
                printer=PrinterState(), kvm=KvmState("pc", "pc", "usb", True), now=1000.0)
    base.update(kw)
    return Snapshot(**base)


def test_every_page_has_nav_and_desk_switch_and_renders():
    s = svc()
    for page in ("fun", "ai", "new_thread", "print"):
        keys = pages.build(View(page), snap(), s, CFG)
        assert set(keys) == set(range(14))
        assert [keys[i].action.view.page for i in (10, 11, 12)] == ["fun", "ai", "print"]
        assert isinstance(keys[13].tile, SwitchTile)
        for k in keys.values():
            assert render(k.tile)[:4] == b"\x89PNG"


def test_desk_switch_goes_to_the_other_machine():
    s = svc()
    # the target is decided by the app at press time (a stale screen must not pin it to one side)
    assert pages.build(View("ai"), snap(kvm=KvmState("pc")), s, CFG)[13].action == Do(s.toggle_kvm)
    assert pages.build(View("ai"), snap(kvm_busy=True), s, CFG)[13].action is None


def test_ai_threads_fill_slots_and_page_when_too_many():
    s = svc()
    keys = pages.build(View("ai"), snap(t3=T3State({"pc": True}, (PX,), threads(10))), s, CFG)
    assert [keys[i].tile.title for i in range(3, 9)] == [f"Thread {i}" for i in range(6)]
    more = keys[9].action
    assert isinstance(more, Go) and more.view == View("ai", offset=6)
    page2 = pages.build(more.view, snap(t3=T3State({"pc": True}, (PX,), threads(10))), s, CFG)
    assert [page2[i].tile.title for i in range(3, 7)] == ["Thread 6", "Thread 7", "Thread 8", "Thread 9"]


def test_thread_key_opens_it_and_shows_its_state():
    s = svc()
    t = threads(1, state="working")
    k = pages.build(View("ai"), snap(t3=T3State({"pc": True}, (PX,), t)), s, CFG)[3]
    assert isinstance(k.tile, ThreadTile) and k.tile.state == "working" and k.tile.project == "other"
    assert k.action.fn == s.t3.open_thread and k.action.args == (t[0],)


def test_new_thread_picker_lists_projects_per_environment():
    s = svc()
    keys = pages.build(View("new_thread"), snap(), s, CFG)
    assert [keys[i].tile.label for i in (2, 3)] == ["3d-modelling", "other"]
    assert keys[2].action.then == View("ai")
    assert keys[1].action == Go(View("new_thread", env="wsl"))
    wsl = pages.build(View("new_thread", env="wsl"), snap(), s, CFG)
    assert wsl[2].tile.label == "wsl-proj" and isinstance(wsl[3].tile, Blank)


def test_print_page_project_threads_and_guarded_cancel():
    s = svc()
    t3 = T3State({"pc": True}, (P3D, PX), threads(1, P3D) + threads(1, PX))
    idle = pages.build(View("print"), snap(t3=t3, printer=PrinterState(online=True, state="standby")), s, CFG)
    assert idle[1].tile.project == "3d-modelling" and isinstance(idle[2].tile, Blank)
    assert idle[4].tile.label == "Camera" and idle[4].action == Do(s.camera_snapshot)
    assert idle[4].long == Do(s.launch, (CFG["camera_page"],))
    assert idle[6].tile.label == "Filament Change" and idle[6].action.fn == s.printer.filament_change
    assert idle[7].action is None
    assert idle[8].tile.label == "Cancel" and idle[8].action is None
    offline = pages.build(View("print"), snap(), s, CFG)
    assert all(offline[i].action is None for i in (4, 6, 7, 8))
    done = pages.build(View("print"), snap(printer=PrinterState(online=True, state="complete")), s, CFG)
    assert done[5].tile.header == "Done" and done[5].action == Do(s.launch, (CFG["mainsail"],))
    printing = pages.build(View("print"), snap(t3=t3, printer=PrinterState(online=True, state="printing", progress=.4)),
                           s, CFG)
    assert printing[7].action.fn == s.printer.pause
    assert printing[6].action is None
    assert printing[8].action.fn == s.printer.cancel and printing[8].hold == 2.0
    assert printing[12].tile.badge == "40%"


def test_fun_page_playlists_and_now_playing():
    s = svc()
    pls = (Playlist("spotify:playlist:a", "A", None), Playlist("spotify:playlist:b", "B", None))
    keys = pages.build(View("fun"), snap(playlists=pls, media=MediaState(True, True, "Song", "Artist")), s, CFG)
    assert keys[2].action.args == ("spotify:playlist:a",) and keys[3].tile.label == "B"
    assert keys[8].action.fn == s.media.toggle and keys[8].tile.glyph == "lucide:pause"
    assert keys[0].action.args == ("steam://rungameid/570",)


def test_headphones_key_tap_reclaims_and_hold_resets():
    s = svc()
    off = pages.build(View("fun"), snap(headphones=HeadphoneState(True, False, 40)), s, CFG)[5]
    assert off.tile.sub == "tap to connect" and off.action.fn == s.reclaim_headphones
    assert off.long.fn == s.reset_headphones
    on = pages.build(View("fun"), snap(headphones=HeadphoneState(True, True, 60)), s, CFG)[5]
    assert on.tile.sub == "on this PC · 60%"


def test_dictate_points_at_the_new_draft_after_new_thread():
    s = svc()
    assert pages.build(View("ai"), snap(), s, CFG)[1].tile.sub == "into open thread"
    assert pages.build(View("ai"), snap(new_draft=True), s, CFG)[1].tile.sub == "into new thread"
    assert pages.build(View("new_thread"), snap(), s, CFG)[2].action.fn == s.new_thread


def test_desk_key_shows_where_usb_is_when_it_lags_behind():
    s = svc()
    k = pages.build(View("ai"), snap(kvm=KvmState("laptop", "pc", "usb", True)), s, CFG)[13]
    assert k.tile.active == "right" and k.tile.active_note == "USB still on PC"
    assert k.action == Do(s.toggle_kvm)


def test_new_thread_picker_without_environments_says_so():
    keys = pages.build(View("new_thread"), snap(t3=T3State()), svc(), CFG)
    assert keys[1].tile.lines == ("no environments", "in deck.toml") and isinstance(keys[2].tile, Blank)


def rich_snapshots():
    t3 = T3State({"pc": True, "wsl": True}, (P3D, PX, PW), threads(9, P3D, "working") + threads(2, PX), (PC, WSL))
    pls = (Playlist("spotify:playlist:a", "A", None),)
    playing = MediaState(True, True, "Song", "Artist")
    for printer in (PrinterState(), PrinterState(online=True, state="standby"),
                    PrinterState(online=True, state="printing", progress=.4),
                    PrinterState(online=True, state="paused"), PrinterState(online=True, state="complete")):
        yield snap(t3=t3, playlists=pls, media=playing, printer=printer, camera=b"jpeg",
                   headphones=HeadphoneState(True, False, 40))
    yield snap(printer=PrinterState(online=True, state="standby"), printer_power=PowerState.ON)
    yield snap(printer_power=PowerState.OFF)


def test_print_power_keys_follow_the_relay_and_job_state():
    s = svc()
    idle = PrinterState(online=True, state="standby")
    offline = PrinterState()
    for state, power, key7, key8 in (
        (offline, PowerState.OFF, "filament_change", "power_on"),
        (PrinterState(online=True, state="shutdown"), PowerState.OFF, "filament_change", "power_on"),
        (offline, PowerState.ON, None, None),
        (idle, PowerState.UNKNOWN, None, None),
        (idle, PowerState.ON, "filament_change", "power_off"),
        (PrinterState(online=True, state="printing"), PowerState.ON, "pause", "cancel"),
        (PrinterState(online=True, state="paused"), PowerState.ON, "resume", "cancel"),
    ):
        keys = pages.build(View("print"), snap(printer=state, printer_power=power), s, CFG)
        assert isinstance(keys[6].tile, Blank)
        assert keys[7].action == (Do(getattr(s.printer, key7)) if key7 else None)
        assert keys[8].action == (Do(getattr(s.printer, key8)) if key8 else None)
        if key8 == "power_on":
            assert keys[8].tile.label == "Power On" and keys[8].hold == 0.0
        elif key8:
            assert keys[8].hold == 2.0
            assert keys[8].long_label == ("Power Off" if key8 == "power_off" else "")


def test_pages_bind_only_what_the_real_app_has():
    """The contract between pages and the App: every action a page binds must exist on a real App (renaming an
    App method must fail here, not on the deck)."""
    from test_app import app_with
    app = app_with({})
    views = [View("fun"), View("ai"), View("ai", offset=6), View("new_thread"), View("new_thread", env="wsl"),
             View("print")]
    bound = set()
    for s in rich_snapshots():
        for view in views:
            for key in pages.build(view, s, app, CFG).values():
                for action in (key.action, key.long):
                    if isinstance(action, Do):
                        bound.add(action.fn.__qualname__)
    fake = svc()
    expected = {f"{type(owner).__qualname__}.{name}"
                for owner, names in ((app, ["launch", "toggle_kvm", "camera_snapshot", "toggle_dictation", "settle_thread",
                                            "new_thread", "reclaim_headphones", "reset_headphones"]),
                                     (app.t3, vars(fake.t3)), (app.media, vars(fake.media)),
                                     (app.printer, vars(fake.printer)))
                for name in names}
    assert bound == expected


def test_done_or_failed_thread_keys_settle_on_a_3_s_hold_and_open_on_a_tap():
    s = svc()
    for state in ("ready", "error"):
        t = threads(1, state=state)
        k = pages.build(View("ai"), snap(t3=T3State({"pc": True}, (PX,), t)), s, CFG)[3]
        assert k.action == Do(s.t3.open_thread, (t[0],))
        assert k.long == Do(s.settle_thread, (t[0],), quiet=True)
        assert k.long_s == 3.0 and k.long_label == "Settle"
    for state in ("working", "attention"):     # still going, or waiting on you: nothing to settle
        k = pages.build(View("ai"), snap(t3=T3State({"pc": True}, (PX,), threads(1, state=state))), s, CFG)[3]
        assert k.long is None and k.action.fn == s.t3.open_thread


def test_dictate_says_it_sends_and_then_that_it_is_sending():
    s = svc()
    assert pages.build(View("ai"), snap(dictating=True), s, CFG)[1].tile.sub == "listening… tap to send"
    assert pages.build(View("ai"), snap(dictating=True, dictation_sending=True), s, CFG)[1].tile.sub == "sending…"
