#!/usr/bin/env python3
"""
The single nightly wake + workout sync. In one run:

  1. Compute tomorrow's alarm wake time from tomorrow's calendar (drive time
     + arrival buffer; earlier than the 8:30 default only when an early
     commitment requires it) and act on it — write it to the Hatch when
     The user's home, or (travelling) disable the Hatch and send a "set your
     phone alarm for X" brief + schedule a backup nudge. "Travelling" here
     means is_away_from_home() OR a flight on tomorrow's calendar — the
     latter catches the night-before-departure case that the away flag
     misses (see sync_alarm.flight_tomorrow / feedback-travel-detection-lag).
  2. Book tomorrow's workout via schedule_workouts.py, which reads the
     calendar and places it at the user's normal configured time
     (preferred_start_times[weekday]) or the next free window if that's
     blocked. This step is INDEPENDENT of the wake time — see book_workout().
  3. Re-check back-to-back calendar conflicts now that the real workout
     event exists.
  4. Send one Telegram message covering all of the above.

This replaced two cron jobs (hevy-night-prep-9pm + hatch-alarm-sync-910pm)
that only lined up by coincidence. The ordering that actually matters:
wake-time compute uses tomorrow's calendar *before* the workout is booked
(no circularity); the conflict check runs *after* (needs the real event).

History note: 2026-08-21 → 2026-08-28 this script also passed the workout a
`--preferred-start` derived from the computed wake time. That was removed —
on a day the user woke early *for a meeting*, it tried to jam the workout into
the gap before that meeting. The get-ready buffer is baked into the normal
configured start time instead. See feedback-workout-wake-buffer.

Usage:
    python3 nightly_wake_and_workout_sync.py
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
HEVY_SCRIPTS_DIR = SCRIPTS_DIR.parent.parent / "hevy-workout" / "scripts"
SCHEDULE_WORKOUTS = HEVY_SCRIPTS_DIR / "schedule_workouts.py"
SEND_TELEGRAM = SCRIPTS_DIR.parent.parent / "shared" / "send_telegram.py"
TRAVEL_WAKE_NUDGE = SCRIPTS_DIR / "travel_wake_nudge.py"

NUTRITION_DIR = SCRIPTS_DIR.parent.parent / "nutrition"
SEND_MEAL_REMINDER = NUTRITION_DIR / "scripts" / "send_meal_reminder.py"
NUTRITION_CONFIG = NUTRITION_DIR / "config" / "nutrition_config.json"
BREAKFAST_JOB_PREFIX = "nutrition-breakfast-anchor-"
DEFAULT_BREAKFAST_AFTER_WAKE_MIN = 30
# The alarm time the pending one-shot was built from + when it fires.
# send_meal_reminder.py reads it for the "by H:MM" line and so its static
# backstop doesn't pre-empt a later one-shot; refresh_breakfast_anchor.py
# reads it to tell whether the live Hatch alarm has moved since.
BREAKFAST_PLAN_FILE = Path(os.path.expanduser("~/.openclaw/breakfast_anchor_plan.json")).expanduser()

RUN_MORNING_BRIEFING = SCRIPTS_DIR.parent.parent / "morning-briefing" / "scripts" / "run_morning_briefing.py"
BRIEFING_JOB_PREFIX = "morning-briefing-at-wake-"
BRIEFING_AFTER_WAKE_MIN = 15

sys.path.insert(0, str(SCRIPTS_DIR))
import sync_alarm  # noqa: E402
from current_location import get_tz, is_away_from_home  # noqa: E402
from data_paths import path as data_path  # noqa: E402

# The alarm time the pending briefing one-shot was built from + when it fires;
# refresh_morning_briefing.py reads it to tell whether the alarm has moved since.
BRIEFING_PLAN_FILE = data_path("briefing_plan.json")
from misfire_guard import within_slot  # noqa: E402  (sync_alarm put shared/ on sys.path)

# This job is cron-scheduled for 21:00 in the user's current tz. If the gateway
# was asleep at 21:00 and restarts hours later, its catch-up run would fire
# this in the afternoon and send a "prep for tomorrow" brief at the wrong
# time (and, past midnight, compute the wrong "tomorrow"). Only run in the
# evening slot; a real 21:00 fire the next night is unaffected.
MISFIRE_SLOT = (19, 24)  # [hour_lo, hour_hi) local, must not cross midnight

TG_TOKEN = __import__("runpy").run_path(os.path.expanduser("~/.openclaw/workspace/Skills/shared/runtime_secrets.py"))["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"

OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"
WEEKLY_CHECKIN_REMINDER_AFTER_WAKE_MINUTES = 5
WEEKLY_CHECKIN_JOB_PREFIX = "weekly-weekly_checkin-reminder-"
WEEKLY_CHECKIN_MESSAGE = "\U0001f501 Weekly check-in time \U0001f3a9"

# Backup wake nudge while travelling (Hatch is disabled + sitting at home;
# phone alarm is primary, this is the failsafe). One-shot cron at the
# computed wake time, runs travel_wake_nudge.py.
TRAVEL_WAKE_JOB_PREFIX = "travel-wake-nudge-"
TRAVEL_WAKE_NUDGE_REPEATS = 3
TRAVEL_WAKE_NUDGE_INTERVAL_SECONDS = 240


def notify(text: str) -> None:
    # 2026-10-06: this rolls its own Telegram POST instead of going through
    # shared/send_telegram.py, so it never saw that module's TELEGRAM_SEND_DISABLED
    # tripwire. Two tests in test_breakfast_anchor_refresh.py did not stub notify, and
    # the moment a code change made them reach the error branch they messaged the user for
    # real -- four times. The tripwire is the same one send_telegram uses, and it raises
    # rather than skipping: a test that gets here has a missing guard, and silence would
    # hide it (feedback-test-suite-sent-real-telegram).
    if os.environ.get("TELEGRAM_SEND_DISABLED") == "1":
        raise RuntimeError("Telegram sending is disabled in this environment "
                           "(nightly sync notify blocked)")
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"notify failed: {e}", file=sys.stderr)


def compute_wake(config: dict, tz) -> dict:
    """Fail-safe wake compute+apply — mirrors sync_alarm.build_result()'s
    resilience (never raises, always returns something concrete) but
    WITHOUT the conflict check, which runs later in main() after the
    workout is booked."""
    try:
        events = sync_alarm.fetch_tomorrow_events()
    except Exception as e:
        return {
            "wake_dt_iso": None, "wake_time_fmt": None, "is_default": None, "away": None,
            "flight_tomorrow": None,
            "default_wake_time_fmt": config.get("default_wake_time"),
            "event_title": None, "leave_time_fmt": None,
            "sync_failed": True, "error": str(e)[:200],
        }

    try:
        return sync_alarm.compute_and_apply(config, tz, events)
    except Exception as e:
        error_msg = str(e)[:200]
        try:
            wake_time_fmt = sync_alarm.read_current_only(config)
        except Exception:
            wake_time_fmt = None
        return {
            "wake_dt_iso": None, "wake_time_fmt": wake_time_fmt, "is_default": None, "away": None,
            "flight_tomorrow": sync_alarm.flight_tomorrow(events),
            "default_wake_time_fmt": config.get("default_wake_time"),
            "event_title": None, "leave_time_fmt": None,
            "sync_failed": True, "error": error_msg,
        }


def book_workout() -> dict:
    """Book tomorrow's workout, INDEPENDENTLY of the wake time.

    schedule_workouts.py reads tomorrow's calendar, tries the normal
    configured time (preferred_start_times[weekday], ~09:25 on weekdays /
    10:00 Saturday), and falls back to an earliest-free-window search when
    that slot is blocked — so on a clear day the workout lands at the user's
    usual time, and on a busy day it slides to the next open gap.

    We deliberately do NOT pass --preferred-start. From 2026-08-21 to
    2026-08-28 this handed it `wake_time + buffer`, so a day the user woke early
    *for a meeting* (6:00am wake for a 7:30am meeting) told the booker to try
    ~7:00am first and, if that pre-meeting gap was free, it grabbed it — a
    workout he never wanted. The get-ready buffer (wake → workout) is
    already baked into the normal configured start time; an early
    commitment-driven wake shouldn't drag the workout around with it. See
    feedback-workout-wake-buffer.
    """
    cmd = [sys.executable, "-W", "ignore", str(SCHEDULE_WORKOUTS), "--date", "tomorrow"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout.strip())
    except Exception:
        return {"status": "error", "message": (r.stderr or r.stdout).strip()[:300]}


MEAL_CRON_NAME = "nutrition-meal-reminder-slot{slot}"
SUPPLEMENT_CRON_NAME = "nutrition-supplement-nudge-between-meals"


def _meal_job_ids() -> dict:
    """{slot: job_id} for the five standing meal crons, by name.

    Resolved every run rather than hardcoded: a job recreated by hand gets a new id, and
    a stale id would retime nothing while reporting success."""
    r = subprocess.run([OPENCLAW_BIN, "cron", "list", "--all", "--json"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("could not read the cron list: " + (r.stderr or r.stdout).strip()[:200])
    jobs = {j.get("name"): j for j in (json.loads(r.stdout).get("jobs") or [])}
    ids, missing = {}, []
    for slot in range(1, 6):
        name = MEAL_CRON_NAME.format(slot=slot)
        job = jobs.get(name)
        if job is None or not job.get("id"):
            missing.append(name)
        else:
            ids[slot] = job["id"]
    if missing:
        raise RuntimeError("meal cron(s) missing from the cron list: " + ", ".join(missing))
    return ids


def _retime_supplement_nudge(times: dict, tz) -> None:
    """Point the between-meals supplement nudge at today's computed gap.

    The supplement is taken between meals (physician's instruction), so its time is the
    midpoint of the lunch -> afternoon-snack gap and moves with the alarm just
    like the five meal windows.

    WHY THIS IS HERE (2026-10-12): it shipped on 2026-10-11 as a cron at a fixed
    `30 13,14,15 * * *` with a misfire guard picking whichever firing landed
    nearest. On the first real day the user woke at 9:30, which put the gap midpoint
    at 3:54 PM — past the last firing — so all three were refused as too early
    and he got nothing. That is the shape _retime_meal_crons already warns
    about: three triggers because no single one was trusted. One trigger,
    retimed nightly, is the fix.

    The midpoint comes from send_supplement_nudge's own gap_midpoint() rather
    than a second copy of the arithmetic, so the cron and the script can never
    disagree about when the nudge is due."""
    sys.path.insert(0, str(NUTRITION_DIR / "scripts"))
    import send_supplement_nudge as nudge
    due = nudge.gap_midpoint(
        {"slots": {str(slot): when.isoformat() for slot, when in times.items()}})
    if due is None:
        raise RuntimeError(
            f"no gap between meal slots {nudge.GAP_FROM} and {nudge.GAP_TO} to place "
            f"the supplement nudge in")
    job_id = _job_id_by_name(SUPPLEMENT_CRON_NAME)
    local = due.astimezone(tz)
    r = subprocess.run(
        [OPENCLAW_BIN, "cron", "edit", job_id,
         "--cron", f"{local.minute} {local.hour} * * *", "--tz", str(tz)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError("supplement nudge: " + (r.stderr or r.stdout).strip()[:200])
    # `cron edit` succeeds on a DISABLED job, so its exit code proves nothing
    # about whether the nudge will fire (feedback-cron-edit-succeeds-on-disabled-job).
    if not _cron_enabled(job_id):
        raise RuntimeError(f"supplement nudge retimed to {local:%H:%M}, but the job is disabled")


HYDRATION_CRON_NAME = "hydration-nudge-slot{slot}"
HYDRATION_SLOTS = 6
HYDRATION_FIRST_AFTER_WAKE_MIN = 45   # 7:30 wake -> 8:15, which is where they already sat
HYDRATION_SPACING_MIN = 120
HYDRATION_LATEST = (20, 0)            # nothing after 8pm: loading up near bed costs sleep
HYDRATION_MIN_SPACING = timedelta(minutes=45)


def hydration_times(wake_dt, tz) -> list:
    """The six nudge times for a day that starts at `wake_dt`.

    First at wake + 45 min, then every two hours. On a late start the last one
    would land near bedtime, so the whole run is compressed to finish by 8pm
    rather than dropping nudges — the 96oz goal does not shrink just because the
    day did, and a drink at 10:30pm costs him sleep."""
    first = (wake_dt + timedelta(minutes=HYDRATION_FIRST_AFTER_WAKE_MIN)).astimezone(tz)
    latest = first.replace(hour=HYDRATION_LATEST[0], minute=HYDRATION_LATEST[1],
                           second=0, microsecond=0)
    spacing = timedelta(minutes=HYDRATION_SPACING_MIN)
    span = spacing * (HYDRATION_SLOTS - 1)
    if first + span > latest:
        # Compress to finish by 8pm — but never below MIN: a wake so late that the
        # whole run cannot fit would otherwise collapse to six nudges at the same
        # minute, i.e. six messages at once. Spaced and slightly late beats stacked.
        spacing = max(HYDRATION_MIN_SPACING, (latest - first) / (HYDRATION_SLOTS - 1))
    return [first + spacing * i for i in range(HYDRATION_SLOTS)]


def _retime_hydration_nudges(wake_dt, tz) -> None:
    """Point the six hydration nudges at today's waking hours.

    WHY (2026-10-13): they were six fixed crons, 8:15 to 18:15, and nothing in
    that skill ever read the alarm. Those times are exactly wake + 45 min at
    2-hour spacing for the 7:30 start they were written against — so the rhythm
    was right and only the anchor was missing. On the noon alarm the user tried, half
    of them fired before he was awake and he was left chasing 96oz in half the
    waking hours. Same shape as the meal windows and the supplement nudge:
    the cadence follows the alarm, it is not a wall-clock.
    """
    failures = []
    for slot, when in enumerate(hydration_times(wake_dt, tz), start=1):
        name = HYDRATION_CRON_NAME.format(slot=slot)
        try:
            job_id = _job_id_by_name(name)
            local = when.astimezone(tz)
            r = subprocess.run(
                [OPENCLAW_BIN, "cron", "edit", job_id,
                 "--cron", f"{local.minute} {local.hour} * * *", "--tz", str(tz)],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                failures.append(f"{name}: {(r.stderr or r.stdout).strip()[:120]}")
            elif not _cron_enabled(job_id):
                # `cron edit` succeeds on a DISABLED job, so its exit code proves
                # nothing (feedback-cron-edit-succeeds-on-disabled-job).
                failures.append(f"{name}: retimed to {local:%H:%M} but the job is disabled")
        except Exception as e:  # noqa: BLE001
            failures.append(f"{name}: {e}")
    if failures:
        raise RuntimeError("hydration nudges — " + "; ".join(failures))


PREMEAL_CRON_NAME = "physician-premeal-break-slot{slot}"
PREMEAL_SLOTS = (1, 2, 3)


def _retime_premeal_breaks(times: dict, tz) -> None:
    """Put each pre-meal break back in front of its meal.

    The physician's plan (2026-09-15) asks for a short break BEFORE MEALS. The
    11:30/14:30/17:30 the pings used were chosen during the 2026-09-18
    reconciliation, not by the physician, and once the meal windows began
    cadencing off the alarm they drifted onto the wrong side of the meal — on an
    8:30 wake all three landed about half an hour AFTER eating. The user chose
    "before meals" on 2026-10-13.

    The lead time and the meal each slot belongs to live in send_premeal_break.py,
    so the cron and the script's own misfire guard read one definition."""
    sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "dr-physician" / "scripts"))
    import send_premeal_break as premeal
    plan = {"slots": {str(slot): when.isoformat() for slot, when in times.items()}}
    failures = []
    for slot in PREMEAL_SLOTS:
        due = premeal.due_at(slot, plan)
        if due is None:
            failures.append(f"slot {slot}: no meal {premeal.MEAL_FOR_SLOT[slot]} in today's plan")
            continue
        name = PREMEAL_CRON_NAME.format(slot=slot)
        try:
            job_id = _job_id_by_name(name)
            local = due.astimezone(tz)
            r = subprocess.run(
                [OPENCLAW_BIN, "cron", "edit", job_id,
                 "--cron", f"{local.minute} {local.hour} * * *", "--tz", str(tz)],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                failures.append(f"{name}: {(r.stderr or r.stdout).strip()[:120]}")
            elif not _cron_enabled(job_id):
                failures.append(f"{name}: retimed to {local:%H:%M} but the job is disabled")
        except Exception as e:  # noqa: BLE001
            failures.append(f"{name}: {e}")
    if failures:
        raise RuntimeError("pre-meal breaks — " + "; ".join(failures))


OPEN_LOOP_CRON_NAME = "open-loop-chase"   # no time in the name: it moves nightly
OPEN_LOOP_AFTER_WAKE = timedelta(hours=2)


def _retime_open_loop_chase(wake_dt, tz) -> None:
    """Chase unanswered asks two hours after the user is up.

    It was a fixed 10:45am, which is wake + 2h on the 8:45 start it was written
    against — so, like the hydration nudges, the interval was already right and
    only the anchor was missing. On a late wake it fired before he was awake and
    the nudge was buried by the time he saw it (The user, 2026-10-13: "yeah that
    works"). Nothing here is clinical or physical; it just has to arrive while he
    is awake enough to act on it."""
    job_id = _job_id_by_name(OPEN_LOOP_CRON_NAME)
    local = (wake_dt + OPEN_LOOP_AFTER_WAKE).astimezone(tz)
    r = subprocess.run(
        [OPENCLAW_BIN, "cron", "edit", job_id,
         "--cron", f"{local.minute} {local.hour} * * *", "--tz", str(tz)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError("open-loop chase: " + (r.stderr or r.stdout).strip()[:200])
    if not _cron_enabled(job_id):
        raise RuntimeError(f"open-loop chase retimed to {local:%H:%M}, but the job is disabled")


CONSOLE_CRON_NAME = "govee-morning-energize"
GOVEE_CONTROL = str(SCRIPTS_DIR.parent.parent / "govee-lights" / "scripts" / "control.py")


def _retime_console_morning(wake_dt, tz) -> None:
    """Bring the desk light up with the user, not at a fixed 7:55.

    The BEDROOM light has followed the alarm since 2026-09; the console never
    did, so on a late wake it ran 100% / 6500K at an empty chair for hours
    (2026-10-13). console_schedule.morning_hhmm() owns the time — the curve that
    the post-Zoom-call restore reads and this cron must not disagree, which is
    what the module docstring there warns about.

    The argv carries the job's own --expected-start for the misfire guard, so it
    is rewritten too: retiming the schedule alone would leave the guard refusing
    the new time as a catch-up (the same trap the pre-meal breaks had)."""
    sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "govee-lights" / "scripts"))
    import console_schedule
    hhmm = console_schedule.morning_hhmm()
    hour, minute = (int(x) for x in hhmm.split(":"))
    job_id = _job_id_by_name(CONSOLE_CRON_NAME)
    argv = ["python3", GOVEE_CONTROL, "--light", "console_light", "--on",
            "--brightness", "100", "--temp", "6500", "--skip-if-away",
            "--expected-start", hhmm, "--late-grace-min", "180"]
    r = subprocess.run(
        [OPENCLAW_BIN, "cron", "edit", job_id,
         "--cron", f"{minute} {hour} * * *", "--tz", str(tz),
         "--command-argv", json.dumps(argv)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError("console morning: " + (r.stderr or r.stdout).strip()[:200])
    if not _cron_enabled(job_id):
        raise RuntimeError(f"console morning retimed to {hhmm}, but the job is disabled")


def _job_id_by_name(name: str) -> str:
    r = subprocess.run([OPENCLAW_BIN, "cron", "list", "--all", "--json"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("could not read the cron list: " + (r.stderr or r.stdout).strip()[:200])
    for job in (json.loads(r.stdout).get("jobs") or []):
        if job.get("name") == name and job.get("id"):
            return job["id"]
    raise RuntimeError(f"no cron job named {name}")


def _cron_enabled(job_id: str) -> bool:
    r = subprocess.run([OPENCLAW_BIN, "cron", "list", "--all", "--json"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return False
    for job in (json.loads(r.stdout).get("jobs") or []):
        if job.get("id") == job_id:
            return bool(job.get("enabled"))
    return False


def _retime_meal_crons(times: dict, tz) -> None:
    """Point the five standing meal crons at today's computed window times.

    2026-10-06: until now this ADDED five one-shot jobs while the five standing crons
    stayed at their old fixed times, so every slot had two independent senders and
    send_meal_reminder.py deduped between them through an unlocked read-then-write. Four
    of the five pairs sat on the same minute, so the dedup was a race: on 10-06 slots 4
    and 5 both sent twice (messages 13794/13795 and 13803/13804), and only one of each
    pair could be ticked in place because the state remembers one message per slot.

    A backstop that exists because the primary is not trusted is a watchdog, and the fix
    for a watchdog is to make the primary trustworthy. One trigger per slot now: these
    crons ARE the schedule, retimed daily. If this fails they keep yesterday's times and
    still fire -- close to right, and loudly rather than silently.
    """
    ids = _meal_job_ids()
    for slot, when in sorted(times.items()):
        local = when.astimezone(tz)
        r = subprocess.run(
            [OPENCLAW_BIN, "cron", "edit", ids[slot],
             "--cron", f"{local.minute} {local.hour} * * *", "--tz", str(tz)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(f"slot {slot}: " + (r.stderr or r.stdout).strip()[:200])
    # `cron edit` succeeds on a DISABLED job, so its exit code says nothing about whether
    # tomorrow's reminder will happen -- the bedroom sunrise missed two mornings exactly
    # this way while its sync reported ok (2026-10-06).
    dead = _disabled_meal_crons(ids)
    if dead:
        raise RuntimeError("retimed, but these will never run: " + "; ".join(dead))


def _disabled_meal_crons(ids: dict) -> list:
    """Which of the meal crons the gateway will not run. [] when all five are live."""
    r = subprocess.run([OPENCLAW_BIN, "cron", "list", "--all", "--json"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return ["could not re-read the cron list to confirm the meal jobs are enabled"]
    try:
        jobs = {j.get("id"): j for j in (json.loads(r.stdout).get("jobs") or [])}
    except (ValueError, AttributeError):
        return ["could not parse the cron list to confirm the meal jobs are enabled"]
    out = []
    for slot, job_id in sorted(ids.items()):
        job = jobs.get(job_id)
        if job is None:
            out.append(f"slot {slot} ({job_id}) vanished from the cron list")
        elif not job.get("enabled"):
            out.append(f"{job.get('name') or job_id} is disabled")
    return out


def _cancel_oneshots(prefix: str) -> None:
    """Delete every one-shot job whose name starts with `prefix`, so the
    caller can then add a fresh one idempotently.

    `openclaw cron add <name>` does NOT replace a same-named job — it creates
    a duplicate (verified 2026-08-28) — so the only safe way to "re-create
    tonight's job" is to clear all matching jobs first, today's included.
    That also sweeps up stale jobs from a previous day whose
    --delete-after-run never fired (missed/failed run). Mirrors
    email-monitor's _cancel_stale_flight_reminders."""
    try:
        r = subprocess.run(
            [OPENCLAW_BIN, "cron", "list", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return
        jobs = json.loads(r.stdout).get("jobs", [])
    except Exception:
        return
    for job in jobs:
        if job.get("name", "").startswith(prefix):
            subprocess.run([OPENCLAW_BIN, "cron", "rm", job.get("id", "")],
                            capture_output=True, text=True, timeout=30)


def _travel_wake_message(alarm_result: dict) -> str:
    """The wake-nudge text — mirrors the phone-alarm instruction from the
    9pm brief so the two are consistent."""
    if alarm_result.get("is_default"):
        return "⏰ Wake up — no early commitment tomorrow (default time). Ignore if you're sleeping in. 🎩"
    return (
        f"⏰ Wake up — '{alarm_result['event_title']}' at {alarm_result['event_start_fmt']}, "
        f"leave by {alarm_result['leave_time_fmt']}. 🎩"
    )


def schedule_travel_wake_nudge(wake_dt, alarm_result: dict) -> None:
    """Schedule a one-shot Telegram backup nudge at the computed wake time —
    the failsafe behind the user's phone alarm. Fires whenever tomorrow is
    travel-sensitive: he's already away (Hatch disabled, sitting in an empty
    house) OR tomorrow's calendar has a flight (he flies out in the morning
    and, even if the Hatch is still set, a 4am departure wake is not
    something to leave to one device). The flight signal matters because
    is_away_from_home() doesn't flip until detect_travel.py runs mid-trip —
    never the night before — so keying only on `away` lost him the nudge on
    2026-09-01. See feedback-travel-detection-lag.

    No-op when the wake compute failed (no reliable time to fire at — the
    9pm brief still tells him what it could), and on default-time days: a
    backup alarm only earns its interruption when there's an actual early
    commitment. With nothing on the calendar the morning ping is just noise
    — the 9pm brief already told him the day's clear."""
    travel_sensitive = alarm_result.get("away") or alarm_result.get("flight_tomorrow")
    if not travel_sensitive or wake_dt is None or alarm_result.get("is_default"):
        return

    job_name = f"{TRAVEL_WAKE_JOB_PREFIX}{wake_dt.date().isoformat()}"
    _cancel_oneshots(TRAVEL_WAKE_JOB_PREFIX)

    cmd = [
        OPENCLAW_BIN, "cron", "add", job_name,
        "--at", wake_dt.isoformat(),
        "--command-argv", json.dumps([
            "python3", str(TRAVEL_WAKE_NUDGE),
            "--message", _travel_wake_message(alarm_result),
            "--repeats", str(TRAVEL_WAKE_NUDGE_REPEATS),
            "--interval-seconds", str(TRAVEL_WAKE_NUDGE_INTERVAL_SECONDS),
        ]),
        "--session", "isolated",
        "--channel", "telegram",
        "--to", TG_CHAT_ID,
        "--delete-after-run",
        "--no-deliver",  # travel_wake_nudge.py self-sends; see feedback-nutrition-cron-double-delivery
        "--timeout-seconds", str(TRAVEL_WAKE_NUDGE_REPEATS * TRAVEL_WAKE_NUDGE_INTERVAL_SECONDS + 120),
        "--no-output-timeout-seconds", str(TRAVEL_WAKE_NUDGE_INTERVAL_SECONDS + 120),
        "--json",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip())
        out = json.loads(r.stdout)
        if not out.get("id"):
            raise RuntimeError(f"no job id returned: {r.stdout.strip()}")
    except Exception as e:
        # A scheduling failure shouldn't leave the user with no backup at all —
        # tell him now so he definitely sets the phone alarm himself.
        notify(
            f"⚠️ Couldn't schedule the backup wake nudge for {wake_dt.strftime('%-I:%M %p')} "
            f"({str(e)[:150]}). Set your phone alarm manually — you're on your own for the wake-up. 🎩"
        )


def schedule_breakfast_anchor_reminder(wake_dt, tz, config: dict,
                                       send_now_on_failure: bool = True) -> None:
    """Every day: schedule all five eating windows off the user's alarm.

    Slot 1 is wake + N minutes (N = meal_timing.breakfast_after_wake_minutes, <= 60) for
    the physician's "breakfast within 60 minutes of waking" guidance (2026-09-10). Slots 2-5 follow
    it by the gaps in meal_timing, computed by nutrition/scripts/meal_schedule.py.

    2026-10-05: slots 2-5 used to be fixed wall-clock crons, so moving the alarm moved
    breakfast and left the rest of the day where it was — on Sunday 2026-10-04 a 09:30
    alarm put breakfast and the fixed snack cron in the same minute. The user: "the meals are
    not anchored to cadence but rather timing."

    Static backstop crons (nutrition-meal-reminder-slot1..5) still
    fire; send_meal_reminder.py dedups each slot per day, and holds the
    backstop back while BREAKFAST_PLAN_FILE says the one-shot is still ahead.

    2026-09-19: also called pre-dawn by refresh_breakfast_anchor.py with the
    LIVE Hatch alarm, so an alarm the user changes himself after the 21:00 sync
    still moves the reminder ("7:30 unless I change it"). That caller passes
    send_now_on_failure=False — a 3am run must never send the message.

    Skipped when away from home — meal reminders already suppress on travel,
    and the wake compute is phone-alarm-driven then anyway. Falls back to
    alarm_config default_wake_time if tonight's wake compute failed; falls
    back to firing the reminder now if the cron add itself fails."""
    if is_away_from_home():
        _cancel_oneshots(BREAKFAST_JOB_PREFIX)
        BREAKFAST_PLAN_FILE.unlink(missing_ok=True)
        return

    try:
        after_min = int(json.loads(NUTRITION_CONFIG.read_text())
                        .get("meal_timing", {}).get("breakfast_after_wake_minutes",
                                                    DEFAULT_BREAKFAST_AFTER_WAKE_MIN))
    except Exception:
        after_min = DEFAULT_BREAKFAST_AFTER_WAKE_MIN
    after_min = max(0, min(after_min, 60))

    if wake_dt is not None:
        base = wake_dt
    else:
        hh, mm = (int(p) for p in config["default_wake_time"].split(":"))
        tomorrow = datetime.now(tz).date() + timedelta(days=1)
        base = datetime.combine(tomorrow, dtime(hh, mm), tzinfo=tz)

    # 2026-10-05: all five windows, not just breakfast. The user: "if I move my alarm back,
    # the meals are not anchored to cadence but rather timing." meal_schedule owns the
    # arithmetic; this only turns its times into one-shot crons.
    sys.path.insert(0, str(NUTRITION_DIR / "scripts"))
    import meal_schedule
    try:
        nutrition_cfg = json.loads(NUTRITION_CONFIG.read_text())
    except Exception:
        nutrition_cfg = {}
    times = meal_schedule.day_times(base, nutrition_cfg)
    reminder_dt = times[1]
    _cancel_oneshots(BREAKFAST_JOB_PREFIX)

    try:
        _retime_meal_crons(times, tz)
        BREAKFAST_PLAN_FILE.write_text(json.dumps({
            "date": base.date().isoformat(),
            "wake_at": base.isoformat(),
            "fire_at": reminder_dt.isoformat(),
            "slots": {str(slot): when.isoformat() for slot, when in sorted(times.items())},
            "squeezed": meal_schedule.squeezed(base, nutrition_cfg),
        }))
        plan_written = True
    except Exception as e:
        plan_written = False
        # No one-shot is pending now, so nothing for the backstop to wait on.
        BREAKFAST_PLAN_FILE.unlink(missing_ok=True)
        if not send_now_on_failure:
            print(f"breakfast anchor reschedule failed: {e}", file=sys.stderr)
            return
        subprocess.run([sys.executable, str(SEND_MEAL_REMINDER), "--slot", "1"],
                        capture_output=True, text=True, timeout=30)
        notify(f"⚠️ Couldn't schedule tomorrow's Breakfast Anchor reminder ({str(e)[:120]}) — "
               f"sent one now instead. 🎩")

    # Deliberately OUTSIDE the block above, and after the plan is on disk. The
    # supplement nudge is strictly less important than the meal plan, and that
    # except clause DELETES the plan, fires a meal reminder immediately and
    # messages the user — so a problem retiming one supplement must never be able
    # to trigger it. Reported on stderr and left at yesterday's time: close to
    # right and visible, rather than silently cascading.
    if plan_written:
        try:
            _retime_supplement_nudge(times, tz)
        except Exception as e:  # noqa: BLE001 - see the comment above
            print(f"supplement nudge reschedule failed: {e}", file=sys.stderr)
        try:
            _retime_hydration_nudges(base, tz)
        except Exception as e:  # noqa: BLE001 - same reasoning: never cascade
            print(f"hydration nudge reschedule failed: {e}", file=sys.stderr)
        try:
            _retime_premeal_breaks(times, tz)
        except Exception as e:  # noqa: BLE001 - same reasoning: never cascade
            print(f"pre-meal break reschedule failed: {e}", file=sys.stderr)
        try:
            _retime_open_loop_chase(base, tz)
        except Exception as e:  # noqa: BLE001 - same reasoning: never cascade
            print(f"open-loop chase reschedule failed: {e}", file=sys.stderr)
        try:
            _retime_console_morning(base, tz)
        except Exception as e:  # noqa: BLE001 - same reasoning: never cascade
            print(f"console morning reschedule failed: {e}", file=sys.stderr)


def schedule_morning_briefing(wake_dt, tz, config: dict,
                              send_now_on_failure: bool = True) -> None:
    """Fire the morning briefing wake + BRIEFING_AFTER_WAKE_MIN.

    2026-09-27, the user: "I just want it to fire when I actually get up as opposed at 8am
    daily. If I choose to sleep in I want it to be dynamic." The old
    morning-briefing-daily-8am cron landed at a fixed hour while his first waking hour
    ranged 6:45–11:33 over ten days, so it was either waiting for him or long stale.

    Unlike the Breakfast Anchor this does NOT skip while travelling: the wake time is
    computed here (calendar + default + any override), never read off the Hatch, so it
    still exists when the Hatch is off in an empty house — and `--at` carries his
    current timezone, so a trip in another time zone fires on local time.

    It follows the ALARM, not him: snooze for an hour and the briefing still arrives at
    alarm + 15. Falls back to the configured default wake time if tonight's compute
    failed. A failed schedule notifies him — it never sends a briefing at 9pm."""
    if wake_dt is not None:
        base = wake_dt
    else:
        hh, mm = (int(p) for p in config["default_wake_time"].split(":"))
        tomorrow = datetime.now(tz).date() + timedelta(days=1)
        base = datetime.combine(tomorrow, dtime(hh, mm), tzinfo=tz)

    fire_dt = base + timedelta(minutes=BRIEFING_AFTER_WAKE_MIN)
    job_name = f"{BRIEFING_JOB_PREFIX}{fire_dt.date().isoformat()}"
    _cancel_oneshots(BRIEFING_JOB_PREFIX)

    cmd = [
        OPENCLAW_BIN, "cron", "add", job_name,
        "--at", fire_dt.isoformat(),
        "--command-argv", json.dumps(["python3", str(RUN_MORNING_BRIEFING)]),
        "--session", "isolated",
        "--channel", "telegram",
        "--to", TG_CHAT_ID,
        "--delete-after-run",
        "--no-deliver",  # run_morning_briefing.py self-sends; see feedback-telegram-reconnect-delivery-race
        "--timeout-seconds", "300",
        "--json",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip())
        out = json.loads(r.stdout)
        if not out.get("id"):
            raise RuntimeError(f"no job id returned: {r.stdout.strip()}")
        BRIEFING_PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        BRIEFING_PLAN_FILE.write_text(json.dumps({
            "date": base.date().isoformat(),
            "wake_at": base.isoformat(),
            "fire_at": fire_dt.isoformat(),
        }))
    except Exception as e:
        BRIEFING_PLAN_FILE.unlink(missing_ok=True)
        if not send_now_on_failure:
            print(f"morning briefing reschedule failed: {e}", file=sys.stderr)
            return
        notify(f"⚠️ Couldn't schedule tomorrow's morning briefing ({str(e)[:120]}) — "
               f"no briefing will arrive on its own. Ask me for it when you're up. 🎩")


# 2026-09-10: PAUSED at the physician's request for the current phase.
# The function is kept intact; main() just doesn't call it while paused, and
# sweeps any pending one-shot. Flip WEEKLY_CHECKIN_REMINDER_PAUSED back to False (or
# remove the guard in main()) when the physician clears it again.
WEEKLY_CHECKIN_REMINDER_PAUSED = True


def schedule_weekly_checkin_reminder(weekday: str, wake_dt, tz, config: dict) -> None:
    """Mondays only: fire the weekly check-in reminder ~5 minutes after tomorrow's
    actual computed wake time instead of a fixed clock time.

    Was a static "0 10 * * 1" cron — fine most weeks, but on a Monday with
    an early out-of-house commitment (e.g. an 8am breakfast) it fired hours
    after the user had already left, and the check-in only makes sense at home
    from home. Wake time already accounts for tomorrow's real calendar (see
    compute_wake above), so anchoring the reminder to it instead means it
    lands while the user's still home regardless of how the rest of the day
    shakes out. Falls back to alarm_config's default_wake_time if tonight's
    wake compute itself failed, so a Hatch-side outage never silently loses
    the reminder outright — see sync_alarm.py's own fail-safe philosophy.
    """
    if weekday != "monday":
        return

    if is_away_from_home():
        return  # no bathroom scale on the road — see current_location.is_away_from_home

    if wake_dt is not None:
        base = wake_dt
    else:
        hh, mm = (int(p) for p in config["default_wake_time"].split(":"))
        tomorrow = datetime.now(tz).date() + timedelta(days=1)
        base = datetime.combine(tomorrow, dtime(hh, mm), tzinfo=tz)

    reminder_dt = base + timedelta(minutes=WEEKLY_CHECKIN_REMINDER_AFTER_WAKE_MINUTES)
    job_name = f"{WEEKLY_CHECKIN_JOB_PREFIX}{reminder_dt.date().isoformat()}"

    _cancel_oneshots(WEEKLY_CHECKIN_JOB_PREFIX)

    cmd = [
        OPENCLAW_BIN, "cron", "add", job_name,
        "--at", reminder_dt.isoformat(),
        "--command-argv", json.dumps(["python3", str(SEND_TELEGRAM), "--text", WEEKLY_CHECKIN_MESSAGE]),
        "--session", "isolated",
        "--channel", "telegram",
        "--to", TG_CHAT_ID,
        "--delete-after-run",
        "--no-deliver",  # payload self-sends via send_telegram.py; runner
                          # delivery would otherwise announce raw stdout too
                          # (see feedback-nutrition-cron-double-delivery)
        "--json",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip())
        out = json.loads(r.stdout)
        if not out.get("id"):
            raise RuntimeError(f"no job id returned: {r.stdout.strip()}")
    except Exception as e:
        # Never let a scheduling failure mean total silence tomorrow —
        # fall back to sending the reminder right now instead of losing it.
        notify(f"{WEEKLY_CHECKIN_MESSAGE}\n\n(couldn't schedule for wake time — sent now instead: {str(e)[:150]})")


def format_message(alarm_result: dict, workout_result: dict) -> str:
    # Travelling: The user wants ONLY the wake-up info in this message — no
    # workout line, no back-to-back conflict lines, no "backup nudge
    # scheduled" tail. Whatever the workout booker did on a trip stays
    # silent here (it still runs — see main()).
    travel_sensitive = alarm_result.get("away") or alarm_result.get("flight_tomorrow")
    if travel_sensitive and not alarm_result["sync_failed"]:
        if alarm_result.get("away"):
            if alarm_result["is_default"]:
                return (
                    f"📱 Away — Hatch off. Set your phone alarm for "
                    f"{alarm_result['wake_time_fmt']} (nothing early tomorrow). 🎩"
                )
            return (
                f"📱 Away — Hatch off. Set your phone alarm for {alarm_result['wake_time_fmt']} — "
                f"'{alarm_result['event_title']}' (actually {alarm_result['event_start_fmt']}), "
                f"leave by {alarm_result['leave_time_fmt']}. 🎩"
            )
        # Flying out tomorrow but still home tonight: the Hatch is set, but an
        # early-departure wake gets a phone-alarm backup + nudge too.
        if alarm_result["is_default"]:
            return (
                f"🛫 Travel day tomorrow — Hatch set for {alarm_result['wake_time_fmt']}. "
                f"Set a phone alarm as backup too (nudge scheduled). 🎩"
            )
        return (
            f"🛫 Travel day tomorrow — Hatch set for {alarm_result['wake_time_fmt']} for "
            f"'{alarm_result['event_title']}' (actually {alarm_result['event_start_fmt']}), "
            f"leave by {alarm_result['leave_time_fmt']}. Set a phone alarm as backup too "
            f"(nudge scheduled). 🎩"
        )

    if alarm_result["sync_failed"]:
        # "check manually with alarm.py --list" was a command in a message to the user.
        known = alarm_result["wake_time_fmt"] or "a time I couldn't read"
        lines = [f"⏰ Alarm: sync failed ({alarm_result['error']}) — currently set to {known}."]
    elif alarm_result.get("is_manual"):
        # 2026-09-20: a time the user set himself is kept for that morning (wake_override.py).
        lines = [f"⏰ Alarm: {alarm_result['wake_time_fmt']} (the time you set)."]
    elif alarm_result["is_default"]:
        lines = [f"⏰ Alarm: {alarm_result['wake_time_fmt']} (default)."]
    else:
        lines = [
            f"⏰ Alarm: {alarm_result['wake_time_fmt']} — '{alarm_result['event_title']}' "
            f"(actually {alarm_result['event_start_fmt']}) means leaving by "
            f"{alarm_result['leave_time_fmt']}."
        ]
        if alarm_result.get("manual_wake_time_fmt"):
            lines[0] += f" That is earlier than the {alarm_result['manual_wake_time_fmt']} you set."

    status = workout_result.get("status")
    # Light-movement single-block shape (schedule_recovery_movement) has a flat
    # "title"/"start" pair and no "booked"/"failed" lists — detect it by shape,
    # not by matching the activity name in the title. A 2026-09-11 rename of
    # the activity from "Recovery Movement" to "Swim" broke a title-string
    # match here and silently dropped the whole workout line (or, on a booking
    # failure, fell through to the generic "unknown" error below) — see
    # feedback on the 2026-09-11 21:00 run.
    is_recovery_block = "title" in workout_result and "booked" not in workout_result \
        and "failed" not in workout_result
    if is_recovery_block and status == "success":
        # A light-movement block, not a lift.
        start = workout_result.get("start", "")[11:16] or "?"
        activity = workout_result.get("title", "Recovery movement").split(" <> ")[0]
        lines.append(f"🚶 {activity}: {start} (45 min, light movement).")
    elif is_recovery_block and status == "already_booked":
        activity = workout_result.get("title", "Recovery movement").split(" <> ")[0]
        lines.append(f"🚶 {activity} already booked: {workout_result.get('start','')[11:16] or '?'}.")
    elif status == "success":
        for b in workout_result.get("booked", []):
            label = "Already booked" if b.get("already_booked") else "Workout"
            lines.append(f"🏋️ {label}: {b.get('workout_start', b.get('arrive_at', '?'))}.")
        for f in workout_result.get("failed", []):
            lines.append(f"⚠️ Workout not booked: {f.get('reason', 'unknown')}.")
    elif status in ("rest_day", "gym_closed", "skipped", "vacation_mode", "not_started",
                    "disabled", "travel_day", "recovery_rest", "recovery_mode"):
        pass  # nothing to say — matches schedule_workouts.py's own quiet-by-design cases
        # (travel_day: schedule_workouts already sent its own ✈️ Telegram)
    elif status == "failed" and workout_result.get("failed"):
        for f in workout_result["failed"]:
            lines.append(f"⚠️ Workout not booked: {f.get('reason', 'unknown')}.")
    else:
        reason = workout_result.get("reason") or workout_result.get("message", "unknown")
        lines.append(f"⚠️ Workout booking error: {reason}.")

    for c in alarm_result.get("conflicts", []):
        lines.append(
            f"⚠️ Can't make it: '{c['to_event']}' at {c['to_start_fmt']} needs you "
            f"leaving '{c['from_event']}' by {c['required_leave_fmt']}, but it doesn't "
            f"end until {c['from_end_fmt']} — {c['shortfall_minutes']} min short."
        )
    return "\n".join(lines) + " 🎩"


def main() -> None:
    ok, reason = within_slot(None, MISFIRE_SLOT[0], MISFIRE_SLOT[1])
    if not ok:
        print(f"misfire-guard: {reason} — skipping nightly wake+workout sync",
              file=sys.stderr)
        return

    config = sync_alarm.load_config()
    tz = get_tz()
    tomorrow = datetime.now(tz).date() + timedelta(days=1)
    weekday = tomorrow.strftime("%A").lower()

    alarm_result = compute_wake(config, tz)
    wake_dt = datetime.fromisoformat(alarm_result["wake_dt_iso"]) if alarm_result.get("wake_dt_iso") else None

    if WEEKLY_CHECKIN_REMINDER_PAUSED:
        _cancel_oneshots(WEEKLY_CHECKIN_JOB_PREFIX)  # sweep any already-scheduled one-shot
    else:
        schedule_weekly_checkin_reminder(weekday, wake_dt, tz, config)
    schedule_breakfast_anchor_reminder(wake_dt, tz, config)
    schedule_morning_briefing(wake_dt, tz, config)
    schedule_travel_wake_nudge(wake_dt, alarm_result)

    workout_result = book_workout()

    try:
        post_events = sync_alarm.fetch_tomorrow_events()
        conflicts = sync_alarm.find_back_to_back_conflicts(post_events, tz, config)
    except Exception:
        conflicts = []
    alarm_result["conflicts"] = conflicts

    msg = format_message(alarm_result, workout_result)
    if alarm_result.get("flight_tomorrow"):
        # Travel-kit rule — pack it the night before, not at the airport.
        msg += "\n🧳 Pack your travel kit tonight, not at the airport."
    notify(msg)


if __name__ == "__main__":
    main()
