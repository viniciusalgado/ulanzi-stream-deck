"""Set up the ESP32 UGREEN switch controller over USB.

Full setup: writes config.json (Wi-Fi + access token) and the firmware (boot.py, main.py, app.py),
then resets the board.

  python ugreen_provision.py --port COM4 --ssid MyWiFi --password-file <file>

Code only (keeps the Wi-Fi config already on the board):

  python ugreen_provision.py --port COM4 --code-only

The Wi-Fi password is read from a file (never a command-line argument, so it doesn't show up in
process lists or shell history). It's copied to the ESP32 through a temporary file that is deleted
right after, and it's never written to this repo or the PC-side config.

The PC-side config (token + hostname, no Wi-Fi password) goes to %APPDATA%\\ugreen-switch\\config.json,
where scripts/ugreen.py reads it. Once the board is soldered in (and must not be plugged into USB),
use `ugreen.py set ...` and `ugreen.py update` over Wi-Fi instead.

Needs mpremote in the same Python environment (pip install mpremote).
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIRMWARE_DIR = REPO / "firmware" / "ugreen-button"
FIRMWARE_FILES = ("boot.py", "app.py", "main.py")  # main.py last: it's what runs at boot
PC_CONFIG = Path(os.environ["APPDATA"]) / "ugreen-switch" / "config.json"


def mpremote(port: str, *commands: list[str]) -> None:
    args = [sys.executable, "-m", "mpremote", "connect", port]
    for i, cmd in enumerate(commands):
        args += (["+"] if i else []) + cmd
    subprocess.run(args, check=True)


def copy_code_commands() -> list[list[str]]:
    return [["cp", str(FIRMWARE_DIR / name), ":" + name] for name in FIRMWARE_FILES]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="ESP32 serial port, e.g. COM4")
    ap.add_argument("--code-only", action="store_true", help="only copy the firmware files, keep config.json")
    ap.add_argument("--ssid")
    ap.add_argument("--password-file", type=Path, help="file whose content is the Wi-Fi password")
    ap.add_argument("--pin", type=int, default=4, help="GPIO wired to the button circuit (default 4)")
    ap.add_argument("--drive", choices=("opto", "direct"), default="opto")
    ap.add_argument("--press-ms", type=int, default=150)
    ap.add_argument("--hostname", default="ugreen-switch")
    ap.add_argument("--new-token", action="store_true", help="generate a new access token instead of reusing the saved one")
    args = ap.parse_args()

    if args.code_only:
        mpremote(args.port, *copy_code_commands(), ["reset"])
        print("Done. Firmware updated; Wi-Fi config on the board unchanged.")
        return
    if not (args.ssid and args.password_file):
        sys.exit("--ssid and --password-file are required (or use --code-only).")

    password = args.password_file.read_text(encoding="utf-8").rstrip("\r\n")
    if not 8 <= len(password) <= 63:
        sys.exit("Wi-Fi password should be 8-63 characters (WPA2). Nothing was written.")

    pc_cfg = json.loads(PC_CONFIG.read_text()) if PC_CONFIG.exists() else {}
    token = pc_cfg.get("token") if pc_cfg.get("token") and not args.new_token else secrets.token_urlsafe(32)

    device_cfg = {
        "ssid": args.ssid, "password": password, "token": token,
        "pin": args.pin, "drive": args.drive, "press_ms": args.press_ms,
        "hostname": args.hostname, "port": 80,
    }

    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(device_cfg, f)
        mpremote(args.port, ["cp", tmp, ":config.json"], *copy_code_commands(), ["reset"])
    finally:
        os.remove(tmp)

    pc_cfg.update({"token": token, "hostname": args.hostname, "port": 80})
    PC_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    PC_CONFIG.write_text(json.dumps(pc_cfg, indent=2))
    print(f"Done. ESP32 joins '{args.ssid}' as {args.hostname}.local (GPIO {args.pin}, drive={args.drive}).")
    print(f"PC config (token, no Wi-Fi password): {PC_CONFIG}")


if __name__ == "__main__":
    main()
