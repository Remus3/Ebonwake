"""Start EW on login (plan 010 item 3): tools/logon_task.py install|remove|status.

Registers a per-user Task Scheduler logon task "Ebonwake" via
`schtasks.exe /Create /XML <file> /F` (idempotent: /F replaces the task with the
same XML). LogonTrigger for the current user, Delay PT60S (staggered startup),
LeastPrivilege + InteractiveToken (no admin rights), action = pythonw.exe beside
sys.executable running tools/launch.py with WorkingDirectory = repo root.
Every command prints ONE JSON object read back from
`schtasks /Query /TN Ebonwake /XML`. All subprocess calls are injectable;
CREATE_NO_WINDOW. Paths are resolved at run time; none is stored in this file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
TASK = "Ebonwake"
DELAY = "PT60S"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Ebonwake: start the EW server and app at logon.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>{delay}</Delay>
    </LogonTrigger>
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
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
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


def task_xml(command: str, arguments: str, workdir: str, user: str, delay: str = DELAY) -> str:
    return TEMPLATE.format(command=escape(command), arguments=escape(arguments),
                           workdir=escape(workdir), user=escape(user), delay=escape(delay))


def pythonw(exe: str | None = None) -> str:
    p = Path(exe or sys.executable)
    alt = p.with_name("pythonw.exe")
    return str(alt if alt.exists() else p)


def current_user(env=None) -> str:
    env = os.environ if env is None else env
    dom, name = env.get("USERDOMAIN", ""), env.get("USERNAME", "")
    return f"{dom}\\{name}" if dom else name


def _schtasks(run, *args):
    return run(["schtasks.exe", *args], capture_output=True, creationflags=FLAGS)


def _decode(raw) -> str:
    if isinstance(raw, str):
        return raw
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\0" in raw[:64]:
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "mbcs", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1")


def parse_task(text: str) -> dict:
    body = text.split("?>", 1)[1] if text.lstrip().startswith("<?xml") else text
    root = ET.fromstring(body.strip())

    def val(path, default=None):
        el = root.find(path, NS)
        return el.text if el is not None and el.text is not None else default

    return {"exists": True,
            "command": val("t:Actions/t:Exec/t:Command"),
            "arguments": val("t:Actions/t:Exec/t:Arguments"),
            "workdir": val("t:Actions/t:Exec/t:WorkingDirectory"),
            "delay": val("t:Triggers/t:LogonTrigger/t:Delay"),
            "enabled": val("t:Settings/t:Enabled", "true").strip().lower() == "true"}


def status(run=subprocess.run) -> dict:
    r = _schtasks(run, "/Query", "/TN", TASK, "/XML")
    if r.returncode != 0:
        return {"exists": False}
    return parse_task(_decode(r.stdout))


def install(run=subprocess.run, env=None, root=ROOT, exe=None, tmp_dir=None) -> dict:
    root = Path(root)
    xml = task_xml(command=pythonw(exe), arguments=f'"{root / "tools" / "launch.py"}"',
                   workdir=str(root), user=current_user(env))
    tmp_dir = Path(tmp_dir) if tmp_dir else root / "ops" / "runtime"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / f"logon_task.{os.getpid()}.xml"
    try:
        path.write_bytes(xml.encode("utf-16"))  # BOM + LE: what schtasks expects
        r = _schtasks(run, "/Create", "/TN", TASK, "/XML", str(path), "/F")
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    if r.returncode != 0:
        return {"exists": False, "error": _decode(r.stderr or r.stdout).strip()[:300]}
    return status(run)


def remove(run=subprocess.run) -> dict:
    if not status(run)["exists"]:
        return {"exists": False, "removed": False}
    r = _schtasks(run, "/Delete", "/TN", TASK, "/F")
    return {"exists": status(run)["exists"], "removed": r.returncode == 0}


def main(argv, run=subprocess.run, env=None, root=ROOT, exe=None, tmp_dir=None) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "install":
        doc = install(run=run, env=env, root=root, exe=exe, tmp_dir=tmp_dir)
    elif cmd == "remove":
        doc = remove(run=run)
    elif cmd == "status":
        doc = status(run=run)
    else:
        print("usage: logon_task.py install|remove|status", file=sys.stderr)
        return 2
    print(json.dumps(doc))
    return 0 if "error" not in doc else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
