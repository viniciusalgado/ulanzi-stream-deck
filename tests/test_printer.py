import pytest

from deck.sources import printer


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
