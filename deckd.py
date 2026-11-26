"""Entry point of the deployed copy: C:\\Program Files\\UlanziDeck\\app\\deckd.py (see deck/deploy.py).

    python.exe  -I deckd.py service           the Windows service (SYSTEM)
    pythonw.exe -I deckd.py console-helper    started by the service in the console session (SYSTEM)
    pythonw.exe -I deckd.py agent             the signed-in user's agent (logon task)

-I ignores PYTHON* variables, the user's site-packages and the current folder, so only Program Files code runs.
"""
import os
import site
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
if sys.argv[1:2] in (["service"], ["console-helper"]):
    os.environ["ULANZI_DECK_STATE"] = str(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ulanzi-deck")
sys.path.insert(0, str(APP))
site.addsitedir(str(APP.parent / "site-packages"))

from deck.__main__ import main  # noqa: E402

main(sys.argv[1:])
