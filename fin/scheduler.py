"""Phase 6 - daily automatic run (sync + encrypted backup) via Windows Task Scheduler.

The task runs as you, only while you're logged on (Windows Credential Manager needs your
session), catches up after sleep/shutdown ("run as soon as possible after a missed start"),
runs on battery, and uses pythonw.exe so no window pops up. Results go to the finintel log
and to `fin daily --status`.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "finintel daily"
STATUS_KEY = "last_daily"


def task_xml(python_exe: str, start: dt.datetime, workdir: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>finintel: read-only sync of bank/Fidelity data and encrypted backup.</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start:%Y-%m-%dT%H:%M:%S}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(python_exe)}</Command><Arguments>-m fin daily</Arguments><WorkingDirectory>{escape(workdir)}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def gui_python() -> str:
    """pythonw.exe from the same environment (no console window); falls back to python.exe."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def install(at: str = "07:30", runner=subprocess.run, python_exe: str | None = None,  # noqa: ANN001
            workdir: str | None = None) -> str:
    hh, mm = (int(x) for x in at.split(":"))
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError("time must look like 07:30")
    start = dt.datetime.combine(dt.date.today(), dt.time(hh, mm))
    from fin.config import PROJECT_ROOT

    xml = task_xml(python_exe or gui_python(), start, workdir or str(PROJECT_ROOT))
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "task.xml"
        f.write_text(xml, encoding="utf-16")
        r = runner(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(f), "/F"],
                   capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "schtasks failed").strip())
    return f"Scheduled '{TASK_NAME}' every day at {at} (catches up if the laptop was off/asleep)."


def remove(runner=subprocess.run) -> str:  # noqa: ANN001
    r = runner(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "schtasks failed").strip())
    return f"Removed '{TASK_NAME}'."


def query(runner=subprocess.run) -> str:  # noqa: ANN001
    r = runner(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"],
               capture_output=True, text=True)
    if r.returncode != 0:
        return "Not scheduled. Run: uv run python -m fin schedule install --time 07:30"
    keep = ("TaskName", "Next Run Time", "Status", "Last Run Time", "Last Result")
    return "\n".join(line for line in r.stdout.splitlines() if line.split(":")[0].strip() in keep)


def save_status(s, status: dict) -> None:  # noqa: ANN001
    from fin.db.models import Setting

    row = s.get(Setting, STATUS_KEY)
    if row is None:
        s.add(Setting(key=STATUS_KEY, value=json.dumps(status)))
    else:
        row.value = json.dumps(status)


def load_status(s) -> dict | None:  # noqa: ANN001
    from fin.db.models import Setting

    row = s.get(Setting, STATUS_KEY)
    return json.loads(row.value) if row and row.value else None
