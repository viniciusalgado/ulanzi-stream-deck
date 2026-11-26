import shutil
import xml.dom.minidom
from pathlib import Path

from deck import deploy

SID = "S-1-5-21-1-2-3-1001"


def test_service_access_gets_one_ace_for_the_user_before_the_audit_part():
    sddl = "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)S:(AU;FA;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;WD)"
    once = deploy.service_sddl_with_user(sddl, SID)
    assert once == ("D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)"
                    f"(A;;CCLCLORPWPRC;;;{SID})S:(AU;FA;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;WD)")
    assert deploy.service_sddl_with_user(once, SID) == once
    assert deploy.service_sddl_with_user("D:(A;;GA;;;SY)", SID).endswith(f"(A;;CCLCLORPWPRC;;;{SID})")


def test_copy_skips_tests_caches_and_test_tools(tmp_path):
    src = tmp_path / "src"
    for f in ["python.exe", "Lib/os.py", "Lib/test/t.py", "Lib/__pycache__/os.pyc", "Lib/site-packages/x.py",
              "DLLs/_ssl.pyd", "Scripts/pip.exe"]:
        (src / f).parent.mkdir(parents=True, exist_ok=True)
        (src / f).write_text("x")
    shutil.copytree(src, tmp_path / "out", ignore=deploy.skipper(src, deploy.RUNTIME_SKIP))
    got = sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert got == ["DLLs/_ssl.pyd", "Lib/os.py", "python.exe"]


def test_service_command_line_and_agent_task_point_at_program_files():
    program = Path(r"C:\Program Files\UlanziDeck")
    assert deploy.binpath(program) == (r'"C:\Program Files\UlanziDeck\python\python.exe" -I '
                                       r'"C:\Program Files\UlanziDeck\app\deckd.py" service')
    doc = xml.dom.minidom.parseString(deploy.task_xml(program, r"PC\V", SID, "2026-11-26T12:00:00")
                                      .replace('encoding="UTF-16"', ""))
    text = lambda tag: doc.getElementsByTagName(tag)[0].firstChild.data  # noqa: E731
    assert text("Command") == r"C:\Program Files\UlanziDeck\python\pythonw.exe"
    assert text("Arguments") == r'-I "C:\Program Files\UlanziDeck\app\deckd.py" agent'
    assert text("RunLevel") == "LeastPrivilege" and SID in text("SecurityDescriptor")
