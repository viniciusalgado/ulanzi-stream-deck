# Rollback guard for Wi-Fi updates of app.py.
# After POST /update, "app_pending" holds "0". This boot marks it "1" and runs the new app.py;
# the app deletes the marker once Wi-Fi and its server work. If the device boots again while the
# marker is still "1", the new version failed, so restore app_prev.py.
import os


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


if _exists("app_pending"):
    try:
        with open("app_pending") as f:
            attempts = int(f.read() or "0")
    except ValueError:
        attempts = 1
    if attempts >= 1:
        if _exists("app_prev.py"):
            if _exists("app.py"):
                os.remove("app.py")
            os.rename("app_prev.py", "app.py")
            print("boot: new app.py failed to start, restored the previous version")
        os.remove("app_pending")
    else:
        with open("app_pending", "w") as f:
            f.write("1")
