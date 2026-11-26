"""T3 Code: live threads, new threads, dictation. On when deck.toml has [t3] (see deck.sources.t3 for the API).

    [t3]
    [[t3.environments]]          # one per T3 Code server; see deck.example.toml
    key = "pc"  name = "This PC"  url = "http://127.0.0.1:3773"  cli = [...]  primary = true

Sources: t3 (T3State, polled by the desktop: deck.desktop), t3_ui (dictation / new-draft timers).
Actions: t3.open_thread(thread), t3.open_app, t3.new_thread(project), t3.dictate
Widgets:
    t3.open_app        bring T3 Code forward (or start it)
    t3.dictate         toggle dictation; says where it will type (open thread / the new draft)
    t3.new_thread      with `project`: a new draft there; without: opens the project picker screen
    t3.threads         live threads, newest first, paging when they don't fit; `project` limits them to one
Screen: t3.new_thread (environment + project picker). Badge: t3 (threads needing you, else working).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from ..keys import Do, Go, Key, View, age, offline, paged
from ..render import AMBER, BLUE, GREEN, MUTED, RED, AppTile, InfoTile, ThreadTile
from ..sources.t3 import T3State
from ..widgets import Option

NAME = "t3"
SECTION = "t3"
SCREEN = "t3.new_thread"
DRAFT_S = 180         # after "New thread", Dictate says it will type into that new draft for this long
DICTATE_S = 120       # dictation is assumed to stop by itself after this long


@dataclass(frozen=True)
class T3Ui:
    dictating_until: float = 0.0
    draft_until: float = 0.0


def setup(s) -> None:
    s.sources.add("t3_ui", None, initial=T3Ui())
    s.lively(lambda snap: any(t.state == "working" for t in getattr(snap.t3, "threads", ())))

    def ui() -> T3Ui:
        return s.sources.get("t3_ui")

    def new_thread(project) -> None:
        s.desktop.new_thread(project)
        s.sources.put("t3_ui", T3Ui(0.0, time.time() + DRAFT_S))

    def dictate() -> None:
        s.desktop.dictate()
        now = time.time()
        s.sources.put("t3_ui", replace(ui(), dictating_until=0.0 if ui().dictating_until > now else now + DICTATE_S))

    s.actions.add("t3.open_thread", lambda thread: s.desktop.open_thread(thread), requires="input",
                  doc="open a thread in T3 Code")
    s.actions.add("t3.open_app", lambda: s.desktop.open_app(), requires="input",
                  doc="bring T3 Code forward, or start it")
    s.actions.add("t3.new_thread", new_thread, requires="input",
                  doc="open a new draft in a project; the next Dictate types into it")
    s.actions.add("t3.dictate", dictate, requires="input", doc="toggle T3 Code's dictation in the open thread")

    w = s.widgets
    project_opt = Option("project", str, "", "a project's title or folder name; empty: any project")

    @w.widget("t3.open_app", doc="bring T3 Code forward, or start it")
    def open_app(ctx):
        return Key(AppTile("app:t3code", "T3 Code", icon_scale=0.5), Do("t3.open_app"))

    @w.widget("t3.dictate", needs=("t3_ui",), doc="toggle dictation; shows where it will type")
    def dictate_key(ctx):
        u, now, color = ctx.snap.t3_ui or T3Ui(), ctx.snap.now, ctx.color
        if u.dictating_until > now:
            tile = AppTile("lucide:mic", "Dictate", sub="listening… tap to stop", color=RED, accent=RED)
        elif u.draft_until > now:
            tile = AppTile("lucide:mic", "Dictate", sub="into new thread", color=color, accent=color)
        else:
            tile = AppTile("lucide:mic", "Dictate", sub="into open thread")
        return Key(tile, Do("t3.dictate"))

    @w.widget("t3.new_thread", needs=("t3",), options=(project_opt,),
              doc="a new draft in `project`, or (without one) the project picker")
    def new_thread_key(ctx):
        want = ctx.options["project"]
        if not want:
            return Key(AppTile("lucide:square-plus", "New thread", sub="pick project", color=ctx.color),
                       Go(View(SCREEN, back=ctx.page.name)))
        project = _project(ctx.snap.t3, want)
        if project is None:
            return Key(AppTile("lucide:square-plus", "New thread", sub=f"no project '{want}'", dim=True))
        return Key(AppTile("lucide:square-plus", "New thread", sub=project.title, color=ctx.color),
                   Do("t3.new_thread", (project,)))

    @w.widget("t3.threads", slots="many", needs=("t3",),
              options=(project_opt, Option("empty_header", str, "Threads", "title when there are none"),
                       Option("empty_lines", list, ["No active threads", "settled ones stay hidden"],
                              "lines when there are none")),
              doc="live threads, newest first; the last slot pages when they don't fit")
    def threads(ctx):
        state, want = ctx.snap.t3 or T3State(), ctx.options["project"]
        project = _project(state, want) if want else None
        if want and project is None:
            return {}
        primary = next((e for e in state.envs if e.primary), None)
        if primary is not None and not state.online.get(primary.key, False):
            return {ctx.slots[0]: offline("T3 Code")}
        items = [t for t in state.threads if project is None or t.project.id == project.id]
        if not items:
            return {ctx.slots[0]: Key(InfoTile(ctx.options["empty_header"], "", tuple(ctx.options["empty_lines"]),
                                               accent=GREEN, icon="lucide:circle-check"))}
        return paged(items, ctx.slots, ctx.view, lambda t: _thread_key(t, ctx.snap.now),
                     lambda o: View(ctx.view.page, offset=o, back=ctx.view.back))

    def picker(ctx):
        state = ctx.snap.t3 or T3State()
        back_page = ctx.page.name if ctx.page is not None else None
        back = Key(AppTile("lucide:arrow-left", "Back", color=MUTED), Go(View(back_page)))
        envs = [e for e in state.envs if state.online.get(e.key, False)] or list(state.envs[:1])
        if not envs:
            return {0: back, 1: Key(InfoTile("T3 Code", "", ("no environments", "in deck.toml"), accent=MUTED,
                                             icon="lucide:server"))}
        view = ctx.view
        env = next((e for e in envs if e.key == view.env), envs[0])
        nxt = envs[(envs.index(env) + 1) % len(envs)]
        keys = {
            0: back,
            1: Key(AppTile("lucide:server", env.name, sub="environment" if len(envs) > 1 else "only one online",
                           color=ctx.color, accent=ctx.color),
                   Go(View(SCREEN, env=nxt.key, back=view.back)) if len(envs) > 1 else None),
        }
        projects = [p for p in state.projects if p.env == env.key]
        if not projects:
            keys[2] = Key(InfoTile("Projects", "", ("none in this", "environment"), accent=MUTED,
                                   icon="lucide:folder-git-2"))
            return keys

        def project_key(p):
            return Key(AppTile(p.icon, p.title, sub=env.name, icon_scale=0.42),
                       Do("t3.new_thread", (p,), then=View(back_page)))

        keys.update(paged(projects, [2, 3, 4, 5, 6, 7, 8, 9], view, project_key,
                          lambda o: View(SCREEN, offset=o, env=env.key, back=view.back)))
        return keys

    w.screen(SCREEN, picker)
    w.badge("t3", _badge)


def _badge(snap):
    threads = getattr(snap.t3, "threads", ())
    need = sum(t.state in ("attention", "error") for t in threads)
    working = sum(t.state == "working" for t in threads)
    if need:
        return str(need), AMBER
    if working:
        return str(working), BLUE
    return None


def _project(state: T3State, want: str):
    want = want.lower()
    return next((p for p in state.projects
                 if p.title.lower() == want or p.root.replace("\\", "/").rstrip("/").lower().endswith("/" + want)),
                None)


def _thread_key(t, now: float) -> Key:
    status = {"working": f"Working {age(now - t.since)}", "attention": "Needs you", "error": "Failed",
              "ready": f"Ready · {age(now - t.since)}"}[t.state]
    return Key(ThreadTile(t.title, t.project.title, t.project.icon, t.state, status,
                          phase=int(now) if t.state == "working" else 0),
               Do("t3.open_thread", (t,)))
