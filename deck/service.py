"""The deck service: owns the deck from boot to shutdown, whether anyone is signed in or not.

    python -I "C:\\Program Files\\UlanziDeck\\app\\deckd.py" service     # started by the SCM (see deck.deploy)

Runs the App with the agent's RemoteDesktop (desktop features, in the user's session) and the ConsoleDdc
(monitor switching, in the console session), and tells both every second who is at the PC. Its state (logs,
the desk side, the ESP32's config) lives in C:\\ProgramData\\ulanzi-deck, written only by SYSTEM and admins.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

from . import winsession
from .agent import RemoteDesktop
from .app import App
from .config import load_config
from .console import ConsoleDdc
from .paths import DEPLOY_INFO, REPO, STATE_DIR
from .sources.t3 import environments_from_config

log = logging.getLogger(__name__)
SERVICE_NAME = "UlanziDeck"
ESP_CONFIG = "ugreen-switch.json"     # deck.deploy copies the user's ESP32 config here (STATE_DIR)


def helper_argv() -> list[str]:
    """The console helper: the same deployed interpreter, windowless."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return [str(pythonw), "-I", str(REPO / "deckd.py"), "console-helper"]


def serve(stop: threading.Event) -> None:
    info = json.loads(DEPLOY_INFO.read_text(encoding="utf-8"))
    cfg = load_config()
    if "kvm" in cfg:
        cfg["kvm"]["esp_config"] = str(STATE_DIR / ESP_CONFIG)
    ddc = ConsoleDdc(lambda session, signed_in: winsession.spawn_system(session, helper_argv(), signed_in=signed_in,
                                                                         cwd=str(REPO)))
    desktop = RemoteDesktop(environments_from_config(cfg.get("t3", {}).get("environments", [])), info["user_sid"])
    ddc.start()
    desktop.start()
    log.info("deck service %s running (deployed %s for %s)", info.get("version"), info.get("deployed"),
             info.get("user"))
    watcher = threading.Thread(target=_watch, args=(stop, ddc, desktop), name="console-watch", daemon=True)
    watcher.start()
    try:
        while not stop.is_set():          # supervisor: a crash restarts the App instead of leaving the deck dark
            app = App(cfg, desktop=desktop, ddc=ddc)
            runner = threading.Thread(target=app.run, name="app", daemon=True)
            runner.start()
            while runner.is_alive() and not stop.wait(1):
                pass
            app.stop()
            runner.join(10)
            if not stop.is_set():
                log.error("the deck loop ended unexpectedly; restarting it in 5 s")
                stop.wait(5)
    finally:
        desktop.close()
        ddc.close()


def _watch(stop: threading.Event, ddc: ConsoleDdc, desktop: RemoteDesktop) -> None:
    last = None
    while not stop.is_set():
        try:
            console = winsession.console()
            if console != last:
                log.info("console: session %s, %s%s", console.session, console.user or "nobody signed in",
                         ", locked" if console.locked else "")
                last = console
            desktop.follow(console)
            ddc.follow(console)
        except Exception:
            log.exception("console watch failed")
        stop.wait(1.0)


def main() -> None:
    from .winservice import run_as_service
    started = time.monotonic()
    try:
        run_as_service(SERVICE_NAME, serve)
    except OSError:
        log.exception("not started by the Service Control Manager (%.1f s)", time.monotonic() - started)
        raise
