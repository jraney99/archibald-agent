#!/usr/bin/env python3
"""Hand a long ramp off to its own process and let the cron job return.

WHY (2026-10-08): `chilipad/scripts/ramp.py` and `govee-lights/scripts/winddown_fade.py`
each sleep in-process for a 45-minute ramp. While one of them is running, OpenClaw's
cron runner records NO other job's completion — no `cron_run_logs` rows are written and
every job that started alongside it stays flagged `running_at_ms` with no process alive,
forever, because a lost run is never reaped and `payload_timeout_seconds` is not
enforced. It happened twice in one evening:

  22:00  the two Govee fades started  -> 8 jobs wedged; `chilipad-entry` never fired at
         22:15, so the user's bed stayed off until he noticed and said so.
  22:45  `chilipad-descend`'s ramp started -> 3 more wedged, including
         `govee-evening-hold-off`, so his bedroom lights stayed on until he said so.

The ramp itself is not the problem — it is correct, and it has to set a value every few
minutes for 45 minutes. The problem is doing that INSIDE the cron worker. So the cron
job now re-launches the same script detached and exits in well under a second; the ramp
carries on exactly as before in a process of its own.

Safe for these two because every step sets an ABSOLUTE value (a missed step is corrected
by the next one, not compounded) and both re-check whether the user has taken over before
each step — neither depends on the cron runner holding the process.

    from detach import DETACH_FLAG, relaunch_detached     # noqa
    ap.add_argument(DETACH_FLAG, action="store_true", help=...)
    ...guards first, so a misfire still reports itself to cron and spawns nothing...
    if args.detach:
        print(json.dumps(relaunch_detached(LOG_PATH)))
        return
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DETACH_FLAG = "--detach"

# Keep the log from growing without bound: a 45-minute ramp prints a line per step.
MAX_LOG_BYTES = 256 * 1024


def _trim(log_path: Path) -> None:
    try:
        if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
            tail = log_path.read_bytes()[-(MAX_LOG_BYTES // 2):]
            log_path.write_bytes(b"...trimmed...\n" + tail)
    except OSError:
        pass        # a log that cannot be trimmed must never stop the ramp


def relaunch_detached(log_path: Path, argv: list[str] | None = None) -> dict:
    """Re-run this script without the detach flag, in a session of its own, and
    return a receipt for the caller to print.

    `start_new_session=True` puts the child in its own process group, so it
    survives the cron worker's exit and is not killed with it. Output goes to
    `log_path` rather than a pipe: nothing stays attached to read a pipe for 45
    minutes, and a full pipe buffer would block the ramp.
    """
    argv = list(sys.argv if argv is None else argv)
    script = str(Path(argv[0]).resolve())
    rest = [a for a in argv[1:] if a != DETACH_FLAG]
    cmd = [sys.executable, script, *rest]

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _trim(log_path)
    with open(log_path, "ab") as log:
        log.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} "
                  f"{' '.join(rest)} ===\n".encode())
        log.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
            cwd=str(Path(script).parent),
        )
    return {"detached": True, "pid": proc.pid, "log": str(log_path)}


def is_running(pid: int) -> bool:
    """Whether a detached ramp is still alive — for status scripts."""
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True
