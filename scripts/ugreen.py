"""Control the ESP32 UGREEN switch controller over the local network. Standard library only.

  python ugreen.py status                      which computer is active, LEDs, Wi-Fi, settings
  python ugreen.py select laptop               switch to a computer (presses only if needed, confirms via LEDs)
  python ugreen.py press [--ms 300]            raw button press
  python ugreen.py set led_pins=[5,6] led_on_level=0     change wiring settings (device reboots)
  python ugreen.py update                      upload firmware/ugreen-button/app.py over Wi-Fi
  python ugreen.py page                        print the phone page URL and the token to enter there

Reads the token and hostname from %APPDATA%\\ugreen-switch\\config.json (written by ugreen_provision.py)
and remembers the ESP32's last IP there, so it doesn't depend on .local name lookup every time.
Exit code 0 on success, 1 on any error.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PC_CONFIG = Path(os.environ["APPDATA"]) / "ugreen-switch" / "config.json"
APP_FILE = Path(__file__).resolve().parent.parent / "firmware" / "ugreen-button" / "app.py"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # LAN device: never use a proxy


class Unreachable(Exception):
    pass


def load_config() -> dict:
    if not PC_CONFIG.exists():
        sys.exit(f"No config at {PC_CONFIG}. Run ugreen_provision.py first.")
    return json.loads(PC_CONFIG.read_text())


def call(cfg: dict, method: str, path: str, data: bytes | None = None, auth: bool = False,
         timeout: float = 3, content_type: str = "application/octet-stream") -> dict:
    hosts = [h for h in (cfg.get("ip"), cfg["hostname"] + ".local") if h]
    last_error = None
    for host in hosts:
        req = urllib.request.Request(f"http://{host}:{cfg['port']}{path}", method=method,
                                     data=(data or b"") if method == "POST" else None)
        req.add_header("Content-Type", content_type)
        if auth:
            req.add_header("Authorization", "Bearer " + cfg["token"])
        try:
            with _opener.open(req, timeout=timeout) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:  # the device answered with an error: report it, don't try other hosts
            body = json.load(e)
        except OSError as e:
            last_error = e
            continue
        if body.get("ip") and body["ip"] != cfg.get("ip"):
            cfg["ip"] = body["ip"]
            PC_CONFIG.write_text(json.dumps(cfg, indent=2))
        return body
    raise Unreachable(f"Can't reach the ESP32 at {', '.join(hosts)}: {last_error}")


def wait_back(cfg: dict, seconds: float = 60) -> dict:
    """Wait for the device to reboot and answer again."""
    time.sleep(3)
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            return call(cfg, "GET", "/status", timeout=2)
        except Unreachable:
            time.sleep(1)
    raise Unreachable(f"Device didn't come back within {seconds:.0f} s")


def parse_setting(item: str) -> tuple[str, object]:
    key, sep, raw = item.partition("=")
    if not sep:
        sys.exit(f"Settings look like key=value, got {item!r}")
    try:
        return key, json.loads(raw)   # numbers, lists, true/false
    except json.JSONDecodeError:
        return key, raw               # plain strings like drive=direct


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sel = sub.add_parser("select")
    sel.add_argument("target", help="host name, e.g. pc or laptop")
    press = sub.add_parser("press")
    press.add_argument("--ms", type=int)
    st = sub.add_parser("set")
    st.add_argument("settings", nargs="+", help="key=value, e.g. led_pins=[5,6]")
    up = sub.add_parser("update")
    up.add_argument("file", nargs="?", type=Path, default=APP_FILE)
    sub.add_parser("page")
    args = ap.parse_args()

    cfg = load_config()
    try:
        if args.cmd == "status":
            body = call(cfg, "GET", "/status")
        elif args.cmd == "select":
            body = call(cfg, "POST", f"/select?to={args.target}", auth=True, timeout=10)
        elif args.cmd == "press":
            body = call(cfg, "POST", "/press" + (f"?ms={args.ms}" if args.ms else ""), auth=True)
        elif args.cmd == "set":
            changes = dict(parse_setting(s) for s in args.settings)
            body = call(cfg, "POST", "/config", json.dumps(changes).encode(), auth=True,
                        content_type="application/json")
            if body.get("ok"):
                print("Saved; device rebooting...")
                body = wait_back(cfg)
        elif args.cmd == "update":
            src = args.file.read_bytes()
            m = re.search(rb'^VERSION = "([^"]+)"', src, re.M)
            if not m:
                sys.exit(f"{args.file} has no VERSION line; bump it so the update can be verified.")
            want = m.group(1).decode()
            body = call(cfg, "POST", "/update", src, auth=True, timeout=15, content_type="text/x-python")
            if body.get("ok"):
                print(f"Uploaded {len(src)} bytes (version {want}); device rebooting...")
                body = wait_back(cfg)
                if body.get("version") != want:
                    body = {"ok": False, "error": f"device came back on version {body.get('version')}, "
                                                  f"not {want}: the new version failed and was rolled back"}
                # A working new version deletes its rollback marker ~10 s after coming online.
        else:  # page
            print(f"Phone page:  http://{cfg.get('ip') or cfg['hostname'] + '.local'}/")
            print(f"Token (enter it once on the phone): {cfg['token']}")
            return
    except Unreachable as e:
        sys.exit(str(e))
    print(json.dumps(body, indent=2))
    sys.exit(0 if body.get("ok") else 1)


if __name__ == "__main__":
    main()
