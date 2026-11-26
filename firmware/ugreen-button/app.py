"""UGREEN switch controller (MicroPython, ESP32-S3).

Presses the UGREEN switch's button and reads its two indicator LEDs, so it always knows which
computer is active, whether the switch was changed by hand, by this device, or at power-up.

HTTP API on the local network (port 80):

  GET  /                       phone page with "PC" / "Laptop" buttons (no secrets in it)
  GET  /status                 state, LEDs, Wi-Fi, version                        (no auth)
  POST /select?to=laptop       switch to that computer: presses only if needed,
                               then confirms via the LEDs                         (auth)
  POST /press?ms=150           raw button press                                   (auth)
  POST /config  {json}         change non-secret settings, then reboot            (auth)
  POST /update  <app.py>       replace this file, reboot; boot.py rolls back
                               automatically if the new version doesn't come up    (auth)

Auth: header "Authorization: Bearer <token>". The token and Wi-Fi credentials live only in
config.json on the device (written over USB by scripts/ugreen_provision.py).

Wiring settings (config.json, changeable with POST /config):
  pin           GPIO driving the button circuit (default 4)
  drive         "opto": pin HIGH presses (PC817 optocoupler / NPN; safe for any button voltage)
                "direct": pin LOW presses (only if the button's high side is 3.3 V or less)
  press_ms      default press length (default 150)
  led_pins      GPIOs reading the switch's LEDs: [led1, led2], or [led1] (default [] = not wired)
  led_on_level  1 if the sensed pad reads HIGH when its LED is lit, 0 if LOW (default 1)
  led_pull      "none" | "up" | "down" internal pull on the LED inputs (default "none"; digital mode only)
  led_mode      "digital": read the pad as a logic level (needs >= ~2.5 V for "high")
                "adc": measure the pad's voltage and compare to led_threshold_mv (for pads like a
                lit LED's anode at ~2 V). ADC1 pins only (GPIO 1-10), because ADC2 stops working
                while Wi-Fi is on. Max 3.1 V on the pin. (default "digital")
  led_threshold_mv  "adc" mode: above this = high (default 1000)
  host_names    names for host 1 and host 2, matching LED 1 and LED 2 (default ["pc", "laptop"])
  cpu_mhz       80 | 160 | 240 CPU clock (default 80); lower runs cooler
  wifi_power_save  true: radio sleeps between router beacons (much cooler, adds ~0.1-0.3 s to
                the first request after idle); false: radio always on, ~0.2 s faster but
                ~20 C hotter (default true)

/status also reports the chip's internal temperature (temp_c; approximate, +-few degrees).

The button pin floats (input, high-Z) when idle and at boot, so it never presses by accident.
"""
import asyncio
import json
import os
import time

import machine
import network

try:
    import esp32
except ImportError:
    esp32 = None

VERSION = "4"
CONFIG_FILE = "config.json"
PENDING_FILE = "app_pending"  # present after an update until the new version proves it works
PRESS_MIN_MS, PRESS_MAX_MS = 20, 2000
MIN_GAP_MS = 500              # ignore raw presses closer together than this
SWITCH_TIMEOUT_MS = 3000      # how long the LEDs may take to show a switch
MAX_HEADER_BYTES = 2048
MAX_BODY_BYTES = 64 * 1024
SAFE_PINS = (1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 21, 38, 39, 40, 41, 42, 43, 44, 47, 48)
ADC1_PINS = (1, 2, 4, 5, 6, 7, 8, 9, 10)  # GPIO 3 is a strapping pin; ADC2 doesn't work with Wi-Fi on
PUBLIC_KEYS = ("pin", "drive", "press_ms", "led_pins", "led_on_level", "led_pull", "led_mode",
               "led_threshold_mv", "host_names", "cpu_mhz", "wifi_power_save")
DEFAULTS = {"pin": 4, "drive": "opto", "press_ms": 150, "hostname": "ugreen-switch", "port": 80,
            "led_pins": [], "led_on_level": 1, "led_pull": "none", "led_mode": "digital",
            "led_threshold_mv": 1000, "host_names": ["pc", "laptop"], "cpu_mhz": 80,
            "wifi_power_save": True}


# ---- config -----------------------------------------------------------------

def validate(cfg):
    """Raise ValueError if a public setting is unusable."""
    if cfg["pin"] not in SAFE_PINS:
        raise ValueError("pin must be one of %s" % (SAFE_PINS,))
    if cfg["drive"] not in ("opto", "direct"):
        raise ValueError("drive must be 'opto' or 'direct'")
    if not (isinstance(cfg["press_ms"], int) and PRESS_MIN_MS <= cfg["press_ms"] <= PRESS_MAX_MS):
        raise ValueError("press_ms must be %d-%d" % (PRESS_MIN_MS, PRESS_MAX_MS))
    leds = cfg["led_pins"]
    if not isinstance(leds, list) or len(leds) > 2 or any(p not in SAFE_PINS or p == cfg["pin"] for p in leds):
        raise ValueError("led_pins must be a list of up to 2 safe GPIOs, different from pin")
    if len(set(leds)) != len(leds):
        raise ValueError("led_pins must be different GPIOs")
    if cfg["led_on_level"] not in (0, 1):
        raise ValueError("led_on_level must be 0 or 1")
    if cfg["led_pull"] not in ("none", "up", "down"):
        raise ValueError("led_pull must be none, up or down")
    if cfg["led_mode"] not in ("digital", "adc"):
        raise ValueError("led_mode must be digital or adc")
    if cfg["led_mode"] == "adc" and any(p not in ADC1_PINS for p in leds):
        raise ValueError("adc mode needs ADC1 pins: %s" % (ADC1_PINS,))
    if not (isinstance(cfg["led_threshold_mv"], int) and 100 <= cfg["led_threshold_mv"] <= 3000):
        raise ValueError("led_threshold_mv must be 100-3000")
    names = cfg["host_names"]
    if not (isinstance(names, list) and len(names) == 2 and all(isinstance(n, str) and n for n in names)
            and names[0] != names[1]):
        raise ValueError("host_names must be two different names")
    if cfg["cpu_mhz"] not in (80, 160, 240):
        raise ValueError("cpu_mhz must be 80, 160 or 240")
    if cfg["wifi_power_save"] not in (True, False):
        raise ValueError("wifi_power_save must be true or false")


def load_config():
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    for key in ("ssid", "password", "token"):
        if not cfg.get(key):
            raise ValueError("config.json is missing " + key)
    for key, value in DEFAULTS.items():
        cfg.setdefault(key, value)
    validate(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE + ".tmp", "w") as f:
        json.dump(cfg, f)
    os.rename(CONFIG_FILE + ".tmp", CONFIG_FILE)


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def same_secret(a, b):
    """Compare without stopping at the first mismatch."""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


# ---- hardware -----------------------------------------------------------------

class Button:
    def __init__(self, pin, drive):
        self._pressed_level = 1 if drive == "opto" else 0
        self._pin = machine.Pin(pin, machine.Pin.IN)  # floating = released
        self._busy = False
        self._last = time.ticks_add(time.ticks_ms(), -MIN_GAP_MS)
        self.presses = 0

    @property
    def busy(self):
        return self._busy

    async def press(self, ms):
        if self._busy or time.ticks_diff(time.ticks_ms(), self._last) < MIN_GAP_MS:
            return False
        self._busy = True
        try:
            self._pin.init(machine.Pin.OUT, value=self._pressed_level)
            await asyncio.sleep_ms(ms)
        finally:
            self._pin.init(machine.Pin.IN)
            self._last = time.ticks_ms()
            self._busy = False
        self.presses += 1
        return True


class Leds:
    """Reads the switch's indicator LEDs. Samples briefly so PWM-dimmed LEDs still count as lit:
    digital mode counts how often the pad is at the "lit" level; adc mode averages the voltage."""

    SAMPLES, SAMPLE_GAP_US, LIT_FRACTION = 12, 400, 0.2

    def __init__(self, pins, on_level, pull, names, mode="digital", threshold_mv=1000):
        self._adc = mode == "adc"
        if self._adc:
            self._pins = [machine.ADC(machine.Pin(p), atten=machine.ADC.ATTN_11DB) for p in pins]
        else:
            pull_mode = {"none": None, "up": machine.Pin.PULL_UP, "down": machine.Pin.PULL_DOWN}[pull]
            self._pins = [machine.Pin(p, machine.Pin.IN, pull_mode) for p in pins]
        self._on = on_level
        self._threshold_uv = threshold_mv * 1000
        self._names = names
        self.mv = []  # last measured voltages (adc mode), for diagnosing the wiring

    @property
    def wired(self):
        return bool(self._pins)

    def read(self):
        counts = [0] * len(self._pins)
        for _ in range(self.SAMPLES):
            for i, p in enumerate(self._pins):
                if self._adc:
                    counts[i] += p.read_uv()
                elif p.value() == self._on:
                    counts[i] += 1
            time.sleep_us(self.SAMPLE_GAP_US)
        if not self._adc:
            return [c >= self.SAMPLES * self.LIT_FRACTION for c in counts]
        avg_uv = [c // self.SAMPLES for c in counts]
        self.mv = [uv // 1000 for uv in avg_uv]
        return [(uv >= self._threshold_uv) == (self._on == 1) for uv in avg_uv]

    def active(self, lit):
        """Which host is selected, or 'unknown' / 'none' / 'both'."""
        if not lit:
            return "unknown"
        if len(lit) == 1:  # only LED 1 wired: lit = host 1, dark = host 2
            return self._names[0] if lit[0] else self._names[1]
        if lit[0] and lit[1]:
            return "both"
        if lit[0]:
            return self._names[0]
        if lit[1]:
            return self._names[1]
        return "none"  # switch unpowered, or in the middle of switching


def chip_temp():
    try:
        return round(esp32.mcu_temperature(), 1)
    except (AttributeError, OSError):
        return None


# ---- app ------------------------------------------------------------------------

class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.button = Button(cfg["pin"], cfg["drive"])
        self.leds = Leds(cfg["led_pins"], cfg["led_on_level"], cfg["led_pull"], cfg["host_names"],
                         cfg["led_mode"], cfg["led_threshold_mv"])
        self.wlan = network.WLAN(network.STA_IF)
        self.started = time.ticks_ms()
        self.lit = []
        self.active = "unknown"
        self.changes = 0
        self.changed_at = None
        self.switch_lock = asyncio.Lock()
        self.server_up = False

    # ---- background tasks ----
    async def keep_wifi(self):
        network.hostname(self.cfg["hostname"])
        self.wlan.active(True)
        try:
            # PM_NONE: radio always on (lowest latency, warmest); PM_PERFORMANCE: modem sleep
            self.wlan.config(pm=network.WLAN.PM_PERFORMANCE if self.cfg["wifi_power_save"] else network.WLAN.PM_NONE)
        except (AttributeError, ValueError):
            pass
        was_up = False
        while True:
            if not self.wlan.isconnected():
                if was_up:
                    print("wifi: lost connection")
                    was_up = False
                try:
                    self.wlan.connect(self.cfg["ssid"], self.cfg["password"])
                except OSError as e:
                    print("wifi: connect error", e)
                for _ in range(20):  # wait up to 10 s
                    if self.wlan.isconnected():
                        break
                    await asyncio.sleep_ms(500)
            if self.wlan.isconnected() and not was_up:
                was_up = True
                print("wifi: connected, ip", self.wlan.ifconfig()[0], "host", self.cfg["hostname"] + ".local")
            await asyncio.sleep(5)

    async def watch_leds(self):
        if not self.leds.wired:
            return
        candidate, seen = None, 0
        while True:
            lit = self.leds.read()
            state = self.leds.active(lit)
            self.lit = lit
            if state == self.active:
                candidate, seen = None, 0
            elif state == candidate:
                seen += 1
                if seen >= 2:  # stable for 3 reads (~150 ms): accept
                    print("leds: %s -> %s" % (self.active, state))
                    if self.active != "unknown":  # the first reading after boot isn't a change
                        self.changes += 1
                        self.changed_at = time.ticks_ms()
                    self.active = state
                    candidate, seen = None, 0
            else:
                candidate, seen = state, 0
            await asyncio.sleep_ms(50)

    async def confirm_update(self):
        """After an update, delete the pending marker once Wi-Fi and the server work; otherwise reboot
        so boot.py restores the previous version."""
        if not exists(PENDING_FILE):
            return
        deadline = time.ticks_add(time.ticks_ms(), 90000)
        while not (self.wlan.isconnected() and self.server_up):
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                print("update: new version never got online, rebooting to roll back")
                machine.reset()
            await asyncio.sleep(1)
        await asyncio.sleep(10)
        os.remove(PENDING_FILE)
        print("update: version", VERSION, "confirmed")

    # ---- actions ----
    async def select(self, target):
        names = self.cfg["host_names"]
        if target not in names:
            return 400, {"ok": False, "error": "to must be one of %s" % names}
        if not self.leds.wired:
            return 409, {"ok": False, "error": "LEDs not wired/configured, state unknown; use /press"}
        async with self.switch_lock:
            if self.active == target:
                return 200, {"ok": True, "active": target, "changed": False}
            if self.active not in names:
                return 409, {"ok": False, "error": "switch state is '%s', not pressing blindly" % self.active}
            base = self.cfg["press_ms"]
            for attempt, ms in enumerate((base, min(base * 2, PRESS_MAX_MS)), 1):
                while not await self.button.press(ms):  # wait out the minimum gap between presses
                    await asyncio.sleep_ms(100)
                deadline = time.ticks_add(time.ticks_ms(), SWITCH_TIMEOUT_MS)
                while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                    if self.active == target:
                        return 200, {"ok": True, "active": target, "changed": True, "attempts": attempt}
                    await asyncio.sleep_ms(50)
                if self.active not in names:  # e.g. switch lost power: don't keep pressing
                    break
            return 504, {"ok": False, "error": "switch did not change", "active": self.active}

    def status(self):
        up = self.wlan.isconnected()
        return {
            "ok": True, "version": VERSION, "name": self.cfg["hostname"],
            "active": self.active, "leds": self.lit, "led_mv": self.leds.mv, "host_names": self.cfg["host_names"],
            "changes": self.changes,
            "changed_s_ago": time.ticks_diff(time.ticks_ms(), self.changed_at) // 1000 if self.changed_at else None,
            "ip": self.wlan.ifconfig()[0] if up else None,
            "rssi": self.wlan.status("rssi") if up else None,
            "uptime_s": time.ticks_diff(time.ticks_ms(), self.started) // 1000,
            "temp_c": chip_temp(), "cpu_mhz": machine.freq() // 1000000,
            "busy": self.button.busy, "presses": self.button.presses,
            "settings": {k: self.cfg[k] for k in PUBLIC_KEYS},
        }

    def update_config(self, body):
        changes = json.loads(body)
        if not isinstance(changes, dict) or not changes:
            raise ValueError("send a JSON object of settings")
        unknown = [k for k in changes if k not in PUBLIC_KEYS]
        if unknown:
            raise ValueError("can't change %s here; allowed: %s" % (unknown, PUBLIC_KEYS))
        new = dict(self.cfg)
        new.update(changes)
        validate(new)
        save_config(new)

    def install_update(self, body):
        if not body:
            raise ValueError("empty file")
        src = body.decode()
        if "def main(" not in src:
            raise ValueError("doesn't look like app.py")
        try:
            compile(src, "app.py", "exec")  # catches syntax errors before we swap
        except NameError:  # compile() not built into this firmware: boot.py rollback still protects us
            pass
        with open("app_new.py", "w") as f:
            f.write(src)
        if exists("app_prev.py"):
            os.remove("app_prev.py")
        os.rename("app.py", "app_prev.py")
        os.rename("app_new.py", "app.py")
        with open(PENDING_FILE, "w") as f:
            f.write("0")

    async def reboot_soon(self):
        await asyncio.sleep_ms(500)  # let the HTTP reply go out first
        machine.reset()

    # ---- HTTP ----
    async def handle(self, reader, writer):
        code, body, ctype = 500, {"ok": False, "error": "internal"}, "application/json"
        try:
            request = await asyncio.wait_for(reader.readline(), 5)
            parts = request.decode().split()
            if len(parts) < 2:
                raise ValueError("bad request")
            method, target = parts[0], parts[1]
            headers, size = {}, len(request)
            while True:
                line = await asyncio.wait_for(reader.readline(), 5)
                size += len(line)
                if size > MAX_HEADER_BYTES:
                    raise ValueError("headers too large")
                if line in (b"\r\n", b"\n", b""):
                    break
                name, _, value = line.decode().partition(":")
                headers[name.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            if not 0 <= length <= MAX_BODY_BYTES:
                raise ValueError("body too large")
            data = b""
            while len(data) < length:
                chunk = await asyncio.wait_for(reader.read(length - len(data)), 10)
                if not chunk:
                    raise ValueError("body cut short")
                data += chunk
            path, _, query = target.partition("?")
            params = {}
            for p in query.split("&"):
                k, _, v = p.partition("=")
                if k:
                    params[k] = v
            code, body, ctype = await self.route(method, path, params, headers, data)
        except Exception as e:  # never let one bad request kill the server
            code, body, ctype = 400, {"ok": False, "error": str(e)}, "application/json"
        try:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            head = "HTTP/1.1 %d OK\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
            writer.write((head % (code, ctype, len(payload))).encode() + payload)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def authorized(self, headers):
        auth = headers.get("authorization", "")
        return auth.startswith("Bearer ") and same_secret(auth[7:], self.cfg["token"])

    async def route(self, method, path, params, headers, data):
        js = "application/json"
        if method == "GET" and path == "/":
            return 200, PAGE, "text/html; charset=utf-8"
        if method == "GET" and path == "/status":
            return 200, self.status(), js
        if method != "POST" or path not in ("/select", "/press", "/config", "/update"):
            return 404, {"ok": False, "error": "not found"}, js
        if not self.authorized(headers):
            await asyncio.sleep_ms(300)  # slow down guessing
            return 401, {"ok": False, "error": "unauthorized"}, js

        if path == "/select":
            code, body = await self.select(params.get("to", ""))
            print("select", params.get("to"), "->", code, body.get("active"))
            return code, body, js
        if path == "/press":
            ms = int(params.get("ms", self.cfg["press_ms"]))
            if not PRESS_MIN_MS <= ms <= PRESS_MAX_MS:
                return 400, {"ok": False, "error": "ms must be %d-%d" % (PRESS_MIN_MS, PRESS_MAX_MS)}, js
            if not await self.button.press(ms):
                return 409, {"ok": False, "error": "busy, try again in a moment"}, js
            print("button: pressed for", ms, "ms")
            return 200, {"ok": True, "ms": ms, "presses": self.button.presses}, js
        if path == "/config":
            self.update_config(data.decode())
            asyncio.create_task(self.reboot_soon())
            return 200, {"ok": True, "rebooting": True}, js
        # /update
        self.install_update(data)
        asyncio.create_task(self.reboot_soon())
        return 200, {"ok": True, "rebooting": True, "bytes": len(data)}, js

    async def run(self):
        asyncio.create_task(self.keep_wifi())
        asyncio.create_task(self.watch_leds())
        asyncio.create_task(self.confirm_update())
        await asyncio.start_server(self.handle, "0.0.0.0", self.cfg["port"])
        self.server_up = True
        print("http: listening on port", self.cfg["port"])
        while True:
            await asyncio.sleep(3600)


PAGE = b"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>USB Switch</title>
<style>
:root{--bg:#f4f4f5;--fg:#18181b;--card:#fff;--muted:#71717a;--on:#16a34a;--btn:#e4e4e7}
@media (prefers-color-scheme:dark){:root{--bg:#09090b;--fg:#fafafa;--card:#18181b;--muted:#a1a1aa;--on:#22c55e;--btn:#27272a}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px system-ui,sans-serif;display:flex;justify-content:center}
main{width:100%;max-width:420px;padding:24px 16px}
h1{font-size:20px;margin:0 0 4px}p{color:var(--muted);margin:0 0 20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
button{border:0;border-radius:16px;padding:32px 8px;font-size:20px;font-weight:600;background:var(--btn);color:var(--fg)}
button.active{background:var(--on);color:#fff}button:disabled{opacity:.6}
a{color:var(--muted);font-size:14px}
</style></head><body><main>
<h1>USB Switch</h1><p id="st">Loading...</p><div class="grid" id="g"></div>
<p style="margin-top:20px"><a href="#" id="forget">Forget token</a></p>
</main><script>
const K='ugreenToken',st=document.getElementById('st'),g=document.getElementById('g');
let names=[],busy=false;
function token(){let t=localStorage.getItem(K);if(!t){t=prompt('Access token (from the PC config):');if(t)localStorage.setItem(K,t.trim())}return localStorage.getItem(K)}
async function refresh(){if(busy)return;try{const s=await(await fetch('/status')).json();
if(names.join()!==s.host_names.join()){names=s.host_names;g.innerHTML='';for(const n of names){const b=document.createElement('button');b.textContent=n[0].toUpperCase()+n.slice(1);b.dataset.n=n;b.onclick=()=>go(n);g.appendChild(b)}}
for(const b of g.children)b.classList.toggle('active',b.dataset.n===s.active);
st.textContent='Active: '+s.active}catch(e){st.textContent='Offline'}}
async function go(n){const t=token();if(!t)return;busy=true;for(const b of g.children)b.disabled=true;st.textContent='Switching to '+n+'...';
try{const r=await fetch('/select?to='+n,{method:'POST',headers:{Authorization:'Bearer '+t}});const j=await r.json();
if(r.status===401){localStorage.removeItem(K);st.textContent='Wrong token'}else st.textContent=j.ok?'Active: '+j.active:'Error: '+j.error}
catch(e){st.textContent='Offline'}busy=false;for(const b of g.children)b.disabled=false;setTimeout(refresh,300)}
document.getElementById('forget').onclick=e=>{e.preventDefault();localStorage.removeItem(K);st.textContent='Token forgotten'};
refresh();setInterval(refresh,2000);
</script></body></html>"""


def main():
    try:
        cfg = load_config()
    except (OSError, ValueError) as e:
        print("Not configured (" + str(e) + "). Run scripts/ugreen_provision.py from the PC.")
        return
    machine.freq(cfg["cpu_mhz"] * 1000000)
    print("ugreen-switch v%s: button GPIO %d (%s), LEDs %s" % (VERSION, cfg["pin"], cfg["drive"], cfg["led_pins"] or "not wired"))
    asyncio.run(App(cfg).run())
