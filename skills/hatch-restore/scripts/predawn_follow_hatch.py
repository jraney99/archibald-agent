#!/usr/bin/env python3
"""3:05am, while the user sleeps: look at the Hatch itself and make the morning follow it.

The user, 2026-09-20: "The lights should be dynamic with what I set the hatch to. If I set the
alarm to 9:30 am, the lights should adjust to that time." He sets the Hatch in its own app at
bedtime — after the 9pm sync and the 9:15pm light retime — and nothing tells us. So:

  1. read what the alarm will actually do this morning;
  2. retime the bedroom sunrise + morning-off lights to it (covers a wake moved EARLIER;
     one moved LATER is also caught when the ramp fires — sunrise_ramp.py --follow-hatch);
  3. note that time as the baseline, so tonight's 9pm sync can tell "he changed the Hatch
     since this morning" (keep it for tomorrow) from "that was this morning's time".

Silent by design — never sends a message. Any failure leaves last night's schedule standing.
Known limit: an alarm moved EARLIER in the app after 3:05am is not seen until it rings.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SHARED_DIR = SCRIPTS_DIR.parent.parent / "shared"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SHARED_DIR))
import wake_override  # noqa: E402
from current_location import get_tz  # noqa: E402
from sync_bedroom_sunrise import read_plan  # noqa: E402

RETIME = SHARED_DIR / "sync_bedroom_sunrise.py"
# The briefing hangs off the same alarm (wake + 15, 2026-09-27), so an alarm moved in
# the Hatch app at bedtime has to move it too. Silent and best-effort, like the lights.
BRIEFING_REFRESH = SCRIPTS_DIR / "refresh_morning_briefing.py"


def main() -> int:
    try:
        plan = read_plan()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": f"could not read the Hatch: {str(e)[:160]}"}))
        return 1
    wake = plan["wake_dt"].time()
    wake_override.remember_baseline(wake, "pre-dawn read of the Hatch", datetime.now(get_tz()))
    run = subprocess.run([sys.executable, "-W", "ignore", str(RETIME)], capture_output=True, text=True, timeout=120)
    try:
        lights = json.loads(run.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        lights = {"status": "error", "error": (run.stdout or run.stderr).strip()[-160:]}
    try:
        briefing = subprocess.run([sys.executable, "-W", "ignore", str(BRIEFING_REFRESH)],
                                  capture_output=True, text=True, timeout=120)
        briefing_note = (briefing.stdout or briefing.stderr).strip().splitlines()[-1][:120]
    except Exception as e:  # noqa: BLE001
        briefing_note = f"briefing refresh failed: {str(e)[:100]}"
    print(json.dumps({"briefing": briefing_note,
                      "status": lights.get("status", "error"), "hatch_wake": wake.strftime("%H:%M"),
                      "alarm_on": plan["enabled"], "sunrise_start": lights.get("ramp_start_local"),
                      "lights_off": lights.get("morning_off_local"), "error": lights.get("error")}))
    return 0 if lights.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
