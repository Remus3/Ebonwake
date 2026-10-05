"""Plan 015 item 2: tools/loop_task.py install|remove|status|run-now. Every
schtasks call goes through an injected runner; no test touches the real Task
Scheduler."""

import datetime as dt
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loop_task  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
ENV = {"USERDOMAIN": "HOST", "USERNAME": "op"}
LIST = """
Folder: \\EbonwakeOps
HostName:                             HOST
TaskName:                             \\EbonwakeOps\\LaneLoop
Next Run Time:                        10/5/2026 3:15:00 PM
Status:                               {state}
Logon Mode:                           Interactive only
Last Run Time:                        10/5/2026 3:00:00 PM
Last Result:                          0
"""


class FakeSchtasks:
    def __init__(self):
        self.tasks, self.calls, self.state = {}, [], "Ready"

    def __call__(self, args, **kw):
        self.calls.append((list(args), kw))
        assert args[0] == "schtasks.exe"
        verb, name = args[1], args[args.index("/TN") + 1]
        ok = lambda out=b"SUCCESS": subprocess.CompletedProcess(args, 0, out, b"")  # noqa: E731
        missing = subprocess.CompletedProcess(args, 1, b"", b"ERROR: not found")
        if verb == "/Create":
            raw = Path(args[args.index("/XML") + 1]).read_bytes()
            assert raw[:2] == b"\xff\xfe", "task xml must be UTF-16 LE with BOM"
            self.tasks[name] = raw.decode("utf-16")
            return ok()
        if name not in self.tasks:
            return missing
        if verb == "/Query" and "/XML" in args:
            return ok(self.tasks[name].encode("utf-16"))
        if verb == "/Query":
            assert args[-3:] == ["/FO", "LIST", "/V"]
            return ok(LIST.format(state=self.state).encode("mbcs" if sys.platform == "win32"
                                                           else "latin-1"))
        if verb == "/Run":
            self.state = "Running"
            return ok()
        if verb == "/Delete":
            del self.tasks[name]
            return ok()
        raise AssertionError(verb)


def _ctx(tmp_path, fake):
    exe = tmp_path / "py" / "python.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    (exe.parent / "pythonw.exe").write_bytes(b"")
    return dict(run=fake, env=ENV, root=tmp_path / "repo", exe=str(exe), tmp_dir=tmp_path / "rt")


def test_task_xml_shape():
    x = loop_task.task_xml(command="py/pythonw.exe", arguments='"r/tools/ew_loop.py" tick',
                           workdir="r", user="HOST\\op", start="2026-10-05T15:01:00")
    root = ET.fromstring(x.split("?>", 1)[1])
    trig = root.find("t:Triggers/t:TimeTrigger", NS)
    assert trig.find("t:Repetition/t:Interval", NS).text == "PT15M"
    assert trig.find("t:Repetition/t:StopAtDurationEnd", NS).text == "false"
    assert trig.find("t:Repetition/t:Duration", NS) is None  # repeat indefinitely
    assert trig.find("t:StartBoundary", NS).text == "2026-10-05T15:01:00"
    s = root.find("t:Settings", NS)
    assert s.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    pr = root.find("t:Principals/t:Principal", NS)
    assert pr.find("t:RunLevel", NS).text == "LeastPrivilege"
    assert pr.find("t:LogonType", NS).text == "InteractiveToken"
    ex = root.find("t:Actions/t:Exec", NS)
    assert ex.find("t:Arguments", NS).text == '"r/tools/ew_loop.py" tick'
    assert ex.find("t:WorkingDirectory", NS).text == "r"


def test_start_boundary_is_next_whole_minute():
    assert loop_task.start_boundary(dt.datetime(2026, 10, 5, 23, 59, 30)) == "2026-10-06T00:00:00"


def test_install_then_status_reads_back(tmp_path):
    fake = FakeSchtasks()
    c = _ctx(tmp_path, fake)
    st = loop_task.install(**c)
    assert st["exists"] and st["enabled"] and st["interval"] == "PT15M"
    assert st["multiple_instances"] == "IgnoreNew" and st["run_level"] == "LeastPrivilege"
    assert st["command"].endswith("pythonw.exe")
    assert st["arguments"] == '"' + str(c["root"] / "tools" / "ew_loop.py") + '" tick'
    assert st["workdir"] == str(c["root"])
    assert st["state"] == "Ready" and st["last_result"] == "0"
    assert st["last_run"] == "10/5/2026 3:00:00 PM" and st["next_run"].endswith("PM")
    create = [a for a, _ in fake.calls if a[1] == "/Create"][0]
    assert create[:4] == ["schtasks.exe", "/Create", "/TN", "\\EbonwakeOps\\LaneLoop"]
    assert "/F" in create
    nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for _, kw in fake.calls:
        assert kw.get("creationflags", 0) & nw == nw and kw.get("capture_output") is True
    assert not list((tmp_path / "rt").glob("*.xml")), "temp xml left behind"


def test_install_idempotent_run_now_remove(tmp_path):
    fake = FakeSchtasks()
    c = _ctx(tmp_path, fake)
    assert loop_task.run_now(run=fake)["exists"] is False
    loop_task.install(**c)
    loop_task.install(**c)
    assert list(fake.tasks) == ["\\EbonwakeOps\\LaneLoop"]
    doc = loop_task.run_now(run=fake)
    assert doc["started"] is True and doc["state"] == "Running"
    assert loop_task.remove(run=fake) == {"exists": False, "removed": True}
    assert loop_task.remove(run=fake) == {"exists": False, "removed": False}


def test_status_disabled_and_missing(tmp_path):
    fake = FakeSchtasks()
    assert loop_task.status(run=fake) == {"exists": False}
    x = loop_task.task_xml(command="p", arguments="a", workdir="w", user="u", start="s")
    fake.tasks[loop_task.TASK] = x.replace("<Enabled>true</Enabled>\n  </Settings>",
                                           "<Enabled>false</Enabled>\n  </Settings>")
    st = loop_task.status(run=fake)
    assert st["enabled"] is False and st["state"] == "Disabled"


def test_parse_list_first_record_only():
    text = LIST.format(state="Ready") + LIST.format(state="Running")
    assert loop_task.parse_list(text)["state"] == "Ready"


def test_main_prints_one_json_object(tmp_path, capsys):
    fake = FakeSchtasks()
    c = _ctx(tmp_path, fake)
    assert loop_task.main(["status"], **c) == 0
    assert json.loads(capsys.readouterr().out) == {"exists": False}
    assert loop_task.main(["run-now"], **c) == 1
    capsys.readouterr()
    assert loop_task.main(["install"], **c) == 0
    assert json.loads(capsys.readouterr().out)["exists"] is True
    assert loop_task.main(["bogus"], **c) == 2


def test_no_machine_path_in_tool_source():
    src = (ROOT / "tools" / "loop_task.py").read_text(encoding="utf-8")
    assert not re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", src) and "Users" not in src
    src.encode("ascii")
