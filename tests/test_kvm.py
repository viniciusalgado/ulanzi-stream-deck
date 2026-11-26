from deck.sources.kvm import Kvm, KvmState, MonitorInput

import pytest


class FakeDdc:
    def __init__(self, missing=()):
        self.calls, self.missing = [], set(missing)

    def models(self):
        return ["27G2", "24G2HE5"]

    def set_inputs(self, codes):
        self.calls.append(dict(codes))
        return {m: m not in self.missing for m in codes}


class FakeEsp:
    def __init__(self, hid, active="unknown", wired=True):
        self.hid, self.active, self.wired, self.calls = hid, active, wired, []

    def call(self, method, path, timeout=4):
        self.calls.append((method, path))
        if path == "/status":
            return {"ok": True, "active": self.active}
        if path == "/press":
            if self.wired:
                self.hid.here = not self.hid.here   # the UGREEN flips to the other computer
            return {"ok": True}
        if path.startswith("/select"):
            self.active = path.split("=")[1]
            return {"ok": True, "active": self.active}
        raise AssertionError(path)


class FakeHid:
    def __init__(self, here):
        self.here = here

    def enumerate(self, vid, pid):
        return [{}] if self.here else []


def make(tmp_path, here=True, active="unknown", missing=(), wired=True):
    hid = FakeHid(here)
    esp = FakeEsp(hid, active, wired)
    ddc = FakeDdc(missing)
    kvm = Kvm([MonitorInput("27G2", 0x0F, 0x12), MonitorInput("24G2HE5", 0x11, 0x01)], [(0x04D9, 0xA231)], esp,
              ddc=ddc, hid_module=hid, sleep=lambda s: None, side_file=tmp_path / "side.txt")
    return kvm, ddc, esp, hid


def test_switch_moves_both_monitors_at_once_and_touches_nothing_else(tmp_path):
    kvm, ddc, esp, hid = make(tmp_path)
    kvm.switch("laptop")
    assert ddc.calls == [{"27G2": 0x12, "24G2HE5": 0x01}]
    assert esp.calls == [] and hid.here                    # USB is a separate, later step
    assert kvm.poll().here == "laptop"


def test_desk_side_follows_the_monitors_even_when_usb_stays(tmp_path):
    kvm, ddc, esp, hid = make(tmp_path, wired=False)
    kvm.switch("laptop")
    assert kvm.switch_usb("laptop") == "USB switch didn't follow"
    assert kvm.poll() == KvmState("laptop", "pc", "usb", True)   # so the next press goes back to the PC
    again = Kvm(kvm.monitors, kvm.presence, esp, ddc=ddc, hid_module=hid, side_file=tmp_path / "side.txt")
    assert again.poll().here == "laptop"                          # remembered across restarts


def test_usb_follows_by_pressing_once_when_wired(tmp_path):
    kvm, ddc, esp, hid = make(tmp_path, here=True)
    assert kvm.switch_usb("laptop") is None
    assert esp.calls.count(("POST", "/press")) == 1 and not hid.here
    assert kvm.switch_usb("laptop") is None and esp.calls.count(("POST", "/press")) == 1   # already there


def test_back_to_pc_retries_a_monitor_that_is_slow_to_reappear(tmp_path):
    kvm, ddc, esp, hid = make(tmp_path, missing={"27G2"})
    with pytest.raises(RuntimeError, match="27G2"):
        kvm.switch("pc")
    assert len(ddc.calls) == 6 and ddc.calls[1] == {"27G2": 0x0F}   # only the missing one is retried
    assert kvm.poll().here == "pc"                                   # the other one did move


def test_uses_led_confirmed_select_when_the_esp_knows_the_state(tmp_path):
    kvm, ddc, esp, hid = make(tmp_path, here=True, active="pc")
    assert kvm.poll() == KvmState("pc", "pc", "leds", True)
    assert kvm.switch_usb("laptop") is None
    assert ("POST", "/select?to=laptop") in esp.calls and ("POST", "/press") not in esp.calls
