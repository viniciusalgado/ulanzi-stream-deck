"""deck.layout: reading pages from deck.toml (every problem at once), and building a view from widgets."""
import copy

import pytest

from deck import layout
from deck.actions import Actions
from deck.config import load_config
from deck.keys import Do, Go, Key, Snapshot, View
from deck.paths import EXAMPLE_CONFIG
from deck.render import AppTile, Blank
from deck.widgets import Option, Widgets


def registries():
    actions, widgets = Actions(), Widgets()
    actions.add("say", lambda text: text, requires="input")
    actions.add("beep", lambda: None)

    @widgets.widget("label", options=(Option("text", str), Option("size", int, 1)))
    def label(ctx):
        return Key(AppTile(None, ctx.options["text"], sub=ctx.page.name), Do("say", (ctx.options["text"],)))

    @widgets.widget("row", slots="many")
    def row(ctx):
        return {s: Key(AppTile(None, f"row {s}")) for s in ctx.slots}

    @widgets.widget("nav", slots="many")
    def nav(ctx):
        return {s: Key(AppTile(None, p.name, accent="on" if p.name == ctx.page.name else None), Go(View(p.name)))
                for s, p in zip(ctx.slots, ctx.pages)}

    widgets.screen("picker", lambda ctx: {0: Key(AppTile(None, "back"), Go(View(ctx.page.name)))})
    widgets.badge("count", lambda snap: ("3", "#fff"))
    return actions, widgets


def cfg(**over):
    c = {"deck": {"start_page": "one"},
         "pages": [{"name": "one", "badge": "count", "keys": [{"slot": 0, "widget": "label", "text": "hi"},
                                                              {"slots": [1, 2], "widget": "row"}]},
                   {"name": "two", "keys": [{"slot": 0, "widget": "label", "text": "two", "tap": "beep",
                                             "long": ["say", "long"]}]}],
         "bottom": [{"slots": [10, 11], "widget": "nav"}]}
    c.update(over)
    return c


def parse(c):
    actions, widgets = registries()
    return layout.parse(c, widgets, actions), widgets, actions


def test_a_view_is_its_page_plus_the_bottom_row_and_blank_elsewhere():
    lay, widgets, _ = parse(cfg())
    keys = layout.build(lay, widgets, View("one"), Snapshot())
    assert keys[0] == Key(AppTile(None, "hi", sub="one"), Do("say", ("hi",)))
    assert [keys[i].tile.label for i in (1, 2)] == ["row 1", "row 2"]
    assert keys[10].tile.accent == "on" and keys[11].action == Go(View("two"))
    assert isinstance(keys[5].tile, Blank) and set(keys) == set(range(14))


def test_tap_and_long_replace_what_a_key_does():
    lay, widgets, _ = parse(cfg())
    k = layout.build(lay, widgets, View("two"), Snapshot())[0]
    assert k.action == Do("beep") and k.long == Do("say", ("long",))


def test_a_screen_keeps_the_bottom_row_of_the_page_it_came_from():
    lay, widgets, _ = parse(cfg())
    keys = layout.build(lay, widgets, View("picker", back="two"), Snapshot())
    assert keys[0].action == Go(View("two")) and keys[11].tile.accent == "on"


def test_keys_the_session_doesnt_allow_are_dimmed_and_do_nothing():
    lay, widgets, actions = parse(cfg())
    locked = layout.build(lay, widgets, View("one"), Snapshot(desktop="locked"), actions)[0]
    assert locked.action is None and locked.tile.dim
    here = layout.build(lay, widgets, View("one"), Snapshot(desktop="here"), actions)[0]
    assert here.action == Do("say", ("hi",)) and not here.tile.dim


def test_every_problem_is_reported_at_once():
    c = cfg(pages=[{"name": "one", "color": "#fff", "oops": 1, "badge": "nope", "keys": [
        {"slot": 0, "widget": "label"},                          # missing option
        {"slot": 0, "widget": "row"},                            # slot used twice
        {"slot": 1, "widget": "label", "text": 3, "size": "x"},  # wrong types
        {"slot": 2, "widget": "ghost"},                          # unknown widget
        {"slot": 14, "widget": "label", "text": "a"},            # no such slot
        {"slots": [3, 4], "widget": "label", "text": "a"},       # one-slot widget on two
        {"slot": 5, "widget": "label", "text": "a", "tap": "fly", "colour": "red"},
    ]}], deck={"start_page": "nowhere"})
    with pytest.raises(layout.LayoutError) as e:
        parse(c)
    text = "\n".join(e.value.problems)
    for expected in ("unknown settings ['oops']", "unknown badge 'nope'", "missing option 'text'",
                     "slot 0 used by both", "option 'text' should be text", "option 'size' should be integer",
                     "unknown widget 'ghost'", "slots are 0-13", "takes exactly one slot", "unknown action 'fly'",
                     "unknown option 'colour'", "start_page 'nowhere'"):
        assert expected in text, expected


def test_old_style_config_says_how_to_move_on():
    with pytest.raises(layout.LayoutError, match="deck.example.toml"):
        parse(cfg(pages={"fun": {"apps": []}}))


def test_options_get_their_defaults():
    lay, _, _ = parse(cfg())
    assert lay.page("one").placements[0].options == {"text": "hi", "size": 1}


def test_the_example_layout_only_binds_registered_actions():
    """Every action any key of the default layout can bind exists, whatever the snapshot (contract with plugins)."""
    import golden_cases as gc
    from deck.catalog import registries as real
    from test_golden import new_snapshot, new_view
    c = load_config(EXAMPLE_CONFIG)
    actions, widgets, _ = real(c)
    lay = layout.parse(c, widgets, actions)
    bound = set()
    for view in gc.VIEWS.values():
        for name in gc.SNAPS:
            for k in layout.build(lay, widgets, new_view(*view), new_snapshot(gc.snapshot_fields(name))).values():
                bound |= {a.action for a in (k.action, k.long) if isinstance(a, Do)}
    assert bound and bound <= set(actions.names())
    assert set(gc.OLD_ACTIONS.values()) <= bound      # and every action the old pages had is still reachable


def test_turning_a_plugin_off_is_deleting_its_section_and_keys():
    from deck.catalog import check
    c = copy.deepcopy(load_config(EXAMPLE_CONFIG))
    del c["printer"]
    ok, text = check(c)
    assert not ok and "unknown widget 'printer.camera'" in text
    c["pages"] = [p for p in c["pages"] if p["name"] not in ("print", "signed_out")]
    del c["deck"]["signed_out_page"]
    c["pages"][0]["keys"] = [k for k in c["pages"][0]["keys"] if not k["widget"].startswith("printer")]
    ok, text = check(c)
    assert ok, text
    assert "printer" not in text.split("plugins:")[1]


# ----- who is at the PC (deck.desktop presence; the deck service) -----

def example(presence, view, **fields):
    import golden_cases as gc
    from deck.catalog import registries as real
    from test_golden import new_snapshot
    c = load_config(EXAMPLE_CONFIG)
    actions, widgets, _ = real(c)
    lay = layout.parse(c, widgets, actions)
    snap = new_snapshot({**gc.snapshot_fields("rich"), **fields})
    snap.desktop = presence
    return layout.build(lay, widgets, view, snap, actions), actions


def test_signed_out_shows_only_what_works_without_a_session():
    from deck.render import InfoTile
    from deck.sources.printer import PrinterState
    printing = PrinterState(online=True, state="printing", progress=.4)
    for view in (View("fun"), View("ai"), View("print"), View("t3.new_thread", back="ai")):
        keys, actions = example("signed_out", view, printer=printing)
        assert keys[0].tile.value == "Signed out" and keys[1].tile.header == "Clock"
        assert all(isinstance(keys[i].tile, Blank) for i in (2, 3, 9))
        assert keys[4].action == Do("printer.camera") and keys[4].long is None    # still yes, stream no
        assert keys[5].tile.header == "Printing" and keys[5].action is None        # status yes, Mainsail no
        assert keys[7].action == Do("printer.pause") and keys[8].action == Do("printer.cancel")
        assert keys[8].hold == 2.0 and keys[6].tile.label == "Filament Change"
        assert keys[10].action is None and keys[11].action is None and keys[10].tile.dim
        assert keys[10].tile.sub == "sign in"
        assert keys[12].action == Go(View("print")) and keys[12].tile.accent      # 3D Print stays lit
        assert keys[13].action == Do("kvm.toggle")
        for k in keys.values():
            for a in (k.action, k.long):
                assert not isinstance(a, Do) or actions.get(a.action).requires is None


def test_locked_keeps_music_and_disables_what_needs_the_desktop():
    fun, _ = example("locked", View("fun"))
    assert fun[8].action == Do("media.toggle") and fun[9].action == Do("media.next")
    assert fun[0].action is None and fun[0].tile.dim                  # launching Dota 2
    assert fun[2].action is None                                      # starting a playlist clicks in Spotify
    assert fun[5].action == Do("headphones.reclaim")                  # headphones work while locked
    ai, _ = example("locked", View("ai"))
    assert ai[1].action is None and ai[3].action is None              # dictate, open a thread
    assert ai[2].action == Go(View("t3.new_thread", back="ai"))       # browsing pages still works
    away, _ = example("away", View("fun"))
    assert away[8].action is None and away[13].action == Do("kvm.toggle")
    here, _ = example("here", View("fun"))
    assert here[0].action == Do("launch", ("steam://rungameid/570",))


def test_done_or_failed_thread_keys_settle_on_a_3_s_hold_and_open_on_a_tap():
    from deck.sources.t3 import T3State
    import golden_cases as gc
    for state in ("ready", "error"):
        t = gc.threads(1, state=state)
        k = example("here", View("ai"), t3=T3State({"pc": True}, (gc.PX,), t, (gc.PC,)))[0][3]
        assert k.action == Do("t3.open_thread", (t[0],))
        assert k.long == Do("t3.settle", (t[0],), quiet=True) and k.long_s == 3.0 and k.long_label == "Settle"
    for state in ("working", "attention"):     # still going, or waiting on you: nothing to settle
        t = gc.threads(1, state=state)
        k = example("here", View("ai"), t3=T3State({"pc": True}, (gc.PX,), t, (gc.PC,)))[0][3]
        assert k.long is None and k.action == Do("t3.open_thread", (t[0],))
