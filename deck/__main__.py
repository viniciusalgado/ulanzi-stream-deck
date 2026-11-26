r"""Ulanzi D200 deck.

Normal use: the deck runs as a Windows service from C:\Program Files\UlanziDeck, deployed from this repo, so
it works from boot to shutdown, signed in or not (docs/plans/service-mode.md):
    .venv\Scripts\python -m deck deploy           (administrator) copy this repo to Program Files and (re)start
    .venv\Scripts\python -m deck undeploy         (administrator) remove it, give the Ulanzi app its autostart back
    .venv\Scripts\python -m deck restart          restart the deployed service and agent (no admin)
    .venv\Scripts\python -m deck status           what's deployed and running, and what each source reports
Layout and tools:
    .venv\Scripts\python -m deck check            check deck.toml: lists every problem in the layout
    .venv\Scripts\python -m deck widgets          every widget, action and badge deck.toml can use
    .venv\Scripts\python -m deck run              everything in this terminal (refused while the service runs)
    .venv\Scripts\python -m deck preview [dir]    render every page (and the signed-out page) to PNG, live data
    .venv\Scripts\python -m deck reset            the deck froze: restart it over USB (no replug, no admin)
    .venv\Scripts\python -m deck headphones [reclaim|reset]   headphone state, or bring them back to this PC
    .venv\Scripts\python -m deck switch pc|laptop move monitors + USB switch from the command line
    .venv\Scripts\python -m deck monitors         DDC/CI models this PC can see
    .venv\Scripts\python -m deck spotify-login ID let playlist keys start playback (see deck/sources/spotify.py)
Started by the deployed copy (deckd.py), not by hand: service, agent, console-helper.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import time
from pathlib import Path

from .paths import LOG_FILE, STATE_DIR



def _logging(console: bool, file: Path = LOG_FILE) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(file, maxBytes=1_000_000,
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
        # An elevated daemon can't be stopped or replaced from a normal terminal, so one that hangs stays hung
        # (2026-11-25).
        log.error("don't run the deck from an administrator terminal (it couldn't be stopped later)")
        sys.exit(1)
    from .deploy import service_state
    if service_state() == "RUNNING":
        log.error("the deck service is running and owns the deck; stop it first (sc stop UlanziDeck)")
        sys.exit(1)
    for _ in range(20):  # on a restart the old instance may still be exiting: give it up to 10 s
        if winutil.single_instance("UlanziDeckDaemon"):
            break
        winutil.release_single_instance()
        time.sleep(0.5)
    else:
        log.error("the deck daemon is already running (python -m deck restart to restart it)")
        sys.exit(1)
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


def reset_deck() -> None:
    """Restart the deck's USB device. Unfroze the D200's firmware on 2026-11-25; the daemon reconnects by itself."""
    from .pnp import WindowsPnp
    ok, message = WindowsPnp().restart_deck()
    if not ok:
        sys.exit(message)
    print(message)


def agent() -> None:
    """The deployed agent (logon task): the signed-in user's desktop features, for the service."""
    import threading
    from . import winutil
    from .agent import run_agent
    from .config import load_config
    _logging(False, STATE_DIR / "agent.log")
    winutil.dpi_aware()
    log = logging.getLogger("deck")
    if winutil.is_admin():
        log.error("the agent must run as the signed-in user, not as administrator")
        sys.exit(1)
    if not winutil.single_instance("UlanziDeckAgent"):
        log.info("an agent is already running in this session")
        return
    while True:   # supervisor, as in run()
        try:
            run_agent(load_config(), threading.Event())
            return
        except Exception:
            log.exception("agent crashed; restarting in 5 s")
            time.sleep(5)


def console_helper() -> None:
    from .console import run_helper
    _logging(False, STATE_DIR / "console.log")
    try:
        run_helper()
    except Exception:
        logging.getLogger("deck").exception("monitor helper failed")
        sys.exit(1)


def service() -> None:
    _logging(False)
    from .service import main as service_main
    service_main()


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
    views = [(p.name, View(p.name), "here") for p in app.layout.pages if p.nav]
    views += [(name.replace(".", "-"), View(name, back=app.layout.start_page), "here") for name in app.widgets.screens]
    if app.layout.signed_out_page:
        views.append(("signed-out", View(app.layout.start_page), "signed_out"))
    views.append((f"{app.layout.start_page}-locked", View(app.layout.start_page), "locked"))
    for name, view, presence in views:
        app.sources.put("desktop", presence)
        screen = app.screen(view)
        canvas = Image.new("RGB", (pad * 2 + 5 * key + 4 * gap, pad * 2 + 3 * key + 2 * gap), "#050506")
        for i, k in screen.items():
            img = Image.open(io.BytesIO(render(k.tile)))
            col, row = (3, 2) if i == 13 else (i % 5, i // 5)
            canvas.paste(img, (pad + col * (key + gap), pad + row * (key + gap)))
        path = out / f"page-{name}.png"
        canvas.save(path)
        print(path)


def status() -> None:
    import json
    from .deploy import service_state
    from .paths import PROGRAM_DIR, SERVICE_STATE_DIR
    info = PROGRAM_DIR / "app" / "deploy.json"
    if info.is_file():
        d = json.loads(info.read_text(encoding="utf-8"))
        print(f"Deployed: {d['version']} on {d['deployed']} for {d['user']}")
    else:
        print("Deployed: no (python -m deck deploy, as administrator)")
    print("Service:", service_state() or "not installed", f"(log {SERVICE_STATE_DIR / 'deck.log'})")
    print("Agent log:", STATE_DIR / "agent.log")
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
    if snap.printer_power is not None:
        print("Printer switch:", snap.printer_power.value)
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
    elif cmd == "deploy":
        from .deploy import deploy
        deploy()
    elif cmd == "undeploy":
        from .deploy import undeploy
        undeploy()
    elif cmd == "restart":
        from .deploy import restart
        restart()
    elif cmd == "service":
        service()
    elif cmd == "agent":
        agent()
    elif cmd == "console-helper":
        console_helper()
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
