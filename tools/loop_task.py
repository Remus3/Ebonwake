"""EW loop schedule (plan 015 item 2): tools/loop_task.py install|remove|status|run-now.

Registers the per-user Task Scheduler task "\\Ebonwake\\LaneLoop" via
`schtasks.exe /Create /XML <file> /F` (idempotent): a TimeTrigger repeating
every PT15M with no end, MultipleInstancesPolicy IgnoreNew (a long tick is
never doubled), LeastPrivilege + InteractiveToken (no admin rights), action =
pythonw.exe beside sys.executable running `tools/ew_loop.py tick` with
WorkingDirectory = repo root. `status` reads the task back: the definition from
`schtasks /Query /TN <task> /XML`, the state (Ready / Running / Disabled), last
run and last result from `/Query /FO LIST /V`. `run-now` is `schtasks /Run`.
Every command prints ONE JSON object. All subprocess calls are injectable;
CREATE_NO_WINDOW. Paths are resolved at run time; none is stored in this file.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logon_task  # noqa: E402  (shared schtasks helpers: decode, pythonw, user)

ROOT = Path(__file__).resolve().parent.parent
TASK = "\\Ebonwake\\LaneLoop"
INTERVAL = "PT15M"
TIME_LIMIT = "PT6H"
NS = logon_task.NS
FLAGS = logon_task.FLAGS

TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Ebonwake: EW loop tick (inbox, lanes, review, merge) every 15 minutes.</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>{interval}</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>{limit}</ExecutionTimeLimit>
    <Priority>7</Priority>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def task_xml(command: str, arguments: str, workdir: str, user: str, start: str,
             interval: str = INTERVAL, limit: str = TIME_LIMIT) -> str:
    return TEMPLATE.format(command=escape(command), arguments=escape(arguments),
                           workdir=escape(workdir), user=escape(user), start=escape(start),
                           interval=escape(interval), limit=escape(limit))


def start_boundary(now: _dt.datetime | None = None) -> str:
    """Local time, whole minute, no zone: the first tick is one minute out."""
    now = now or _dt.datetime.now()
    return (now.replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)) \
        .strftime("%Y-%m-%dT%H:%M:%S")


def _schtasks(run, *args):
    return run(["schtasks.exe", *args], capture_output=True, creationflags=FLAGS)


def parse_xml(text: str) -> dict:
    import xml.etree.ElementTree as ET
    body = text.split("?>", 1)[1] if text.lstrip().startswith("<?xml") else text
    root = ET.fromstring(body.strip())

    def val(path, default=None):
        el = root.find(path, NS)
        return el.text if el is not None and el.text is not None else default

    return {"exists": True,
            "command": val("t:Actions/t:Exec/t:Command"),
            "arguments": val("t:Actions/t:Exec/t:Arguments"),
            "workdir": val("t:Actions/t:Exec/t:WorkingDirectory"),
            "interval": val("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval"),
            "multiple_instances": val("t:Settings/t:MultipleInstancesPolicy"),
            "run_level": val("t:Principals/t:Principal/t:RunLevel"),
            "enabled": val("t:Settings/t:Enabled", "true").strip().lower() == "true"}


LIST_KEYS = {"status": "state", "last run time": "last_run", "last result": "last_result",
             "next run time": "next_run"}


def parse_list(text: str) -> dict:
    """The keys we read back from `/FO LIST /V` (first record only)."""
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        k = key.strip().lower()
        if sep and k in LIST_KEYS and LIST_KEYS[k] not in out:
            out[LIST_KEYS[k]] = value.strip()
    return out


def status(run=subprocess.run) -> dict:
    r = _schtasks(run, "/Query", "/TN", TASK, "/XML")
    if r.returncode != 0:
        return {"exists": False}
    doc = parse_xml(logon_task._decode(r.stdout))
    v = _schtasks(run, "/Query", "/TN", TASK, "/FO", "LIST", "/V")
    if v.returncode == 0:
        doc.update(parse_list(logon_task._decode(v.stdout)))
    if not doc["enabled"]:
        doc["state"] = "Disabled"
    return doc


def install(run=subprocess.run, env=None, root=ROOT, exe=None, tmp_dir=None, now=None) -> dict:
    root = Path(root)
    xml = task_xml(command=logon_task.pythonw(exe),
                   arguments=f'"{root / "tools" / "ew_loop.py"}" tick',
                   workdir=str(root), user=logon_task.current_user(env),
                   start=start_boundary(now))
    tmp_dir = Path(tmp_dir) if tmp_dir else root / "ops" / "runtime"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / f"loop_task.{os.getpid()}.xml"
    try:
        path.write_bytes(xml.encode("utf-16"))  # BOM + LE: what schtasks expects
        r = _schtasks(run, "/Create", "/TN", TASK, "/XML", str(path), "/F")
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    if r.returncode != 0:
        return {"exists": False, "error": logon_task._decode(r.stderr or r.stdout).strip()[:300]}
    return status(run)


def remove(run=subprocess.run) -> dict:
    if not status(run)["exists"]:
        return {"exists": False, "removed": False}
    r = _schtasks(run, "/Delete", "/TN", TASK, "/F")
    return {"exists": status(run)["exists"], "removed": r.returncode == 0}


def run_now(run=subprocess.run) -> dict:
    if not status(run)["exists"]:
        return {"exists": False, "error": "task not installed"}
    r = _schtasks(run, "/Run", "/TN", TASK)
    doc = status(run)
    doc["started"] = r.returncode == 0
    return doc


def main(argv, run=subprocess.run, env=None, root=ROOT, exe=None, tmp_dir=None) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "install":
        doc = install(run=run, env=env, root=root, exe=exe, tmp_dir=tmp_dir)
    elif cmd == "remove":
        doc = remove(run=run)
    elif cmd == "status":
        doc = status(run=run)
    elif cmd == "run-now":
        doc = run_now(run=run)
    else:
        print("usage: loop_task.py install|remove|status|run-now", file=sys.stderr)
        return 2
    print(json.dumps(doc))
    return 0 if "error" not in doc else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
