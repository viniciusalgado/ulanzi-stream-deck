"""T3 Code: which threads are live, and the actions the deck offers on them.

Interface:
    t3 = T3(environments)           # from deck.toml [[t3.environments]]
    state = t3.poll()               # T3State: environments, projects, visible threads (never raises)
    t3.open_thread(thread) / t3.new_thread(project) / t3.open_app() / t3.dictate()

Reads come from each environment's local HTTP API (`GET /api/orchestration/shell`, one ~40 ms call that
returns every thread with its run status). Each environment gets its own read-only bearer token, issued once
with that environment's `t3 auth session issue` and re-issued automatically when it expires.
Writes go through the desktop app the user sees: `t3code://threads/...` deep links to open a thread and
`t3 app <folder>` to start a new thread draft in a project, so nothing runs without the user pressing Send.

A thread is shown when it is a top-level thread that isn't settled, archived or snoozed.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import icons, winutil
from ..paths import STATE_DIR

log = logging.getLogger(__name__)
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROTOCOL_HEADER = "x-t3-orchestration-protocol"
WORKING_STATUSES = {"running", "starting", "queued", "pending", "waking"}
ERROR_STATUSES = {"failed", "error", "interrupted"}
TOKEN_ARGS = ["auth", "session", "issue", "--scope", "orchestration:read", "--ttl", "365d",
              "--label", "Ulanzi deck (read-only)", "--token-only"]
T3_EXE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\t3code\T3 Code (Beta).exe")   # its default install


@dataclass(frozen=True)
class Environment:
    key: str            # short id from deck.toml, used for the token file
    name: str           # what the deck shows
    url: str
    cli: tuple[str, ...]  # command prefix that runs this environment's t3 CLI
    primary: bool = False


@dataclass(frozen=True)
class Project:
    id: str
    env: str            # Environment.key
    env_id: str         # T3 environment id (for deep links)
    title: str
    root: str
    icon: str           # icon ref (see deck.icons)
    last_active: float = 0.0


@dataclass(frozen=True)
class Thread:
    id: str
    env_id: str
    project: Project
    title: str
    state: str          # working | attention | error | ready
    since: float        # epoch seconds the current state started (for "3m ago")
    created: float


@dataclass(frozen=True)
class T3State:
    online: dict[str, bool] = field(default_factory=dict)   # env key -> reachable
    projects: tuple[Project, ...] = ()
    threads: tuple[Thread, ...] = ()
    envs: tuple[Environment, ...] = ()                      # as configured, online or not


def _ts(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def is_visible(t: dict, now: float) -> bool:
    lineage = t.get("lineage") or {}
    if lineage.get("parentThreadId") or lineage.get("relationshipToParent"):
        return False                     # subagents and side chats belong to their parent
    if t.get("archivedAt") or t.get("deletedAt"):
        return False
    if _ts(t.get("snoozedUntil")) > now:
        return False
    override = t.get("settledOverride")
    if override == "unsettled":
        return True
    settled_at, unsettled_at = _ts(t.get("settledAt")), _ts(t.get("unsettledAt"))
    if override == "settled" or settled_at:
        return bool(unsettled_at and unsettled_at > settled_at)
    return True


def thread_state(t: dict) -> tuple[str, float]:
    """(state, since) for a raw shell thread."""
    status = (t.get("status") or "").lower()
    activity = (t.get("activityRunStatus") or "").lower()
    if t.get("pendingRuntimeRequest") or t.get("hasActionableProposedPlan"):
        return "attention", _ts(t.get("updatedAt"))
    if status in WORKING_STATUSES or activity in WORKING_STATUSES:
        return "working", _ts(t.get("activityRunStartedAt") or t.get("latestRunStartedAt")
                              or t.get("latestRunRequestedAt") or t.get("updatedAt"))
    if status in ERROR_STATUSES or (t.get("lastError") and status not in ("completed", "idle")):
        return "error", _ts(t.get("latestRunCompletedAt") or t.get("updatedAt"))
    return "ready", _ts(t.get("latestRunCompletedAt") or t.get("updatedAt"))


def parse_shell(shell: dict, env: Environment, env_id: str, now: float | None = None) -> tuple[list[Project], list[Thread]]:
    now = time.time() if now is None else now
    activity: dict[str, float] = {}
    for t in shell.get("threads", []):
        activity[t["projectId"]] = max(activity.get(t["projectId"], 0.0), _ts(t.get("updatedAt")))
    projects = {}
    for p in shell.get("projects", []):
        if p.get("archivedAt") or p.get("deletedAt"):
            continue
        projects[p["id"]] = Project(
            id=p["id"], env=env.key, env_id=env_id, title=p.get("title") or Path(p["workspaceRoot"]).name,
            root=p.get("workspaceRoot") or "",
            icon=icons.project_icon_ref(p["id"], p.get("title") or "", p.get("workspaceRoot") if env.primary else None,
                                        p.get("projectIcon"), p.get("faviconPath")),
            last_active=activity.get(p["id"], _ts(p.get("updatedAt"))))
    threads = []
    for t in shell.get("threads", []):
        proj = projects.get(t["projectId"])
        if proj is None or not is_visible(t, now):
            continue
        state, since = thread_state(t)
        threads.append(Thread(id=t["id"], env_id=env_id, project=proj, title=t.get("title") or "New thread",
                              state=state, since=since, created=_ts(t.get("createdAt"))))
    threads.sort(key=lambda th: th.created, reverse=True)   # newest first; order doesn't jump on status changes
    return sorted(projects.values(), key=lambda p: p.last_active, reverse=True), threads


class T3:
    """`runner`, `launcher`, `focus`, `in_front`, `keys` and `sleep` default to the real subprocess / winutil / time
    calls; tests pass fakes. Keys are only ever pressed while T3 Code is checked to be the window in front."""

    def __init__(self, environments: list[Environment], *, runner=subprocess.run, launcher=None,
                 focus=None, in_front=None, keys=None, sleep=time.sleep):
        self.envs = environments
        self._run = runner
        self._launch = launcher or winutil.launch
        self._focus = focus or winutil.focus_window
        self._in_front = in_front or winutil.is_foreground
        self._keys = keys or winutil.hotkey
        self._sleep = sleep
        self._tokens: dict[str, str] = {}
        self._env_ids: dict[str, str] = {}
        self._retry_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self._last = T3State(envs=tuple(environments))

    # ----- reads -----

    def poll(self) -> T3State:
        online, projects, threads = {}, [], []
        now = time.time()
        for env in self.envs:
            if self._retry_at.get(env.key, 0) > now:
                online[env.key] = False
                ps = [p for p in self._last.projects if p.env == env.key]
                projects += ps
                continue
            try:
                shell = self._get(env, "/api/orchestration/shell?omitIdleSubagents=true&omitOlderSettled=true")
                ps, ts = parse_shell(shell, env, self._env_id(env), now)
                online[env.key] = True
                projects += ps
                threads += ts
                self._retry_at.pop(env.key, None)
            except Exception as e:
                online[env.key] = False
                self._retry_at[env.key] = now + (5 if env.primary else 30)
                log.debug("t3 %s unreachable: %s", env.key, e)
        threads.sort(key=lambda th: th.created, reverse=True)
        self._last = T3State(online=online, projects=tuple(projects), threads=tuple(threads), envs=tuple(self.envs))
        return self._last

    def _env_id(self, env: Environment) -> str:
        if env.key not in self._env_ids:
            with _opener.open(env.url + "/.well-known/t3/environment", timeout=3) as r:
                self._env_ids[env.key] = json.load(r)["environmentId"]
        return self._env_ids[env.key]

    def _get(self, env: Environment, path: str) -> dict:
        for attempt in (1, 2):
            token = self._token(env, refresh=attempt == 2)
            req = urllib.request.Request(env.url + path, headers={"Authorization": f"Bearer {token}",
                                                                  PROTOCOL_HEADER: "2"})
            try:
                with _opener.open(req, timeout=4) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 1:
                    log.info("t3 %s token rejected; issuing a new one", env.key)
                    continue
                raise
        raise RuntimeError("unreachable")

    def _token(self, env: Environment, refresh: bool = False) -> str:
        with self._lock:
            path = STATE_DIR / f"t3-token-{env.key}.txt"
            if not refresh:
                if env.key in self._tokens:
                    return self._tokens[env.key]
                if path.is_file():
                    self._tokens[env.key] = path.read_text().strip()
                    return self._tokens[env.key]
            out = self._run([*env.cli, *TOKEN_ARGS], capture_output=True, text=True, timeout=60,
                            creationflags=0x08000000)
            token = out.stdout.strip().splitlines()[-1].strip() if out.stdout.strip() else ""
            if out.returncode != 0 or len(token) < 20:
                raise RuntimeError(f"t3 token for {env.key} failed: {out.stderr.strip()[:200]}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token)
            self._tokens[env.key] = token
            return token

    # ----- actions (run on a worker thread; raise on failure so the key flashes red) -----

    def open_thread(self, thread: Thread) -> None:
        self._launch(f"t3code://threads/{thread.env_id}/{thread.id}")

    def new_thread(self, project: Project) -> None:
        env = next(e for e in self.envs if e.key == project.env)
        if env.primary:
            out = self._run([*env.cli, "app", project.root], capture_output=True, text=True, timeout=30,
                            creationflags=0x08000000)
            if out.returncode != 0:
                raise RuntimeError(out.stderr.strip()[:200] or "t3 app failed")
            # `t3 app` may have had to start T3 Code: give its window a few seconds to appear, and fail (red key,
            # and no "into new thread" on Dictate) rather than report a draft the user can't see.
            for _ in range(10):
                if self._focus(T3_EXE):
                    return
                self._sleep(0.5)
            raise RuntimeError("opened the draft, but T3 Code's window didn't come up")
        # The desktop app can only start drafts in its primary environment from outside, so for the others
        # open a new thread and the host picker, where the user picks this environment's project.
        self._bring_forward()
        self._sleep(0.4)
        self._send("ctrl+n")
        self._sleep(0.3)
        self._send("ctrl+shift+h")

    def open_app(self) -> None:
        if not self._focus(T3_EXE):
            self._launch(T3_EXE)

    def dictate(self) -> None:
        """Toggle T3 Code's composer dictation (its `composer.dictation` shortcut, Ctrl+Shift+D).

        T3 dictates into the thread that is open, so right after new_thread() it types into the new draft.
        """
        self._bring_forward()
        self._sleep(0.15)
        self._send("ctrl+shift+d")

    def _bring_forward(self) -> None:
        if not self._focus(T3_EXE):   # raises when it runs but Windows won't bring it forward
            raise RuntimeError("T3 Code isn't running")

    def _send(self, combo: str) -> None:
        """Press `combo` only if T3 Code is still in front: a toast or a click in the pause before it must not
        get T3's shortcuts (Ctrl+N in a browser opens a window)."""
        if not self._in_front(T3_EXE):
            raise RuntimeError(f"T3 Code lost focus; didn't press {combo}")
        self._keys(combo)


def environments_from_config(cfg: list[dict]) -> list[Environment]:
    envs = []
    for e in cfg:
        cli = [os.path.expandvars(x) for x in e["cli"]]
        envs.append(Environment(key=e["key"], name=e["name"], url=e["url"].rstrip("/"), cli=tuple(cli),
                                primary=bool(e.get("primary"))))
    return envs
