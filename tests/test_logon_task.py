"""Plan 010 item 3: tools/logon_task.py install|remove|status. Every schtasks
call goes through an injected runner; no test touches the real Task Scheduler."""

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import logon_task  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
ENV = {"USERDOMAIN": "HOST", "USERNAME": "op"}


class FakeSchtasks:
    """Minimal in-memory schtasks: /Create /XML <file> /F, /Query /XML, /Delete."""

    def __init__(self):
        self.tasks = {}
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((list(args), kw))
        assert args[0] == "schtasks.exe"
        verb = args[1]
        name = args[args.index("/TN") + 1]
        if verb == "/Create":
            path = Path(args[args.index("/XML") + 1])
            raw = path.read_bytes()
            assert raw[:2] == b"\xff\xfe", "task xml must be UTF-16 LE with BOM"
            if name in self.tasks and "/F" not in args:
                return subprocess.CompletedProcess(args, 1, b"", b"exists")
            self.tasks[name] = raw.decode("utf-16")
            return subprocess.CompletedProcess(args, 0, b"SUCCESS", b"")
        if verb == "/Query":
            if name not in self.tasks:
                return subprocess.CompletedProcess(args, 1, b"", b"ERROR: not found")
            return subprocess.CompletedProcess(args, 0, self.tasks[name].encode("utf-16"), b"")
        if verb == "/Delete":
            if name not in self.tasks:
                return subprocess.CompletedProcess(args, 1, b"", b"ERROR: not found")
            del self.tasks[name]
            return subprocess.CompletedProcess(args, 0, b"SUCCESS", b"")
        raise AssertionError(verb)


def _ctx(tmp_path, fake):
    exe = tmp_path / "py" / "python.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    (exe.parent / "pythonw.exe").write_bytes(b"")
    return dict(run=fake, env=ENV, root=tmp_path / "repo", exe=str(exe),
                tmp_dir=tmp_path / "rt")


def test_task_xml_shape(tmp_path):
    x = logon_task.task_xml(command="py/pythonw.exe", arguments='"r/tools/launch.py"',
                            workdir="r", user="HOST\\op")
    root = ET.fromstring(x.split("?>", 1)[1])
    trig = root.find("t:Triggers/t:LogonTrigger", NS)
    assert trig.find("t:UserId", NS).text == "HOST\\op"
    assert trig.find("t:Delay", NS).text == "PT60S"
    pr = root.find("t:Principals/t:Principal", NS)
    assert pr.find("t:RunLevel", NS).text == "LeastPrivilege"
    assert pr.find("t:LogonType", NS).text == "InteractiveToken"
    ex = root.find("t:Actions/t:Exec", NS)
    assert ex.find("t:Command", NS).text == "py/pythonw.exe"
    assert ex.find("t:Arguments", NS).text == '"r/tools/launch.py"'
    assert ex.find("t:WorkingDirectory", NS).text == "r"
    assert root.find("t:Settings/t:Enabled", NS).text == "true"


def test_task_xml_escapes(tmp_path):
    x = logon_task.task_xml(command="a&b", arguments="<x>", workdir="w", user="u")
    assert "a&amp;b" in x and "&lt;x&gt;" in x


def test_pythonw_sibling(tmp_path):
    c = _ctx(tmp_path, None)
    assert logon_task.pythonw(c["exe"]) == str(Path(c["exe"]).with_name("pythonw.exe"))
    lone = tmp_path / "lone" / "python.exe"
    lone.parent.mkdir()
    lone.write_bytes(b"")
    assert logon_task.pythonw(str(lone)) == str(lone)


def test_install_then_status_reads_back(tmp_path):
    fake = FakeSchtasks()
    c = _ctx(tmp_path, fake)
    st = logon_task.install(**c)
    assert st["exists"] is True and st["enabled"] is True and st["delay"] == "PT60S"
    assert st["command"].endswith("pythonw.exe")
    assert st["arguments"] == '"' + str(c["root"] / "tools" / "launch.py") + '"'
    assert st["workdir"] == str(c["root"])
    create = [a for a, _ in fake.calls if a[1] == "/Create"][0]
    assert create[:4] == ["schtasks.exe", "/Create", "/TN", "Ebonwake"] and "/F" in create
    for _, kw in fake.calls:
        assert kw.get("creationflags", 0) & getattr(subprocess, "CREATE_NO_WINDOW", 0) == \
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert kw.get("capture_output") is True


def test_install_is_idempotent(tmp_path):
    fake = FakeSchtasks()
    c = _ctx(tmp_path, fake)
    a = logon_task.install(**c)
    xml1 = fake.tasks["Ebonwake"]
    b = logon_task.install(**c)
    assert a == b and fake.tasks["Ebonwake"] == xml1 and list(fake.tasks) == ["Ebonwake"]
    assert not list((tmp_path / "rt").glob("*.xml")), "temp xml left behind"


def test_status_missing_and_remove_idempotent(tmp_path):
    fake = FakeSchtasks()
    c = _ctx(tmp_path, fake)
    assert logon_task.status(run=fake) == {"exists": False}
    logon_task.install(**c)
    assert logon_task.remove(run=fake) == {"exists": False, "removed": True}
    assert logon_task.remove(run=fake) == {"exists": False, "removed": False}


def test_status_parses_disabled_and_plain_text_output(tmp_path):
    x = logon_task.task_xml(command="p", arguments="a", workdir="w", user="u")
    x = x.replace("<Enabled>true</Enabled>\n  </Settings>", "<Enabled>false</Enabled>\n  </Settings>")
    for raw in (x.encode("utf-16"), x.encode("utf-8")):
        def run(args, **kw):
            return subprocess.CompletedProcess(args, 0, raw, b"")
        st = logon_task.status(run=run)
        assert st["exists"] and st["enabled"] is False and st["command"] == "p"


def test_main_prints_one_json_object(tmp_path, capsys):
    fake = FakeSchtasks()
    c = _ctx(tmp_path, fake)
    assert logon_task.main(["status"], **c) == 0
    assert json.loads(capsys.readouterr().out) == {"exists": False}
    assert logon_task.main(["install"], **c) == 0
    assert json.loads(capsys.readouterr().out)["exists"] is True
    assert logon_task.main(["bogus"], **c) == 2


def test_no_machine_path_in_tool_source():
    src = (ROOT / "tools" / "logon_task.py").read_text(encoding="utf-8")
    import re
    assert not re.search(r"[A-Za-z]:\\", src) and "Users" not in src
