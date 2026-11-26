"""Deploy the deck from this repo to Program Files, as a service plus a per-user agent. The only way code gets
there: nothing in Program Files is edited by hand.

    python -m deck deploy      (administrator) copy, (re)install, restart; safe to run again after every change
    python -m deck undeploy    (administrator) remove the service, the agent task and the Program Files copy
    python -m deck restart     (no admin) restart the service and the agent with what is deployed

What deploy builds:
    C:\\Program Files\\UlanziDeck\\python          a copy of this repo's base Python (the SCM can't run a venv's
                                                 launcher python.exe as a service)
    C:\\Program Files\\UlanziDeck\\site-packages   the venv's packages, minus pip and pytest
    C:\\Program Files\\UlanziDeck\\app             deck/, deck.toml, deckd.py, deploy.json (version, user)
    service  UlanziDeck        LocalSystem, automatic, restarts on failure; the deploying user may start/stop it
    task     UlanziDeckAgent   the agent at logon in the user's session (+ a 5-minute watchdog run)
    C:\\ProgramData\\ulanzi-deck   the service's state: SYSTEM/admins write, the user reads (logs)
Program Files is writable only by administrators, so nothing running as the user can change code that runs as
SYSTEM. The copy is staged next to the live one and swapped in while everything is stopped.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from xml.sax.saxutils import escape

from .paths import PROGRAM_DIR, REPO, SERVICE_STATE_DIR, STATE_DIR

SERVICE = "UlanziDeck"
TASK = "UlanziDeckAgent"
OLD_TASK = "UlanziDeckDaemon"           # the single-process daemon this replaces
WATCHDOG_MINUTES = 5
NO_WINDOW = 0x08000000
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ULANZI_VALUE, OLD_RUN_VALUE = "UlanziDeck", "UlanziDeckDaemon"
ULANZI_BACKUP = STATE_DIR / "ulanzi-autostart.txt"

RUNTIME_SKIP = ["Lib/test", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "Lib/site-packages", "Doc", "include",
                "libs", "Tools", "Scripts", "tcl", "*/__pycache__"]
PACKAGES_SKIP = ["pip", "pip-*", "pytest", "pytest-*", "_pytest", "pluggy", "pluggy-*", "iniconfig", "iniconfig-*",
                 "py.py", "py_spy-*", "*/__pycache__"]
APP_SKIP = ["*/__pycache__"]


# ---------------- pure helpers (tested) ----------------

def service_sddl_with_user(sddl: str, user_sid: str) -> str:
    """Add an ACE that lets `user_sid` query, start and stop the service (for `deck restart` without admin)."""
    ace = f"(A;;CCLCLORPWPRC;;;{user_sid})"
    if ace in sddl:
        return sddl
    d = sddl.index("D:")
    s = sddl.find("S:", d)
    end = len(sddl) if s < 0 else s
    return sddl[:end] + ace + sddl[end:]


def skipper(root: Path, patterns: list[str]):
    """An ignore= for shutil.copytree that skips paths (relative to root, with /) matching any pattern."""
    def ignore(folder: str, names: list[str]) -> list[str]:
        rel = Path(folder).relative_to(root).as_posix()
        out = []
        for n in names:
            path = n if rel == "." else f"{rel}/{n}"
            if any(fnmatch(path, p) or fnmatch(n, p) for p in patterns):
                out.append(n)
        return out
    return ignore


def binpath(program: Path) -> str:
    return f'"{program / "python" / "python.exe"}" -I "{program / "app" / "deckd.py"}" service'


def task_xml(program: Path, user: str, user_sid: str, now: str) -> str:
    pythonw = escape(str(program / "python" / "pythonw.exe"))
    args = escape(f'-I "{program / "app" / "deckd.py"}" agent')
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Ulanzi deck agent: gives the deck service T3 Code, music and the other desktop
features of this session. Installed by python -m deck deploy; the 5-minute trigger restarts it if it exits.</Description>
    <SecurityDescriptor>D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;{escape(user_sid)})</SecurityDescriptor>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>
    <TimeTrigger><Enabled>true</Enabled><StartBoundary>{now}</StartBoundary>
      <Repetition><Interval>PT{WATCHDOG_MINUTES}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType>
    <RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <DisallowStartOnRemoteAppSession>false</DisallowStartOnRemoteAppSession>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec><Command>{pythonw}</Command><Arguments>{args}</Arguments>
    <WorkingDirectory>{escape(str(program / "app"))}</WorkingDirectory></Exec></Actions>
</Task>
"""


# ---------------- Windows plumbing ----------------

def _run(*cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(list(cmd), capture_output=True, text=True, creationflags=NO_WINDOW,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stdout + r.stderr).strip()}")
    return r


def _ps(script: str) -> str:
    return _run("powershell", "-NoProfile", "-NonInteractive", "-Command", script).stdout.strip()


def service_state() -> str | None:
    """RUNNING / STOPPED / START_PENDING / ..., or None when the service isn't installed."""
    out = _run("sc", "query", SERVICE).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "STATE" and parts[2].isdigit():
            return parts[3]
    return None


def _wait_service(state: str, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if service_state() in (state, None):
            return True
        time.sleep(0.5)
    return False


def _kill_deployed_processes(only_agent: bool = False) -> None:
    """Agents and helpers running from Program Files (and the old single-process daemon)."""
    prog = str(PROGRAM_DIR).replace("'", "''")
    filt = "$_.CommandLine -like '* agent*'" if only_agent else "$true"
    _ps(f"Get-CimInstance Win32_Process | Where-Object {{ ($_.ExecutablePath -like '{prog}\\*' -and ({filt})) "
        f"-or $_.CommandLine -like '*run_deck.pyw*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force "
        f"-ErrorAction SilentlyContinue }}")


def _content_version(*folders: Path) -> str:
    h = hashlib.sha256()
    for folder in folders:
        for f in sorted(folder.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                h.update(f.relative_to(folder).as_posix().encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:10]


def _user() -> tuple[str, str]:
    from .winpipe import process_sid
    return f"{os.environ.get('USERDOMAIN', '')}\\{os.environ['USERNAME']}", process_sid(os.getpid())


def _stage(stage: Path, user: str, sid: str) -> str:
    if stage.exists():
        shutil.rmtree(stage)
    base = Path(sys.base_prefix)
    venv_site = REPO / ".venv" / "Lib" / "site-packages"
    print(f"  python runtime   {base}")
    shutil.copytree(base, stage / "python", ignore=skipper(base, RUNTIME_SKIP))
    print(f"  packages         {venv_site}")
    shutil.copytree(venv_site, stage / "site-packages", ignore=skipper(venv_site, PACKAGES_SKIP))
    print(f"  app              {REPO / 'deck'}")
    shutil.copytree(REPO / "deck", stage / "app" / "deck", ignore=skipper(REPO / "deck", APP_SKIP))
    shutil.copy2(REPO / "deck.toml", stage / "app" / "deck.toml")
    shutil.copy2(REPO / "deckd.py", stage / "app" / "deckd.py")
    version = _content_version(stage / "app")
    (stage / "app" / "deploy.json").write_text(json.dumps({
        "version": version, "deployed": datetime.now().isoformat(timespec="seconds"), "user": user,
        "user_sid": sid, "repo": str(REPO)}, indent=2), encoding="utf-8")
    return version


def _swap(stage: Path) -> None:
    old = PROGRAM_DIR.with_name(PROGRAM_DIR.name + ".old")
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if PROGRAM_DIR.exists():
        for attempt in range(20):        # a process that just exited may still hold its files for a moment
            try:
                PROGRAM_DIR.rename(old)
                break
            except OSError:
                if attempt == 19:
                    raise
                time.sleep(0.5)
    stage.rename(PROGRAM_DIR)
    shutil.rmtree(old, ignore_errors=True)


def _state_dir(user_sid: str) -> None:
    import tomllib
    SERVICE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    _run("icacls", str(SERVICE_STATE_DIR), "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F",
         "*S-1-5-32-544:(OI)(CI)F", f"*{user_sid}:(OI)(CI)RX", check=True)
    with open(REPO / "deck.toml", "rb") as f:
        esp = tomllib.load(f).get("kvm", {}).get("esp_config")
    if esp and Path(os.path.expandvars(esp)).is_file():
        shutil.copy2(os.path.expandvars(esp), SERVICE_STATE_DIR / "ugreen-switch.json")
    side = STATE_DIR / "desk-side.txt"
    if side.is_file() and not (SERVICE_STATE_DIR / "desk-side.txt").exists():
        shutil.copy2(side, SERVICE_STATE_DIR / "desk-side.txt")


def _install_service(sid: str) -> None:
    if service_state() is None:
        _run("sc", "create", SERVICE, "binPath=", binpath(PROGRAM_DIR), "start=", "auto",
             "DisplayName=", "Ulanzi deck", check=True)
    else:
        _run("sc", "config", SERVICE, "binPath=", binpath(PROGRAM_DIR), "start=", "auto", check=True)
    _run("sc", "description", SERVICE, "Drives the Ulanzi D200 deck from boot to shutdown. Installed from "
         f"{REPO} by python -m deck deploy.")
    _run("sc", "failure", SERVICE, "reset=", "86400", "actions=", "restart/5000/restart/5000/restart/30000",
         check=True)
    _run("sc", "failureflag", SERVICE, "1")
    sddl = _run("sc", "sdshow", SERVICE, check=True).stdout.strip()
    _run("sc", "sdset", SERVICE, service_sddl_with_user(sddl, sid), check=True)


def _install_task(user: str, sid: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "task.xml"
        now = datetime.now().replace(microsecond=0).isoformat()
        xml.write_text(task_xml(PROGRAM_DIR, user, sid, now), encoding="utf-16")
        _run("schtasks", "/Create", "/TN", TASK, "/XML", str(xml), "/F", check=True)


def _autostart_entries() -> None:
    """The old Run-key entries: ours goes; the Ulanzi app's is backed up (undeploy puts it back)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
        try:
            winreg.DeleteValue(k, OLD_RUN_VALUE)
        except FileNotFoundError:
            pass
        try:
            value, _ = winreg.QueryValueEx(k, ULANZI_VALUE)
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            ULANZI_BACKUP.write_text(value)
            winreg.DeleteValue(k, ULANZI_VALUE)
            print("Removed the Ulanzi app from logon autostart (undeploy puts it back).")
        except FileNotFoundError:
            pass


def _require_admin() -> None:
    from .winutil import is_admin
    if not is_admin():
        sys.exit("Run this from an administrator terminal (it installs a service and writes to Program Files).")


# ---------------- commands ----------------

def deploy() -> None:
    _require_admin()
    if not (REPO / ".venv" / "Lib" / "site-packages").is_dir():
        sys.exit("No .venv in the repo: create it first (see README).")
    user, sid = _user()
    stage = PROGRAM_DIR.with_name(PROGRAM_DIR.name + ".new")
    print(f"Staging {stage}")
    version = _stage(stage, user, sid)
    print("Stopping the service, the agent and the old daemon")
    if service_state() not in (None, "STOPPED"):
        _run("sc", "stop", SERVICE)
        if not _wait_service("STOPPED", 30):
            sys.exit("The service didn't stop within 30 s; nothing was changed in Program Files.")
    _run("schtasks", "/End", "/TN", TASK)
    _run("schtasks", "/End", "/TN", OLD_TASK)
    _run("schtasks", "/Delete", "/TN", OLD_TASK, "/F")
    _kill_deployed_processes()
    print(f"Installing {PROGRAM_DIR} (version {version})")
    _swap(stage)
    _state_dir(sid)
    _install_service(sid)
    _install_task(user, sid)
    _autostart_entries()
    print("Starting")
    _run("sc", "start", SERVICE)
    _run("schtasks", "/Run", "/TN", TASK)
    ok = _wait_service("RUNNING", 15)
    print(f"Service {SERVICE}: {service_state()}" + ("" if ok else "  (see the log below)"))
    print(f"Logs: {SERVICE_STATE_DIR / 'deck.log'} (service), {SERVICE_STATE_DIR / 'console.log'} (monitor "
          f"helper), {STATE_DIR / 'agent.log'} (agent)")


def undeploy() -> None:
    _require_admin()
    _run("sc", "stop", SERVICE)
    _wait_service("STOPPED", 30)
    _run("sc", "delete", SERVICE)
    _run("schtasks", "/End", "/TN", TASK)
    _run("schtasks", "/Delete", "/TN", TASK, "/F")
    _kill_deployed_processes()
    if PROGRAM_DIR.exists():
        shutil.rmtree(PROGRAM_DIR)
    import winreg
    if ULANZI_BACKUP.is_file():
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
            winreg.SetValueEx(k, ULANZI_VALUE, 0, winreg.REG_SZ, ULANZI_BACKUP.read_text())
        ULANZI_BACKUP.unlink()
        print("Gave the Ulanzi app its logon autostart back.")
    print(f"Removed the service, the agent task and {PROGRAM_DIR}. Kept {SERVICE_STATE_DIR} (logs).")


def restart() -> None:
    state = service_state()
    if state is None:
        sys.exit("Not deployed: run python -m deck deploy from an administrator terminal.")
    if state != "STOPPED":
        _run("sc", "stop", SERVICE)
        _wait_service("STOPPED", 30)
    r = _run("sc", "start", SERVICE)
    if r.returncode != 0:
        sys.exit("Couldn't start the service: " + (r.stdout + r.stderr).strip())
    _kill_deployed_processes(only_agent=True)
    time.sleep(0.5)
    r = _run("schtasks", "/Run", "/TN", TASK)
    print("restarted" if r.returncode == 0 else "service restarted; agent: " + (r.stdout + r.stderr).strip())
