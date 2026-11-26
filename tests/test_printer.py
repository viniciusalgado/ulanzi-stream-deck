from types import SimpleNamespace

import pytest

from deck.sources import printer
from deck.sources.tuya import PowerState


def test_filament_change_beeps_once_the_hotend_reaches_200(monkeypatch):
    device = printer.Printer("http://printer:7125")
    readings = iter([(25, 0), (150, 200), (199.9, 200), (200, 200)])
    events = []

    def poll():
        nozzle = next(readings)
        events.append(nozzle)
        return printer.PrinterState(online=True, state="complete", nozzle=nozzle)

    def post(path, payload):
        assert path == "/printer/gcode/script"
        events.append(payload["script"])

    monkeypatch.setattr(device, "poll", poll)
    monkeypatch.setattr(device, "_post", post)
    monkeypatch.setattr(printer.time, "sleep", lambda _: None)

    device.filament_change()

    assert events == [(25, 0), "M104 S200", (150, 200), (199.9, 200), (200, 200), "M300 S1000 P400"]


@pytest.mark.parametrize("state", ["printing", "paused"])
def test_filament_change_checks_live_state_before_heating(monkeypatch, state):
    device = printer.Printer("http://printer:7125")
    monkeypatch.setattr(device, "poll", lambda: printer.PrinterState(online=True, state=state))
    monkeypatch.setattr(device, "_gcode", lambda _: pytest.fail("Unexpected printer command"))

    with pytest.raises(RuntimeError, match="idle and ready"):
        device.filament_change()


def test_filament_change_skips_beep_if_the_heater_target_changes(monkeypatch):
    device = printer.Printer("http://printer:7125")
    monkeypatch.setattr(device, "poll", lambda: printer.PrinterState(online=True, state="standby", nozzle=(200, 0)))
    commands = []
    monkeypatch.setattr(device, "_gcode", commands.append)

    with pytest.raises(RuntimeError, match="heating was interrupted"):
        device.filament_change()

    assert commands == ["M104 S200"]


def powered_printer(monkeypatch, readings, power_state):
    events = []
    switch = SimpleNamespace(poll=lambda: power_state, set_power=lambda on: events.append(("relay", on)))
    device = printer.Printer("http://printer:7125", power=switch)
    states = iter(readings)
    monkeypatch.setattr(device, "poll", lambda: next(states))
    monkeypatch.setattr(device, "_gcode", events.append)
    monkeypatch.setattr(device, "_get", lambda _: {"queue_state": "paused", "queued_jobs": []})
    monkeypatch.setattr(printer.time, "sleep", lambda _: None)
    return device, events


def test_filament_change_powers_on_then_waits_for_klipper(monkeypatch):
    offline = printer.PrinterState()
    starting = printer.PrinterState(online=True, state="startup")
    idle = printer.PrinterState(online=True, state="standby")
    ready = printer.PrinterState(online=True, state="standby", nozzle=(200, 200))
    device, events = powered_printer(monkeypatch, [offline, starting, idle, idle, ready], PowerState.OFF)

    device.filament_change()

    assert events == [("relay", True), "M104 S200", "M300 S1000 P400"]


def test_filament_change_recovers_usb_mcu_after_power_on(monkeypatch):
    disconnected = printer.PrinterState(online=True, state="shutdown", message="Lost communication with MCU 'mcu'")
    idle = printer.PrinterState(online=True, state="standby")
    ready = printer.PrinterState(online=True, state="standby", nozzle=(200, 200))
    device, events = powered_printer(monkeypatch, [disconnected, disconnected, disconnected, idle, idle, ready],
                                     PowerState.OFF)
    monkeypatch.setattr(printer, "USB_SETTLE_TIME", 0)
    monkeypatch.setattr(device, "_post", events.append)

    device.filament_change()

    assert events == [("relay", True), "/printer/firmware_restart", "M104 S200", "M300 S1000 P400"]


def test_power_on_recovers_klipper_without_heating_and_is_idempotent(monkeypatch):
    disconnected = printer.PrinterState(online=True, state="shutdown", message="Lost communication with MCU 'mcu'")
    idle = printer.PrinterState(online=True, state="standby")
    device, events = powered_printer(monkeypatch, [disconnected, idle], PowerState.OFF)
    monkeypatch.setattr(printer, "USB_SETTLE_TIME", 0)
    monkeypatch.setattr(device, "_post", events.append)

    device.power_on()
    monkeypatch.setattr(device.power, "poll", lambda: PowerState.ON)
    device.power_on()

    assert events == [("relay", True), "/printer/firmware_restart"]


@pytest.mark.parametrize("during_cooldown", ["standby", "printing", "paused", "offline"])
def test_power_off_waits_for_cooldown_and_rechecks_job_state(monkeypatch, during_cooldown):
    hot = printer.PrinterState(online=True, state="standby", nozzle=(200, 200))
    cooling = printer.PrinterState(online=True, state="standby", nozzle=(60, 0))
    last = printer.PrinterState(online=during_cooldown != "offline", state=during_cooldown, nozzle=(50, 0))
    device, events = powered_printer(monkeypatch, [hot, cooling, last], PowerState.ON)

    if during_cooldown == "standby":
        device.power_off()
        assert events == ["TURN_OFF_HEATERS", ("relay", False)]
    else:
        with pytest.raises(RuntimeError, match="idle and ready"):
            device.power_off()
        assert events == ["TURN_OFF_HEATERS"]


def test_power_off_rejects_a_stale_idle_button(monkeypatch):
    device, events = powered_printer(monkeypatch, [printer.PrinterState(online=True, state="paused")], PowerState.ON)

    with pytest.raises(RuntimeError, match="idle and ready"):
        device.power_off()

    assert events == []
