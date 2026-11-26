# Plan: the deck works with nobody signed in (option C)

Status, 2026-11-26: phases 1-6 built on branch `service-mode`. Decisions taken:
- the signed-out page keeps every key that works without a session;
- code runs only from `C:\Program Files\UlanziDeck`, copied there by `python -m deck deploy`;
- no pywin32: ctypes for the service, the pipes and the sessions.

Not yet run as SYSTEM: phase 0's questions (HID from session 0, DDC from the console helper) get answered by the
first `deck deploy` and the phase 7 checklist.

## Goal

The deck stays live from boot to shutdown, whether anyone is signed in or not. With nobody signed in, it still
shows the 3D Print page and the desk switch, and the desk switch works. When you sign in, the T3, Fun and
Spotify features come back within a few seconds, with no flicker and no restart of the deck.

## Today

`UlanziDeckDaemon` is a scheduled task with an InteractiveToken, so the whole daemon lives inside your
session:

- It dies at logoff.
- It doesn't exist before logon. On this PC that's about 3 minutes from boot to logon, and in that time the
  deck shows its factory screen.
- When you're "logged off" on this PC, it has always been during the nightly shutdown.

## Target architecture

```
 ┌──────────────── session 0 ────────────────┐        ┌─────── your session (when signed in) ───────┐
 │ deck service (LocalSystem, auto-start)    │  pipe  │ deck agent (today's task, renamed)          │
 │  owns the D200 (device.Deck), presses,    │◄──────►│  sources: t3, media, playlists, headphones  │
 │  pages, render, main loop                 │  JSON  │  actions: launch, t3 open/new/dictate,      │
 │  host sources: printer, kvm (ESP + USB    │        │  media transport, playlists, headphones     │
 │  presence), camera, Ulanzi-app guard      │        └─────────────────────────────────────────────┘
 │  session watcher (none / active / locked) │
 └───────────────┬───────────────────────────┘
                 │ pipe (SYSTEM only)
 ┌───────────────▼───────────────────────────┐
 │ console helper (SYSTEM, in the console    │   the session that owns the monitors: the login screen
 │  session): DDC/CI models + set_inputs     │   when nobody is signed in, your session otherwise
 └───────────────────────────────────────────┘
```

Why three processes:

- **Service.** USB HID works from session 0, and the service is the only process that is always present.
- **Console helper.** Session 0 has no display, so dxva2 finds no monitors there. DDC has to run in the
  session that owns the console. A SYSTEM process started there works whether or not anyone is signed in.
  This keeps the desk switch independent of the agent.
- **Agent.** T3 Code, the Windows media session, Spotify, Bluetooth audio endpoints and SendInput all belong
  to your session.

### What the 2026-11-26 refactor changes for this plan

Reviewed against the code as of 10:30. The refactor is still going on and nothing is committed.

- **`presses.py`** (pure, clock-driven) moves to the service unchanged.
- **`sources/registry.py`**: session sources become values the agent pushes. That needs a ~10-line
  `Sources.put(name, value)` (store, notify) plus `reset(name)` (back to the initial value when the agent goes
  away). `refresh()` / `poll_all()` don't change.
- **`pnp.py`** (`WindowsPnp`: stuck-in-boot detection, USB restart) moves to the service. Running as SYSTEM,
  the USB restart always works, so the service can try it by itself when the deck stops answering.
- **`Deck(pnp=, clock=)`**, **`Headphones(audio=)`**, **`Media(start_playlist=, launcher=, sleep=)`** and
  **`Kvm(ddc=)`** can all be injected. In the service, each session-bound object becomes a proxy with the same
  methods. Pages keep calling `svc.media.toggle()`, `svc.t3.open_thread(t)` and the rest, and don't change
  (except for the signed-out and locked states).
- **Page actions pass dataclasses as arguments**, `Do(svc.t3.open_thread, (thread,))` and
  `Do(svc.new_thread, (project,))`, so the JSON codec covers action arguments as well as state.
- **`T3State` now carries `envs`**, the configured `Environment`s including `cli`, a command line the agent
  runs. The service must never take `cli` (or any path or command) from the agent's state. It uses
  `environments_from_config` from its own `deck.toml`, and the codec drops `cli` on the way in.
- **`T3_EXE` is a fixed path in your profile.** That's fine, because only the agent uses it.

Net: pages, render, presses, device and pnp stay as they are. Only the wiring in `app.py` / `__main__.py`
changes, plus the proxies, the codec and the new processes. **Phase 1 starts only after the refactor is
committed**, so the two don't edit `app.py` at the same time.

## Phase 0: spikes (go / no-go, admin needed, throwaway scripts in `scripts/spikes/`)

Stop the user daemon before each spike: two programs on the HID fight over the screen.

| # | Question | How | Pass |
|---|---|---|---|
| S1 | Can SYSTEM in session 0 drive the D200? | One-off SYSTEM scheduled task runs a script: `Deck.start()`, shows a test page, logs key presses | Screen shows, presses logged |
| S2 | Does DDC work from a SYSTEM process in the console session? | SYSTEM launcher: `WTSGetActiveConsoleSessionId` → `DuplicateTokenEx` → `SetTokenInformation(TokenSessionId)` → `CreateProcessAsUser` on `winsta0\winlogon` (logged off) and `winsta0\default` (signed in); helper runs `Ddc().models()` and switches both monitors | Works signed in, locked, and at the login screen |
| S3 | Does DDC still work once the login screen has turned the monitors off? | Repeat S2 after more than 60 s at the login screen (`VIDEOCONLOCK` timeout) | Monitors wake on the other input. If not, raise that timeout with powercfg |
| S4 | Does pywin32 work in this Python 3.14 venv, as a service? | `pip install pywin32`, minimal `ServiceFramework` that logs session-change events | Service starts, logs LOGON / LOGOFF / LOCK / UNLOCK |

If S2 fails, there are two fallbacks:

1. DDC from the service itself through AMD ADL (`atiadlxx.dll`, `ADL2_Display_DDCBlockAccess_Get`; the
   RX 580 supports it).
2. As a last resort, the agent switches the monitors when you're signed in, and AOC auto-source covers the
   logged-off case (monitors only, no button).

## Phase 1: seams, with no change in behaviour (user mode, unit tests)

1. **`deck/wire.py`**, a JSON codec for the state that crosses the pipe:
   - Types: `T3State`/`Project`/`Thread`, `MediaState`, `Playlist`, `HeadphoneState`, plus an action result.
   - Uses an explicit list of allowed types. Bytes (covers, album art) go as base64.
   - **No pickle.** The service runs as SYSTEM, and unpickling anything another process sent would let that
     process run code as SYSTEM.
2. **Push sources.** `Sources.push(name, value)` stores a value that came from elsewhere and notifies, the
   same as a poll would. `Sources.stale(name)` resets a source to its initial value when the agent goes away.
3. **Session in the snapshot.** `Snapshot.session` is `none | active | locked` and `Snapshot.agent` is
   connected or not. pages get:
   - **Signed out:** a page of its own, `pages._signed_out`, not the Print page. Most of the Print page needs
     your session: keys 0-3 (T3), 9 (Cura), a tap on 5 (Mainsail) and a hold on 4 (live stream).
     - Key 0 says "Signed out"; key 1 shows a clock; keys 2, 3 and 9 are blank.
     - Keys 4-8: camera still, printer status, filament change, pause/resume, cancel (hold 2 s).
     - Keys 10 and 11 (Fun and AI) are dimmed and say "sign in". Key 12 is Print. Key 13 is the desk switch.
     - It is the only page while nobody is signed in; at sign-in the deck goes back to `start_page`.
     - Mockup, drawn with `deck.render`: `signed-out-mock.png` next to this file.
   - **Locked:** keys that need the desktop (launch, dictate, new thread, open thread, playlist start) show
     "Locked" and do nothing. Transport, printer and desk keep working.
   - **Signed in but agent missing** (crashed, or not started yet): the session keys show "agent not running"
     instead of going dead.
4. **`svc` as an interface.** The App keeps calling `svc.t3.open_thread(...)` etc. In the service, `svc.t3`,
   `svc.media`, `svc.launch`, `svc.new_thread`, `svc.toggle_dictation` and the headphone actions are proxies
   that send a `run` message to the agent and raise if there is no agent, so the key flashes red. Keep the
   dictation and draft timers in the App: they come from key presses.
5. **Paths.**
   - `paths.STATE_DIR` becomes a function of the role: the service uses `C:\ProgramData\ulanzi-deck`
     (SYSTEM and Administrators only, because it holds the ESP token); the agent keeps
     `%APPDATA%\ulanzi-deck`.
   - `deck.toml`'s `esp_config = '%APPDATA%\…'` would expand to SYSTEM's profile, so the service's copy of the
     ESP config lives in ProgramData. `deck install` copies it there.

Exit: `pytest` green; `python -m deck run` (the single-process mode, kept for development) behaves exactly as today.

## Phase 2: the pipes (`deck/ipc.py`)

- **Agent pipe:** `\\.\pipe\ulanzi-deck-agent`, created by the service.
  - Opened with `FILE_FLAG_FIRST_PIPE_INSTANCE`, so nothing else can create it first and pretend to be the
    service.
  - DACL: SYSTEM full control, your account (the SID saved at install) read/write, NETWORK denied.
  - On connect, the service checks the client with `GetNamedPipeClientProcessId`: its token user must be the
    configured SID and its session must be the active console session. Anything else is dropped.
- **Console pipe:** `\\.\pipe\ulanzi-deck-console`, SYSTEM only.
- **Framing:** 4-byte length, then UTF-8 JSON, with messages capped at 4 MB. Every message has a `v` (protocol
  version).
- **Messages:**
  - agent → service: `hello {v, pid, session}`, `state {source, value}` (only when a value changed),
    `result {id, ok, error}`, `pong`
  - service → agent: `run {id, action, args}`, `ping`
  - helper ↔ service: `models`, `set_inputs {codes}` → `{ok_by_model}`
- **Service side, `AgentLink`:** `run(action, args, timeout)` returns a future. When the pipe drops it fails
  pending runs, marks the session sources stale, and the UI falls back to signed-out or locked.
- **Agent side, `ServiceLink`:** reconnect loop with backoff (0.5 s → 5 s). It re-sends every source's current
  value after `hello`, so a service restart repaints instantly.

Exit: tests over an in-memory transport (framing, run/result, timeout, disconnect, version mismatch); a
two-process test on real pipes in user mode.

## Phase 3: the agent (`python -m deck agent`)

- Runs the session sources on the existing `Sources` registry and pushes changes to the service.
- Runs actions from an explicit table: `launch`, `t3.open_thread`, `t3.new_thread`, `t3.open_app`,
  `t3.dictate`, `media.toggle|next|prev|play_playlist`, `headphones.reclaim|reset`. Arguments are decoded with
  `wire` and checked: for example, `launch` only accepts targets from `deck.toml` or `t3code:` / `spotify:`
  URIs.
- One instance per session (the `Local\` mutex, as today). It refuses to run elevated (as today).
- The scheduled task stays, but points at `agent` instead of `run`. The 5-minute watchdog trigger stays.

## Phase 4: the console helper (`python -m deck console-helper`)

- The service starts it in the active console session as SYSTEM, on `winsta0\winlogon` when nobody is signed
  in and on `winsta0\default` otherwise. This is the S2 launcher, cleaned up into `deck/consoles.py`.
- It keeps `Ddc` warm (caps read once, which takes 3.3 s cold) and answers `models` / `set_inputs`.
- The service starts a new helper whenever the console session changes (logon, logoff, console
  connect/disconnect), and restarts it if it dies.
- `Kvm(ddc=ConsoleDdc(link))` in the service: same interface as `Ddc`, so `kvm.py` doesn't change.
- Latency budget: from key press to DDC write under 100 ms (today about 60 ms). Log it as now ("monitors sent
  to X N ms after the key press").

## Phase 5: the service (`python -m deck service`, pywin32 `ServiceFramework`)

- **Startup type:** Automatic, LocalSystem, with `SERVICE_ACCEPT_SESSIONCHANGE`. As a safety net it also
  polls `WTSEnumerateSessions` every 2 s.
- **Recovery:** restart after 5 s, then 5 s, then 30 s. This replaces the watchdog for the process that owns
  the device. The supervisor loop in `__main__.run` stays.
- **Session watcher:** a small pure state machine (`deck/session.py`): console session id, signed in or not,
  locked or not, agent connected or not. Tested on its own.
- **Ulanzi-app guard:** moves to the service (SYSTEM can kill it in any session).
- **Logs:** `C:\ProgramData\ulanzi-deck\service.log` for the service; the agent and helper log to their own
  files. `deck status` reads all three.
- **Letting you restart it without admin:** at install, `sc sdset` grants your SID `SERVICE_START`,
  `SERVICE_STOP` and `SERVICE_QUERY_STATUS`, so `python -m deck restart` keeps working from a normal terminal.

## Phase 6: install, deploy, CLI

The service must **not** run code you can edit without admin. Otherwise anything running as you could change
`deck/*.py` in this repo and get SYSTEM.

- `python -m deck deploy` (elevated) copies `deck/`, `deck.toml` and a venv to `C:\Program Files\UlanziDeck`
  (writable only by admins), then restarts the service.
- The agent can keep running from the repo.
- **Trade-off:** editing `deck.toml` becomes "edit, then `deck deploy`" (one UAC prompt) instead of
  "edit, then `deck restart`".

CLI changes:

- `install` (elevated):
  - creates the service, the ProgramData folder and its ACLs, and the service DACL;
  - saves your SID for the pipe ACL and copies the ESP config;
  - points the task at `agent`, and deploys.
- `uninstall` removes all of that and goes back to the single-process daemon, or to the Ulanzi app as today.
- `status` shows service, agent, helper, session state and the deck.
- `run` stays as the single-process development mode (it must refuse while the service is running).
- `preview` stays in-process, with an optional `--signed-out` / `--locked`.

## Phase 7: live checklist (manual, record results in memory)

1. Cold boot: how many seconds until the deck shows the signed-out page (target: before the login screen
   appears).
2. Login screen: desk to laptop and back; again after the monitors have gone dark (60+ s).
3. Sign in: T3 threads and now-playing appear within 3 s; no flicker; no factory screen.
4. Lock (Win+L): desk, transport and printer work; launch keys show Locked. Unlock: all back.
5. Sign out: the deck drops to the signed-out page; the desk key still works.
6. Kill the agent: the service keeps the deck live and the agent is back within 5 min. Kill the helper: it's
   restarted. Kill the service: recovery restarts it.
7. Replug the deck while signed out; sleep and resume while signed out and while signed in.
8. A 2-hour soak on the Print page while signed out (the firmware freezes came from long sessions).

## Risks / open points

- **S2 is the whole plan for the desk switch while signed out.** If neither S2 nor ADL works, the logged-off
  desk switch is limited to the USB switch plus AOC auto-source.
- **pywin32 on Python 3.14** in a venv-run service is a known source of trouble (DLL search path,
  `pythonservice.exe`). If S4 fails: a tiny C# or Go service host that runs `python -m deck service`, with the
  service polling sessions instead of receiving events.
- **Fast user switching or RDP:** only the active console session's agent counts. Other sessions' agents get
  refused, and that is logged.
- **Concurrent edits:** the code is mid-refactor and uncommitted. Commit a baseline before phase 1.

## Decisions for the user

1. Signed-out page: the dedicated page described in phase 1, item 3 (proposed)? Keys 2, 3 and 9 are free:
   something else useful there?
2. Install the code to `C:\Program Files` (secure, needs `deck deploy` after each edit), or run the service
   from the repo (convenient, but anything running as you can become SYSTEM)?
3. OK to add pywin32 as a dependency?
