"""Catch the D200's own logs during the ~5 s at boot when it exposes an adb shell (before its app takes USB over).

    .venv\\Scripts\\python scripts\\deck_bootcatch.py          wait for boots and save what the deck says

Read-only: nothing is written to the deck. Each boot gives %APPDATA%\\ulanzi-deck\\bootlogs\\<time>-stage1.txt, and
if the deck is still in that stage 15 s later (= stuck: its app never started), a second dump <time>-stuck.txt.
Why: the deck sometimes never leaves that stage (2026-11-25, twice); the cause is inside the deck.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(os.environ["APPDATA"]) / "ulanzi-deck"
ADB = STATE / "tools" / "platform-tools" / "adb.exe"
OUT = STATE / "bootlogs"
NO_WINDOW = 0x08000000

# Most valuable first: the window can close at any moment, and output is streamed to the file as it comes.
DUMP = r"""
echo "== previous boot (what the on-device logger saw after the window closed)"
cat /data/ulz-boot.log 2>&1; mv /data/ulz-boot.log /data/ulz-boot.prev.log 2>/dev/null
echo "== start logger for this boot"
busybox nohup busybox setsid sh -c 'i=0; while [ $i -lt 12 ]; do busybox sleep 5; { echo "### t=$(cat /proc/uptime)"; ps; busybox free; busybox dmesg | busybox tail -n 150; logcat -d -v time | busybox tail -n 300; } > /data/ulz-boot.tmp 2>&1; mv /data/ulz-boot.tmp /data/ulz-boot.log; sync; i=$((i+1)); done' >/dev/null 2>&1 &
echo "logger started"
echo "== dmesg"; busybox dmesg
echo "== init.rc"; cat /etc/init.rc
echo "== ssd_init.sh"; cat /bin/ssd_init.sh
echo "== vold.fstab"; cat /etc/vold.fstab
echo "== build.prop"; cat /etc/build.prop /etc/default.prop
echo "== res"; ls -la /res/bin /res/etc /res/lib /res/ui 2>&1; cat /res/etc/* 2>&1 | busybox head -c 4000
echo "== uptime"; cat /proc/uptime; date
echo "== prefs"; cat /data/preferences.json; echo
echo "== storage"; ls -la /mnt/storage /data/local /data/property /data/wallpaper /res /config /system /extres 2>&1
echo "== logcat"; logcat -d -v time 2>&1
echo "== meminfo"; cat /proc/meminfo
echo "== bin"; ls /bin /sbin /system/bin 2>&1
echo "== usb"; ls /sys/class/android_usb/android0 /sys/class/udc 2>&1; cat /sys/class/android_usb/android0/functions /sys/class/android_usb/android0/iSerial /sys/class/android_usb/android0/state 2>&1
echo "== mtd"; cat /proc/mtd
echo "== etc"; ls -laR /etc 2>&1
echo "== ps"; ps 2>/dev/null || ps -ef
echo "== dmesg"; dmesg | tail -n 400
echo "== df"; df -h 2>/dev/null || df
echo "== free"; free 2>/dev/null; head -n 5 /proc/meminfo
echo "== mount"; mount
echo "== messages"; for f in /var/log/messages /var/log/*.log /tmp/*.log /tmp/log/* /data/log/* /userdata/log/*; do [ -f "$f" ] && { echo "--- $f"; tail -n 200 "$f"; }; done
echo "== ls"; ls -la / /tmp /var/log /data /userdata /oem /mnt /customer /appconfigs 2>&1
echo "== init"; cat /etc/inittab 2>/dev/null; ls -la /etc/init.d 2>&1
for f in /etc/init.d/S* /etc/init.d/rcS; do [ -f "$f" ] && { echo "--- $f"; cat "$f"; }; done
echo "== gadget"; for f in $(find /sys/kernel/config/usb_gadget -maxdepth 3 -name UDC -o -maxdepth 3 -name idProduct -o -maxdepth 4 -name serialnumber 2>/dev/null); do echo "$f: $(cat $f)"; done
echo "== cmdline"; cat /proc/cmdline; uname -a; cat /proc/cpuinfo | head -n 20
echo "== big files under /data-ish"; du -a /data /userdata /customer /appconfigs 2>/dev/null | sort -n | tail -n 40
echo "== end"
"""


def adb(*args, timeout=10):
    return subprocess.run([str(ADB), *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=NO_WINDOW)


def state() -> str | None:
    """'device' | 'offline' | 'unauthorized' | ... | None (no adb device)."""
    try:
        lines = adb("devices", timeout=5).stdout.splitlines()[1:]
    except subprocess.TimeoutExpired:
        return None
    parts = [ln.split() for ln in lines if ln.strip()]
    return parts[0][1] if parts and len(parts[0]) > 1 else None


def dump(label: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}.txt"
    t = time.monotonic()
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(f"# {label} dump started {time.strftime('%H:%M:%S')}\n")
        f.flush()
        try:
            f.write("# features: " + adb("features", timeout=3).stdout.strip() + "\n")
        except subprocess.TimeoutExpired:
            pass
        # adbd may accept the connection before its shell works ("error: closed"): retry until the window closes
        tries = 0
        while time.monotonic() - t < 20:
            tries += 1
            for mode in ("shell", "exec-out"):
                try:
                    r = subprocess.run([str(ADB), mode, "echo __ok__"], capture_output=True, text=True, timeout=3,
                                       creationflags=NO_WINDOW)
                except subprocess.TimeoutExpired:
                    continue
                if "__ok__" in r.stdout:
                    f.write(f"# {mode} works after {tries} tries, {time.monotonic() - t:.1f} s\n")
                    f.flush()
                    try:
                        subprocess.run([str(ADB), mode, DUMP], stdout=f, stderr=subprocess.STDOUT, timeout=60,
                                       creationflags=NO_WINDOW)
                    except subprocess.TimeoutExpired:
                        f.write("\n# timed out\n")
                    f.write(f"\n# took {time.monotonic() - t:.1f} s\n")
                    return path
                if tries <= 3 or tries % 10 == 0:
                    f.write(f"# try {tries} {mode}: {(r.stdout + r.stderr).strip()[:200]}\n")
            if state() is None:
                f.write("# the boot window closed\n")
                break
            time.sleep(0.1)
        f.write(f"\n# took {time.monotonic() - t:.1f} s\n")
    return path


def main() -> None:
    if not ADB.is_file():
        sys.exit(f"adb not found at {ADB}")
    adb("start-server", timeout=30)
    print("Waiting for the deck's boot stage (replug it). Ctrl+C to stop.", flush=True)
    prev, since, dumped_stuck = None, 0.0, False
    while True:
        s = state()
        now = time.monotonic()
        if s != prev:
            print(f"{time.strftime('%H:%M:%S')} adb: {s or 'gone'}", flush=True)
            prev, since, dumped_stuck = s, now, False
            if s == "device":
                print("  saved", dump("stage1"), flush=True)
        elif s is not None and not dumped_stuck and now - since > 15:
            dumped_stuck = True
            print(f"  still in boot stage after 15 s ({s}): the deck's app didn't start", flush=True)
            if s == "device":
                print("  saved", dump("stuck"), flush=True)
        time.sleep(0.15)


if __name__ == "__main__":
    main()
