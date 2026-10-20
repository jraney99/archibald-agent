"""
Daily job: read the Hatch Restore's sunrise-alarm effective wake time + ramp
duration (kept accurate by hatch-restore's sync_alarm.py, which runs earlier
in the evening-briefing cron) and rewrite two bedroom Govee cron jobs:
  1. govee-bedroom-sunrise      — the bedroom light ramps from dim amber to
     full daylight white over the ramp window, finishing right as the Hatch
     alarm wakes the user up.
  2. govee-bedroom-morning-off  — snaps the bedroom light off at wake + 30 min,
     once the user is up and out of the room. Replaced the old 1pm govee-focus-end
     "--light bedroom --off" (retired 2026-08-31): that 1pm timing was a
     leftover from when focus-end also killed the console at the end of the
     11am-1pm focus block — nothing about it was chosen for the bedroom, so
     the strip sat at 100% white for ~5.5h after wake for no reason.

Why a separate scheduler rather than folding into sync_alarm.py directly:
keeps the two skills decoupled (hatch-restore doesn't need to know Govee
exists) at the cost of one extra Hatch login. Mirrors govee-lights'
sunset_scheduler.py's "read external source of truth, rewrite a cron job's
schedule" pattern.

Must run after tomorrow's alarm time has actually been applied to the
device — scheduled at 9:15pm, a few minutes after
hatch-restore/scripts/nightly_wake_and_workout_sync.py (9:00pm), which is
the current sole source of truth for the nightly wake-time compute+apply
(see hatch-restore/SKILL.md's "Automation: calendar-aware wake time"
section). This ran at 8:12pm for years and silently read stale data for
several days in 2026-08 after the wake sync moved later without this job
moving with it — if the wake sync's cron time ever changes again, this one
needs to move too.
"""

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hatch-restore" / "scripts"))
from hatch_client import load_device_map  # noqa: E402
import hatch_alarm_api as alarm_api  # noqa: E402
from hatch_alarm_api import effective_wake_datetime, _alarm_sunrise_duration_seconds  # noqa: E402
import alarm_cache  # noqa: E402

HATCH_CONFIG = Path(__file__).parent.parent / "hatch-restore" / "config" / "alarm_config.json"
GOVEE_SCHEDULE_CONFIG = Path(__file__).parent.parent / "govee-lights" / "config" / "schedule_config.json"
GOVEE_RAMP_SCRIPT = str(Path(__file__).parent.parent / "govee-lights" / "scripts" / "sunrise_ramp.py")
GOVEE_CONTROL_SCRIPT = str(Path(__file__).parent.parent / "govee-lights" / "scripts" / "control.py")
LIGHT_NICKNAME = "bedroom"
TZ = "America/Chicago"

# How long the bedroom sunrise lasts. It finishes at the Hatch wake time, so it starts
# this long before. The user's choice, so it lives in govee-lights/config/schedule_config.json
# ("sunrise_minutes") and this is only the fallback. History: floored at 30 min on
# 2026-09-09 (a 15-min ramp "hit the max at 7:27"); on 2026-09-21, the first morning the
# sunrise actually ran, 30 min woke him too early and he asked for 7.5. It is exactly his
# setting now, no longer max(Hatch's own sunrise, floor) — "make it 7.5" should mean 7.5
# even if the Hatch app's sunrise length changes.
DEFAULT_SUNRISE_MINUTES = 7.5


def sunrise_seconds() -> int:
    try:
        minutes = float(json.loads(GOVEE_SCHEDULE_CONFIG.read_text()).get("sunrise_minutes", DEFAULT_SUNRISE_MINUTES))
    except Exception:  # noqa: BLE001 — a bad config must never cancel the morning
        minutes = DEFAULT_SUNRISE_MINUTES
    return int(round(min(max(minutes, 1), 60) * 60))
# Cron timeout headroom over the ramp duration (the script sleeps in-process
# for the whole ramp). Applied to govee-bedroom-sunrise on every sync.
RAMP_TIMEOUT_MARGIN_SECONDS = 600

# How long after the Hatch wake time to snap the bedroom light off. The user is
# up and moving (workout is wake + 60 min); 30 min gives margin for a slow
# start without leaving the strip on all morning. Chosen 2026-08-31.
MORNING_OFF_DELAY = timedelta(minutes=30)
# A morning-off replayed after a gateway restart still applies up to 2h late; later
# than that it would switch off a light the user turned on himself (2026-09-21).
MORNING_OFF_LATE_GRACE_MIN = 120


def morning_off_argv(off_hhmm: str) -> list[str]:
    """The whole argv for govee-bedroom-morning-off. `cron edit --command-argv` replaces
    the argv, so the misfire guard's --expected-start must be rewritten with the time."""
    return ["python3", GOVEE_CONTROL_SCRIPT, "--light", LIGHT_NICKNAME, "--off",
            "--expected-start", off_hhmm, "--late-grace-min", str(MORNING_OFF_LATE_GRACE_MIN)]


class HatchPlanError(Exception):
    pass


def read_plan(use_cache: bool = True) -> dict:
    """What the Hatch will do next, read from the device right now:
    {enabled, wake_dt, hatch_duration, ramp_duration, ramp_start_dt}. Raises HatchPlanError.

    The one place the lights learn the wake time. Used by the nightly retime below, by
    alarm.py right after a change, by the 3:05am pre-dawn retime, and by sunrise_ramp.py
    --follow-hatch at the moment the ramp fires — so the lights follow the Hatch however and
    whenever it was set (The user, 2026-09-20: "If I set the alarm to 9:30 am, the lights should
    adjust to that time"). The alarm id changes whenever the alarm is recreated in the app, so
    a stale configured id falls back to the device's only alarm, like sync_alarm does."""
    # An alarm change fans out to several followers in a row, and the Hatch is
    # rate-limited: the bedroom sunrise and the ChiliPad night schedule both come
    # through here, so the second login used to get HTTP 429 and the ChiliPad was
    # left at yesterday's timing (2026-10-12). The first caller does the real read
    # and records it; the ones seconds behind it reuse that. Pass use_cache=False
    # to force the device.
    if use_cache:
        cached, hit = alarm_cache.fresh_plan()
        if hit:
            # ramp_duration is local config, never cached — a change to
            # sunrise_minutes takes effect at once instead of up to
            # MAX_AGE_SECONDS later.
            ramp_duration = sunrise_seconds()
            return {**cached, "ramp_duration": ramp_duration,
                    "ramp_start_dt": cached["wake_dt"] - timedelta(seconds=ramp_duration)}

    hatch_cfg = json.loads(HATCH_CONFIG.read_text())
    mac = load_device_map()[hatch_cfg["device"]]["mac"]
    alarm_id = str(hatch_cfg["alarm_id"])

    token = alarm_api.login()
    alarms = alarm_api.fetch_alarms(token, mac)
    alarm = next((a for a in alarms if str(a.get("id")) == alarm_id), None)
    if alarm is None and len(alarms) == 1:
        alarm = alarms[0]
    if alarm is None:
        raise HatchPlanError(f"alarm {alarm_id} not found")

    wake_dt = effective_wake_datetime(alarm)
    if wake_dt is None:
        raise HatchPlanError("could not parse alarm wake time")

    hatch_duration = _alarm_sunrise_duration_seconds(alarm) or 0
    ramp_duration = sunrise_seconds()
    plan = {"enabled": bool(alarm.get("enabled")), "wake_dt": wake_dt, "hatch_duration": hatch_duration,
            "ramp_duration": ramp_duration, "ramp_start_dt": wake_dt - timedelta(seconds=ramp_duration)}
    alarm_cache.note_plan(plan)
    return plan


def disabled_jobs(job_ids) -> list:
    """Which of these jobs the gateway will NOT run, by name.

    WHY (2026-10-06): The user's bedroom sunrise did not fire for two mornings. Both light
    jobs had been switched off by hand after he turned the lamp off mid-ramp on 10-04,
    and this script retimed them and printed {"status": "ok"} three nights running,
    because `cron edit` succeeds perfectly well on a disabled job. The exit code of the
    edit says nothing about whether tomorrow's sunrise will happen, so it is checked
    separately here.

    Deliberately does NOT re-enable anything. Turning his bedroom lights back on by
    itself is exactly the proactive behaviour he has ruled out; being loud about it is
    the job (govee-reliability-frustration, govee-light-commands).

    Returns [] when every job is live, and None entries are impossible: a job that is
    missing from `--all` is reported by name so a deleted job is not read as healthy.
    """
    wanted = {jid for jid in job_ids if jid}
    if not wanted:
        return []
    probe = subprocess.run(["openclaw", "cron", "list", "--all", "--json"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        return ["could not read the cron list to confirm the jobs are enabled"]
    try:
        jobs = json.loads(probe.stdout).get("jobs") or []
    except (ValueError, AttributeError):
        return ["could not parse the cron list to confirm the jobs are enabled"]
    seen = {j.get("id"): j for j in jobs}
    out = []
    for jid in sorted(wanted):
        job = seen.get(jid)
        if job is None:
            out.append(f"{jid} is not in the cron list at all")
        elif not job.get("enabled"):
            out.append(f"{job.get('name') or jid} is disabled")
    return out


def main() -> None:
    try:
        plan = read_plan()
    except HatchPlanError as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)
    wake_dt, hatch_duration = plan["wake_dt"], plan["hatch_duration"]
    ramp_duration, ramp_start_dt = plan["ramp_duration"], plan["ramp_start_dt"]

    schedule_cfg = json.loads(GOVEE_SCHEDULE_CONFIG.read_text())
    job_id = schedule_cfg["bedroom_sunrise_job_id"]

    cron_expr = f"{ramp_start_dt.minute} {ramp_start_dt.hour} * * *"
    expected_start = f"{ramp_start_dt.hour:02d}:{ramp_start_dt.minute:02d}"
    command_argv = json.dumps([
        "python3", GOVEE_RAMP_SCRIPT, "--light", LIGHT_NICKNAME,
        "--duration-seconds", str(ramp_duration),
        # Both guard flags must be re-added here every night — this rewrites
        # the whole argv, so anything not listed is dropped (the misfire
        # guard silently vanished for weeks this way before 2026-09-09).
        # travel gate — ramp no-ops itself when the user's away from home.
        "--skip-if-away",
        # misfire guard — checks the wall clock against this ramp's real
        # scheduled start, so a 1pm gateway catch-up is skipped but an
        # early-golf wake still runs.
        "--skip-if-misfire", "--expected-start", expected_start,
        # re-read the Hatch when the ramp fires: if the alarm was moved later since this
        # retime (set in the Hatch app at bedtime), the ramp moves itself instead of running.
        "--follow-hatch",
    ])
    ramp_timeout = ramp_duration + RAMP_TIMEOUT_MARGIN_SECONDS

    result = subprocess.run(
        ["openclaw", "cron", "edit", job_id, "--cron", cron_expr, "--tz", TZ,
         "--command-argv", command_argv,
         "--timeout-seconds", str(ramp_timeout)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(json.dumps({"status": "error", "stderr": result.stderr[:500]}))
        sys.exit(1)

    # Retime the morning-off job to wake + MORNING_OFF_DELAY, and its misfire
    # guard's --expected-start with it.
    off_dt = wake_dt + MORNING_OFF_DELAY
    off_cron_expr = f"{off_dt.minute} {off_dt.hour} * * *"
    off_hhmm = f"{off_dt.hour:02d}:{off_dt.minute:02d}"
    off_job_id = schedule_cfg.get("bedroom_morning_off_job_id")
    off_result = None
    if off_job_id:
        off_result = subprocess.run(
            ["openclaw", "cron", "edit", off_job_id,
             "--cron", off_cron_expr, "--tz", TZ,
             "--command-argv", json.dumps(morning_off_argv(off_hhmm))],
            capture_output=True, text=True,
        )
        if off_result.returncode != 0:
            print(json.dumps({
                "status": "error", "stage": "morning-off",
                "stderr": off_result.stderr[:500],
            }))
            sys.exit(1)

    # Retiming a job that will never run is not success. See disabled_jobs().
    not_running = disabled_jobs([job_id, off_job_id])
    report = {
        "status": "ok" if not not_running else "will_not_run",
        "ramp_start_local": expected_start,
        "wake_local": f"{wake_dt.hour:02d}:{wake_dt.minute:02d}",
        "morning_off_local": off_hhmm,
        "duration_seconds": ramp_duration,
        "hatch_duration_seconds": hatch_duration,
        "job_id": job_id,
        "morning_off_job_id": off_job_id,
    }
    if not_running:
        report["error"] = ("retimed, but the morning lights will not fire: "
                           + "; ".join(not_running)
                           + ". Re-enable with: openclaw cron edit <id> --enable")
    print(json.dumps(report))
    if not_running:
        sys.exit(1)


if __name__ == "__main__":
    main()
