# Started at logon (and restarted if it exits) by the task `python -m deck install` creates. pythonw: no console.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deck.__main__ import main  # noqa: E402

main(["run", "--quiet"])
