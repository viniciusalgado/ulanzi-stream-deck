# Runs app.py. If it fails to import or crashes, reboot after 10 s; after a Wi-Fi update, boot.py
# then restores the previous app.py. Ctrl+C (e.g. from mpremote) stops here and leaves the REPL open.
import sys
import time

import machine

try:
    import app
    app.main()
except Exception as e:
    sys.print_exception(e)
    print("app crashed; rebooting in 10 s (Ctrl+C to stay in the REPL)")
    time.sleep(10)
    machine.reset()
