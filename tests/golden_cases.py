"""The views and snapshots the golden page test covers (tests/golden/pages.json).

pages.json was recorded from the hand-written pages (before layouts moved to deck.toml), so it is what "the deck
looks the same" means: the default layout in deck.example.toml must produce these tiles and these actions.
Each case's snapshot is given as the *old* Snapshot fields; tests translate them to whatever the code uses now.
"""
from deck.sources.headphones import HeadphoneState
from deck.sources.kvm import KvmState
from deck.sources.media import MediaState, Playlist
from deck.sources.printer import PrinterState
from deck.sources.t3 import Environment, Project, T3State, Thread

PC = Environment("pc", "This PC", "u", ("t3",), primary=True)
WSL = Environment("wsl", "WSL", "u2", ("t3",))
P3D = Project("p3", "pc", "e", "3d-modelling", r"C:\x\3d-modelling", "mono:3M:#fbbf24")
PX = Project("px", "pc", "e", "other", r"C:\x\other", "mono:OT:#60a5fa")
PW = Project("pw", "wsl", "e2", "wsl-proj", "/home/x", "mono:WP:#60a5fa")


def threads(n, project=PX, state="ready", start=0):
    return tuple(Thread(f"t{project.id}{i}", "e", project, f"Thread {i}", state, 900.0 - i, 100.0 - i - start)
                 for i in range(n))


T3_RICH = T3State({"pc": True, "wsl": True}, (P3D, PX, PW),
                  threads(2, P3D, "working") + threads(9, PX) + threads(1, PX, "attention", 20), (PC, WSL))
T3_EMPTY = T3State({"pc": True, "wsl": True}, (P3D, PX, PW), (), (PC, WSL))
T3_OFFLINE = T3State({"pc": False, "wsl": False}, (), (), (PC, WSL))
PLAYLISTS = (Playlist("spotify:playlist:a", "A", None), Playlist("spotify:playlist:b", "B", b"cover"),
             Playlist("spotify:playlist:c", "C", None))

BASE = dict(t3=T3_RICH, media=MediaState(True, True, "Song", "Artist", b"art", "Spotify"), playlists=PLAYLISTS,
            printer=PrinterState(online=True, state="standby"), kvm=KvmState("pc", "pc", "usb", True),
            headphones=HeadphoneState(True, True, 60), now=1000.0)

SNAPS = {
    "rich": {},
    "empty_t3": dict(t3=T3_EMPTY),
    "t3_offline": dict(t3=T3_OFFLINE),
    "nothing_playing": dict(media=MediaState(), playlists=()),
    "paused": dict(media=MediaState(True, False, "Song", "Artist")),
    "hp_off": dict(headphones=HeadphoneState(True, False, None)),
    "hp_unpaired": dict(headphones=HeadphoneState()),
    "printing": dict(printer=PrinterState(online=True, state="printing", progress=.4, filename="gears.gcode",
                                          eta=3600, nozzle=(210, 210), bed=(60, 60))),
    "print_paused": dict(printer=PrinterState(online=True, state="paused", progress=.7)),
    "print_done": dict(printer=PrinterState(online=True, state="complete", elapsed=4000)),
    "print_error": dict(printer=PrinterState(online=True, state="error", message="thermal runaway")),
    "printer_offline": dict(printer=PrinterState()),
    "camera": dict(camera=b"jpeg"),
    "kvm_busy": dict(kvm_busy=True),
    "kvm_error": dict(kvm=KvmState("laptop", "pc", "usb", True), kvm_message="DDC failed", kvm_message_error=True),
    "kvm_note": dict(kvm_message="USB didn't follow"),
    "dictating": dict(dictating=True),
    "new_draft": dict(new_draft=True),
}

VIEWS = {   # name: (page, offset, env)
    "fun": ("fun", 0, None),
    "ai": ("ai", 0, None),
    "ai_page2": ("ai", 6, None),
    "picker": ("new_thread", 0, None),
    "picker_wsl": ("new_thread", 0, "wsl"),
    "print": ("print", 0, None),
}


def snapshot_fields(name: str) -> dict:
    return {**BASE, **SNAPS[name]}


# Action names: what the old App / source methods are called now.
OLD_ACTIONS = {
    "launch": "launch", "toggle_kvm": "kvm.toggle", "camera_snapshot": "printer.camera",
    "toggle_dictation": "t3.dictate", "new_thread": "t3.new_thread",
    "reclaim_headphones": "headphones.reclaim", "reset_headphones": "headphones.reset",
    "t3.open_thread": "t3.open_thread", "t3.open_app": "t3.open_app",
    "media.prev": "media.prev", "media.next": "media.next", "media.toggle": "media.toggle",
    "media.play_playlist": "media.play_playlist",
    "printer.pause": "printer.pause", "printer.resume": "printer.resume", "printer.cancel": "printer.cancel",
    "printer.filament_change": "printer.filament_change",
}
