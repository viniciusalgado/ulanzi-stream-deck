"""The desk switch: both monitors (DDC/CI) and the UGREEN USB switch move between this PC and the laptop.
On when deck.toml has [kvm] (see deck.sources.kvm and deck.example.toml).

Sources: kvm (KvmState), kvm_ui (busy, and the last error / note for a few seconds).
Actions: kvm.toggle (to the other side of where the monitors are *now*), kvm.switch(to: "pc" | "laptop")
Widget: kvm.switch (made for the wide key, 13).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from ..keys import Do, Key
from ..render import AMBER, RED, SwitchTile
from ..sources.kvm import Kvm
from ..widgets import Option

log = logging.getLogger(__name__)
NAME = "kvm"
SECTION = "kvm"
MESSAGE_S = 10


@dataclass(frozen=True)
class KvmUi:
    busy: bool = False
    message: str = ""
    error: bool = False
    until: float = 0.0     # epoch seconds the message shows until


def setup(s, kvm: Kvm | None = None) -> None:
    kvm = kvm or Kvm.from_config(s.section, ddc=s.ddc)
    s.sources.add("kvm", kvm.poll, every=3.0)
    s.sources.add("kvm_ui", None, initial=KvmUi())
    s.lively(lambda snap: snap.kvm_ui is not None and snap.kvm_ui.busy)

    def ui(**kw) -> None:
        s.sources.put("kvm_ui", KvmUi(**kw))

    def switch(to: str) -> None:
        """Monitors first, as fast as possible (this is what the key waits for); then the USB switch on its own."""
        ui(busy=True)
        try:
            kvm.switch(to)
            log.info("monitors sent to %s %.0f ms after the key press", to, (time.monotonic() - s.pressed_at()) * 1000)
            ui()
        except Exception as e:
            ui(message=_short(str(e)), error=True, until=time.time() + MESSAGE_S)
            raise
        finally:
            if s.sources.get("kvm_ui").busy:
                ui()
            s.sources.refresh("kvm")
        threading.Thread(target=usb_follow, args=(to,), name="usb-follow", daemon=True).start()

    def usb_follow(to: str) -> None:
        note = kvm.switch_usb(to)
        if note:
            log.info("desk switched to %s; %s", to, note)
            ui(message=_short(note), until=time.time() + MESSAGE_S)
        s.sources.refresh("kvm")

    def toggle() -> None:
        """move the desk to the other machine"""
        switch("laptop" if kvm.side == "pc" else "pc")

    s.actions.add("kvm.toggle", toggle)
    s.actions.add("kvm.switch", switch, doc='move the desk to "pc" or "laptop"')

    @s.widgets.widget("kvm.switch", needs=("kvm", "kvm_ui"),
                      options=(Option("pc_label", str, "This PC"), Option("laptop_label", str, "Laptop")),
                      doc="both machines, the active one lit; a tap moves the desk to the other one")
    def key(ctx):
        k, u = ctx.snap.kvm, ctx.snap.kvm_ui or KvmUi()
        here = getattr(k, "here", None) or "pc"          # where the monitors are
        usb = getattr(k, "usb", None)
        note = "ACTIVE" if usb in (None, here) else ("USB still on PC" if usb == "pc" else "USB on laptop")
        shown = u.message if u.until >= ctx.snap.now else ""
        tile = SwitchTile((ctx.options["pc_label"], "lucide:monitor"), (ctx.options["laptop_label"], "lucide:laptop"),
                          {"pc": "left", "laptop": "right"}[here], busy=u.busy, message=shown,
                          message_color=RED if shown and u.error else AMBER, active_note=note)
        return Key(tile, None if u.busy else Do("kvm.toggle"))   # the target is decided at press time


def _short(text: str, n: int = 34) -> str:
    text = text.split(";")[0].strip()
    return text if len(text) <= n else text[: n - 1] + "…"
