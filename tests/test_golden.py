"""The default layout (deck.example.toml) draws exactly what the hand-written pages drew (tests/golden/pages.json).

Snapshots are written as the old Snapshot fields (golden_cases); the plugins' UI state replaced some of them:
kvm_busy / kvm_message -> kvm_ui, dictating / new_draft -> t3_ui, camera bytes -> a camera Still. The new-thread
picker was the page "new_thread" and is now the screen "t3.new_thread" (Back goes to the page it came from).
"""
import json
from pathlib import Path

import pytest

import golden_cases as gc
from deck import layout
from deck.catalog import registries
from deck.config import load_config
from deck.keys import Do, Go, Snapshot, View
from deck.paths import EXAMPLE_CONFIG
from deck.plugins.kvm import KvmUi
from deck.plugins.printer import Still
from deck.plugins.t3 import T3Ui

GOLDEN = json.loads((Path(__file__).parent / "golden" / "pages.json").read_text(encoding="utf-8"))
SCREENS = {"new_thread": "t3.new_thread"}


@pytest.fixture(scope="module")
def deck_layout():
    cfg = load_config(EXAMPLE_CONFIG)
    actions, widgets, _ = registries(cfg)
    return layout.parse(cfg, widgets, actions), widgets


def new_snapshot(old: dict) -> Snapshot:
    old = dict(old)
    now = old.pop("now")
    soon = now + 5
    busy, message, error = old.pop("kvm_busy", False), old.pop("kvm_message", ""), old.pop("kvm_message_error", False)
    dictating, draft = old.pop("dictating", False), old.pop("new_draft", False)
    camera = old.pop("camera", None)
    return Snapshot(now=now, **old, kvm_ui=KvmUi(busy, message, error, soon if message else 0.0),
                    t3_ui=T3Ui(soon if dictating else 0.0, soon if draft else 0.0),
                    camera=Still(camera, soon) if camera else None)


def new_view(page: str, offset: int, env) -> View:
    if page in SCREENS:
        return View(SCREENS[page], offset, env, back="ai")
    return View(page, offset, env)


def view_desc(v: View):
    page = {new: old for old, new in SCREENS.items()}.get(v.page, v.page)
    return [page, v.offset, v.env]


def action_desc(a):
    if a is None:
        return None
    if isinstance(a, Go):
        return ["go", view_desc(a.view)]
    assert isinstance(a, Do)
    return ["do", a.action, repr(a.args), view_desc(a.then) if a.then else None]


@pytest.mark.parametrize("case", sorted(GOLDEN))
def test_default_layout_matches_the_hand_written_pages(case, deck_layout):
    lay, widgets = deck_layout
    vname, sname = case.split("/")
    keys = layout.build(lay, widgets, new_view(*gc.VIEWS[vname]), new_snapshot(gc.snapshot_fields(sname)))
    got = {str(i): {"tile": repr(k.tile), "action": action_desc(k.action), "long": action_desc(k.long),
                    "hold": k.hold} for i, k in sorted(keys.items())}
    want = GOLDEN[case]
    diffs = {slot: (want[slot], got[slot]) for slot in want if want[slot] != got.get(slot)}
    assert not diffs, f"{case}: slots differ: " + json.dumps(diffs, indent=1)[:3000]
