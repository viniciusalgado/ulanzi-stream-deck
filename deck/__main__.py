"""Ulanzi D200 deck daemon.

    .venv\\Scripts\\python -m deck run              run in this terminal (logs here and in %APPDATA%\\ulanzi-deck)
    .venv\\Scripts\\python -m deck install          start at logon + restart if it exits (instead of the Ulanzi app)
    .venv\\Scripts\\python -m deck uninstall        stop, remove autostart, give the Ulanzi app its autostart back
    .venv\\Scripts\\python -m deck restart          after editing deck.toml or the code
    .venv\\Scripts\\python -m deck reset            the deck froze: restart it over USB (no replug, no admin)
    .venv\\Scripts\\python -m deck headphones [reclaim|reset]   headphone state, or bring them back to this PC
    .venv\\Scripts\\python -m deck check            check deck.toml: lists every problem in the layout
    .venv\\Scripts\\python -m deck widgets          every widget, action and badge deck.toml can use
    .venv\\Scripts\\python -m deck preview [dir]    render every page to PNG with live data, without the deck
    .venv\\Scripts\\python -m deck status           what each source reports right now
    .venv\\Scripts\\python -m deck switch pc|laptop move monitors + USB switch from the command line
    .venv\\Scripts\\python -m deck monitors         DDC/CI models this PC can see
    .venv\\Scripts\\python -m deck spotify-login ID let playlist keys start playback (see deck/sources/spotify.py)
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys
import time
from pathlib import Path

from .paths import LOG_FILE, REPO, STATE_DIR

PID_FILE = STATE_DIR / "deck.pid"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
OUR_VALUE = "UlanziDeckDaemon"
ULANZI_VALUE = "UlanziDeck"
ULANZI_BACKUP = STATE_DIR / "ulanzi-autostart.txt"
PYTHONW = REPO / ".venv" / "Scripts" / "pythonw.exe"
LAUNCHER = REPO / "run_deck.pyw"
TASK_NAME = "UlanziDeckDaemon"
WATCHDOG_MINUTES = 5


def _logging(console: bool) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000,
                                                                             backupCount=3, encoding="utf-8")]
    if console and sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


def run(console: bool = True) -> None:
    from . import winutil
    from .app import App
    from .config import load_config

    _logging(console)
    winutil.dpi_aware()
    log = logging.getLogger("deck")
    if winutil.is_admin():
        # An elevated daemon can't be stopped or replaced from a normal terminal or by the logon task, so one
        # that hangs stays hung (2026-11-25). Run it as the user, through the task when it's installed.
        if _task_installed():
            log.warning("not running the deck daemon as administrator; starting it through the logon task instead")
            start()
            return
        log.error("don't run the deck daemon from an administrator terminal (it couldn't be stopped later)")
        sys.exit(1)
    for _ in range(20):  # on a restart the old instance may still be exiting: give it up to 10 s
        if winutil.single_instance("UlanziDeckDaemon"):
            break
        winutil.release_single_instance()
        time.sleep(0.5)
    else:
        log.error("the deck daemon is already running (python -m deck restart to restart it)")
        sys.exit(1)
    PID_FILE.write_text(str(os.getpid()))
    while True:  # supervisor: a crash restarts the app instead of leaving the deck frozen
        app = None
        try:
            app = App(load_config())
            app.run()
            return
        except KeyboardInterrupt:
            if app:
                app.stop()
            return
        except Exception:
            log.exception("deck crashed; restarting in 5 s")
            if app:
                app.stop()
            time.sleep(5)


def _ps(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, creationflags=0x08000000)  # no console window


def _task_installed() -> bool:
    return _ps(f"if (Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue) {{ 'yes' }}"
               ).stdout.strip() == "yes"


def start() -> None:
    if _task_installed():  # run it under the watchdog task, so the task keeps track of it
        # A just-killed instance can take a moment to leave the Running state; until then a new run is ignored.
        _ps(f"$i = 0; while ((Get-ScheduledTask -TaskName '{TASK_NAME}').State -eq 'Running' -and $i -lt 50) "
            f"{{ Start-Sleep -Milliseconds 100; $i++ }}; Start-ScheduledTask -TaskName '{TASK_NAME}'")
        return
    subprocess.Popen([str(PYTHONW), str(LAUNCHER)], cwd=str(REPO),
                     creationflags=0x00000008 | 0x00000200, close_fds=True)  # detached, own process group


def stop() -> bool:
    if not PID_FILE.is_file():
        return False
    pid = PID_FILE.read_text().strip()
    r = subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, text=True)
    if r.returncode not in (0, 128):  # 128 = no such process: it already exited
        print(f"Couldn't stop the running daemon (PID {pid}): {(r.stdout + r.stderr).strip()}\n"
              "If it was started from an administrator terminal, stop it from one: taskkill /PID " + pid + " /F")
        return False
    PID_FILE.unlink(missing_ok=True)
    return r.returncode == 0


def reset_deck() -> None:
    """Restart the deck's USB device. Unfroze the D200's firmware on 2026-11-25; the daemon reconnects by itself."""
    from .pnp import WindowsPnp
    ok, message = WindowsPnp().restart_deck()
    if not ok:
        sys.exit(message)
    print(message)


def _task_xml() -> str:
    from datetime import datetime
    from xml.sax.saxutils import escape
    user = escape(os.environ.get("USERDOMAIN", "") + "\\" + os.environ["USERNAME"])
    now = datetime.now().replace(microsecond=0).isoformat()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Ulanzi D200 deck daemon ({escape(str(REPO))}). Starts at logon; the
5-minute trigger is a watchdog that starts it again if it ever exits (a run is skipped while it's running).
Remove with: python -m deck uninstall</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId></LogonTrigger>
    <TimeTrigger><Enabled>true</Enabled><StartBoundary>{now}</StartBoundary>
      <Repetition><Interval>PT{WATCHDOG_MINUTES}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType>
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
  <Actions Context="Author"><Exec><Command>"{escape(str(PYTHONW))}"</Command>
    <Arguments>"{escape(str(LAUNCHER))}"</Arguments><WorkingDirectory>{escape(str(REPO))}</WorkingDirectory></Exec></Actions>
</Task>
"""


def install() -> None:
    import tempfile
    import winreg
    if not PYTHONW.is_file():
        sys.exit(f"{PYTHONW} not found: create the venv first (see README).")
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "task.xml"
        xml.write_text(_task_xml(), encoding="utf-16")
        r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml), "/F"],
                           capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Couldn't create the logon task: " + (r.stdout + r.stderr).strip())
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
        try:
            winreg.DeleteValue(k, OUR_VALUE)  # the older Run-key autostart; the task replaces it
        except FileNotFoundError:
            pass
        try:
            value, _ = winreg.QueryValueEx(k, ULANZI_VALUE)
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            ULANZI_BACKUP.write_text(value)
            winreg.DeleteValue(k, ULANZI_VALUE)
            print("Removed the Ulanzi app from logon autostart (uninstall puts it back).")
        except FileNotFoundError:
            pass
    stop()
    start()
    print(f"Installed: task '{TASK_NAME}' starts the deck daemon at logon and restarts it within "
          f"{WATCHDOG_MINUTES} min if it ever exits. Starting it now.")


def uninstall() -> None:
    import winreg
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True)
    stop()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
        try:
            winreg.DeleteValue(k, OUR_VALUE)
        except FileNotFoundError:
            pass
        if ULANZI_BACKUP.is_file():
            winreg.SetValueEx(k, ULANZI_VALUE, 0, winreg.REG_SZ, ULANZI_BACKUP.read_text())
            ULANZI_BACKUP.unlink()
            print("Gave the Ulanzi app its logon autostart back.")
    print("Uninstalled the deck daemon.")


class _NoDeck:
    def __init__(self, **_):
        pass

    def start(self):
        pass

    def close(self):
        pass

    def show(self, images):
        pass

    def invalidate(self):
        pass


def _live_app():
    from .app import App
    from .config import load_config

    app = App(load_config(), deck_factory=_NoDeck)
    app.sources.poll_all()
    if "printer.camera" in app.actions:
        try:
            app.actions.run("printer.camera")
        except Exception:
            pass
    return app


def preview(out: Path) -> None:
    """One PNG per page, laid out like the physical deck."""
    from PIL import Image

    from .keys import View
    from .render import render
    import io

    app = _live_app()
    out.mkdir(parents=True, exist_ok=True)
    gap, key, pad = 18, 196, 24
    views = [View(p.name) for p in app.layout.pages]
    views += [View(name, back=app.layout.start_page) for name in app.widgets.screens]
    for view in views:
        screen = app.screen(view)
        canvas = Image.new("RGB", (pad * 2 + 5 * key + 4 * gap, pad * 2 + 3 * key + 2 * gap), "#050506")
        for i, k in screen.items():
            img = Image.open(io.BytesIO(render(k.tile)))
            col, row = (3, 2) if i == 13 else (i % 5, i // 5)
            canvas.paste(img, (pad + col * (key + gap), pad + row * (key + gap)))
        path = out / f"page-{view.page.replace('.', '-')}.png"
        canvas.save(path)
        print(path)


def status() -> None:
    snap = _live_app().snapshot()
    if snap.t3 is not None:
        print("T3 online:", snap.t3.online)
        for t in snap.t3.threads:
            print(f"  {t.state:<9} {t.project.title[:18]:<18} {t.title}")
    if snap.media is not None:
        m = snap.media
        print("Media:", "playing" if m.playing else "paused" if m.available else "none", m.title, "-", m.artist)
        print("Playlists:", [p.title for p in snap.playlists or ()])
    if snap.printer is not None:
        p = snap.printer
        print(f"Printer: {p.state} {p.progress:.0%} {p.filename} nozzle {p.nozzle} bed {p.bed}")
    if snap.kvm is not None:
        print("Desk:", snap.kvm)
    if snap.headphones is not None:
        print("Headphones:", snap.headphones)


def check() -> None:
    from .catalog import check as run_check
    from .config import load_config
    ok, text = run_check(load_config())
    print(text)
    if not ok:
        sys.exit(1)


def widgets() -> None:
    from .catalog import describe, registries
    from .config import load_config
    from .paths import CONFIG_FILE, EXAMPLE_CONFIG
    actions, ws, _ = registries(load_config(CONFIG_FILE if CONFIG_FILE.is_file() else EXAMPLE_CONFIG))
    print(describe(actions, ws), end="")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        run(console="--quiet" not in argv)
    elif cmd == "install":
        install()
    elif cmd == "uninstall":
        uninstall()
    elif cmd == "restart":
        stop()
        start()
        print("restarted")
    elif cmd == "stop":
        if stop():
            print("stopped")
        elif not PID_FILE.is_file():
            print("wasn't running")
    elif cmd == "preview":
        preview(Path(argv[1]) if len(argv) > 1 else STATE_DIR / "preview")
    elif cmd == "status":
        status()
    elif cmd == "check":
        check()
    elif cmd == "widgets":
        widgets()
    elif cmd == "switch" and len(argv) == 2:
        from .config import load_config
        from .sources.kvm import Kvm
        Kvm.from_config(load_config()["kvm"]).switch(argv[1])
        print("switched to", argv[1])
    elif cmd == "reset":
        reset_deck()
    elif cmd == "headphones":
        from .config import load_config
        from .sources.headphones import Headphones
        from .sources.media import Media
        hp = Headphones(load_config().get("headphones", {}).get("name", "WH-1000XM5"))
        if len(argv) > 1 and argv[1] in ("reclaim", "reset"):
            getattr(hp, argv[1])(Media([]))
        print(hp.poll())
    elif cmd == "monitors":
        from .sources.kvm import Ddc
        print(Ddc().models())
    elif cmd == "spotify-login" and len(argv) == 2:
        from .sources.spotify import login
        login(argv[1])
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
