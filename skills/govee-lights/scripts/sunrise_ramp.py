"""
Gradual multi-step brightness/color ramp for one Govee light, simulating a
sunrise over a fixed duration: dim deep amber -> gold -> full daylight
white. Meant to be invoked once, at the ramp's start time, by a cron job
whose schedule and --duration-seconds value are kept in sync with the Hatch
Restore's sunrise alarm by shared/sync_bedroom_sunrise.py — this script
itself has no knowledge of Hatch, it just runs the ramp it's told to.

Runs in-process for the full duration (sleeping between steps), so the
triggering cron job needs a --timeout-seconds well above --duration-seconds.

Reliability — the H61E6 strips fall off Wi-Fi constantly, worst first thing
in the morning after sitting idle all night:
  - Reconnect grace: the initial power-on is retried for up to
    RECONNECT_GRACE_SECONDS before giving up, so a strip that needs 30-60s
    to rejoin the network still gets its sunrise.
  - No jump-to-white on error: a failed step is retried, then skipped —
    every step sets absolute brightness+color, so the next step that lands
    puts the ramp back on track. The ramp NEVER slams the light to 100%
    white as a failure response (it used to — that's the "wakeup just goes
    straight to bright white" bug). A botched wake-up light fails to a
    gentle warm glow (FALLBACK_STATE), not a floodlight, and still exits
    non-zero so the cron failure alert fires.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))
from govee_client import GoveeClient, GoveeError  # noqa: E402
from current_location import is_away_from_home  # noqa: E402
from data_paths import path  # noqa: E402
from misfire_guard import within_slot, near_slot  # noqa: E402

# STOPPING A SUNRISE IN PROGRESS (2026-10-01). The user: "during the sunrise I'll say stop and it
# turns back on which is super annoying" — switching the light off did nothing, because the next
# step of this ramp sets brightness again ~every 37s and lights it straight back up. Now:
#   1. control.py --off and `sunrise_ramp.py --stop` write STOP_FLAG, and the ramp checks it
#      before every step, so ANY way of saying "off" ends the sunrise, not just the exact words.
#   2. Backstop: before each step the ramp asks the strip itself (LAN) whether it is off, which
#      also catches the Govee app and the plug being pulled. No local answer → carry on ramping;
#      the cloud's state is never trusted for this, it echoes our own last command.
# A stop is a normal ending (exit 0), not a failure, so no alert fires.
STOP_FLAG = path("sunrise_stop.json")

# The ramp only ever legitimately starts in the pre-dawn / early-morning
# window (wake times run ~4:30-9:00am). A run outside this is a gateway
# catch-up of the morning cron that was "missed" — usually because a
# westward --tz shift on travel made an already-completed run look pending
# (see shared/misfire_guard.py). Firing the ramp at, say, 1:13pm just turns
# the bedroom light on to dim amber and leaves it there (happened 2026-09-08).
#
# Two guard modes:
#   --skip-if-misfire --expected-start HH:MM  → near_slot against the ramp's
#     real scheduled start (what sync_bedroom_sunrise.py passes every night).
#     Correct even on a 4:30am golf wake, which the fixed window below would
#     wrongly reject.
#   --skip-if-misfire alone  → the fixed MISFIRE_WINDOW, for manual/legacy use.
MISFIRE_WINDOW = (3, 11)  # [hour_lo, hour_hi) local time, any weekday
MISFIRE_EARLY_GRACE_MIN = 20   # a fire this far before expected-start is a catch-up
MISFIRE_LATE_GRACE_MIN = 30    # a fire later than this is a stale catch-up

CONFIG_PATH = Path(__file__).parent.parent / "config" / "devices.json"

# (brightness 1-100, (r, g, b)) — deep amber pre-dawn glow through to full
# daylight white. Not a literal copy of Hatch's internal color curve (that's
# proprietary); a reasonable visual approximation of a real sunrise.
#
# Brightness is spaced ~perceptually-evenly, not linearly (perceived
# brightness on these strips is roughly logarithmic): a linear 7-point curve
# put ~40% of its steps in the visually-indistinguishable 65-100% range, so
# the light read as "fully on" ~3 min before the ramp actually ended
# (2026-09-09: "hit the max at 7:27" for a 7:30 wake). 13 points over the
# ramp window; the final steps still climb visibly through the last minutes.
CURVE = [
    (1, (60, 20, 5)),
    (2, (95, 35, 10)),
    (4, (135, 55, 18)),
    (7, (175, 80, 30)),
    (11, (210, 105, 45)),
    (16, (255, 135, 60)),
    (23, (255, 155, 80)),
    (32, (255, 175, 105)),
    (43, (255, 195, 135)),
    (55, (255, 215, 170)),
    (69, (255, 232, 205)),
    (84, (255, 245, 232)),
    (100, (255, 255, 255)),
]

CALL_SPACING_SECONDS = 0.4       # keeps us well under Govee's 2 req/sec/device limit
RECONNECT_GRACE_SECONDS = 90     # keep retrying the initial power-on this long
RECONNECT_POLL_SECONDS = 10      # gap between power-on attempts during the grace window
STEP_RETRIES = 2                 # extra retries per ramp step, on top of GoveeClient._post's own
# Gentle warm glow to fall back to if the ramp mostly failed — deliberately
# NOT full white. The user should still wake to something soft, not a floodlight.
FALLBACK_STATE = (35, (255, 180, 90))


# --follow-hatch: how much later than "now" the Hatch's ramp start must be before this run
# stands down. Small differences (a retime rounding, a few minutes) still ramp.
FOLLOW_TOLERANCE_MIN = 10
RETIME_SCRIPT = Path(__file__).parent.parent.parent / "shared" / "sync_bedroom_sunrise.py"


def hatch_verdict(now, read_plan=None, retime=None):
    """Ask the Hatch before ramping. Returns a dict to print when this run should NOT ramp
    (alarm off, or moved later — the job is retimed to the new time), else None = go ahead.

    2026-09-20: the ramp fired at the time the 9:15pm retime gave it, whatever happened to the
    alarm afterwards. The user moved the Hatch to 9:30, the light still came on at 7:00, and he
    took to unplugging the lamp. A Hatch that cannot be read never blocks the sunrise."""
    import subprocess
    try:
        if read_plan is None:
            from sync_bedroom_sunrise import read_plan  # shared/ is on sys.path above
        plan = read_plan()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"follow_hatch": "could not read the Hatch; ramping as scheduled", "why": str(e)[:160]}))
        return None
    if not plan["enabled"]:
        return {"skipped": "Hatch alarm is off — no sunrise"}
    # Only the TIME of day on a Hatch alarm means anything — its stored date is whenever the
    # alarm was last saved (2026-09-20 it read "2026-09-18 9:30 AM"). Compare clock times today.
    start = datetime.combine(now.date(), plan["ramp_start_dt"].time(), tzinfo=now.tzinfo)
    minutes_late = (start - now).total_seconds() / 60
    if minutes_late <= FOLLOW_TOLERANCE_MIN:
        return None
    retimed = (retime or (lambda: subprocess.run([sys.executable, "-W", "ignore", str(RETIME_SCRIPT)],
                                                 capture_output=True, text=True, timeout=120).returncode == 0))()
    return {"deferred": f"Hatch now wakes {plan['wake_dt']:%H:%M}; sunrise moved to {start:%H:%M}",
            "retimed": bool(retimed)}


def request_stop(reason: str = "asked") -> None:
    """End a sunrise in progress. Called by --stop and by control.py whenever a light is
    switched off; harmless when no ramp is running (the next ramp clears the flag first)."""
    STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
    STOP_FLAG.write_text(json.dumps({"at": time.time(), "reason": reason}))


def clear_stop() -> None:
    STOP_FLAG.unlink(missing_ok=True)


def stop_requested(since: float):
    """A stop asked for AFTER this ramp started, else None — so yesterday's stop can't
    cancel today's sunrise."""
    try:
        data = json.loads(STOP_FLAG.read_text())
    except (OSError, ValueError):
        return None
    return data if data.get("at", 0) >= since else None


def switched_off(device_id: str) -> bool:
    """True only when the strip ITSELF reports being off. No local control → False: better to
    finish a sunrise than to cancel one on a guess."""
    try:
        import govee_lan
        return not govee_lan.status(device_id)["on"]
    except Exception:  # noqa: BLE001 — LanUnavailable, no socket, no local control
        return False


def load_device(nickname: str) -> dict:
    devices = json.loads(CONFIG_PATH.read_text()).get("devices", {})
    if nickname not in devices:
        raise GoveeError(f"Unknown light nickname {nickname!r}.")
    return devices[nickname]


def _power_on_with_grace(client: GoveeClient, sku: str, device_id: str) -> bool:
    """Retry the initial power-on until it lands or the grace window runs
    out. The strip is often still mid-Wi-Fi-reconnect at ramp start and
    GoveeClient._post's ~6s of internal retries isn't enough to wait that
    out. Returns True once power-on succeeds, False if still unreachable
    after the full window."""
    deadline = time.monotonic() + RECONNECT_GRACE_SECONDS
    attempt = 0
    while True:
        attempt += 1
        try:
            client.power(sku, device_id, True)
            return True
        except GoveeError as e:
            if time.monotonic() >= deadline:
                print(json.dumps({"power_on_failed": str(e)[:160], "attempts": attempt}), flush=True)
                return False
            print(json.dumps({"waiting_for_device": str(e)[:120], "attempt": attempt}), flush=True)
            time.sleep(RECONNECT_POLL_SECONDS)


def _apply_step(client: GoveeClient, sku: str, device_id: str, brightness: int, rgb: tuple) -> bool:
    """Set one ramp point. Retries a couple times beyond the client's own
    retry budget, then gives up on this point — the next point that lands
    corrects the ramp, since every step is an absolute set, not a delta."""
    r, g, b = rgb
    for tryn in range(STEP_RETRIES + 1):
        try:
            client.brightness(sku, device_id, brightness)
            time.sleep(CALL_SPACING_SECONDS)
            client.color_rgb(sku, device_id, r, g, b)
            return True
        except GoveeError as e:
            if tryn < STEP_RETRIES:
                time.sleep(CALL_SPACING_SECONDS)
            else:
                print(json.dumps({"step_failed": str(e)[:120], "brightness": brightness}), flush=True)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--light", required=True)
    ap.add_argument("--duration-seconds", type=int,
                    help="Required unless --stop: how long the whole ramp takes.")
    ap.add_argument("--stop", action="store_true",
                    help="End a sunrise in progress and switch the light off. What to run when "
                         "The user says stop / I'm up / turn it off during the ramp.")
    ap.add_argument(
        "--skip-if-away",
        action="store_true",
        help="No-op (exit 0) when is_away_from_home() is true — for the scheduled cron job.",
    )
    ap.add_argument(
        "--skip-if-misfire",
        action="store_true",
        help=(
            "No-op (exit 0) on a gateway catch-up run of the morning cron. "
            "With --expected-start, checks against that time; otherwise uses "
            f"the fixed {MISFIRE_WINDOW[0]:02d}:00-{MISFIRE_WINDOW[1]:02d}:00 "
            "window. For the scheduled job only."
        ),
    )
    ap.add_argument(
        "--expected-start",
        metavar="HH:MM",
        help=(
            "The ramp's real scheduled start time (local), written by "
            "sync_bedroom_sunrise.py each night. Makes --skip-if-misfire "
            "check the actual slot instead of a fixed hour window, so an "
            "early-golf wake isn't misread as a misfire."
        ),
    )
    ap.add_argument(
        "--follow-hatch",
        action="store_true",
        help=(
            "Before ramping, read the Hatch. Alarm off -> no sunrise. Alarm now later than this "
            "job was timed for -> retime the job to the new time and exit instead of ramping. "
            "If the Hatch cannot be read, ramp as scheduled. For the scheduled job only."
        ),
    )
    args = ap.parse_args()

    if args.stop:
        request_stop("asked to stop")
        dev = load_device(args.light)
        client = GoveeClient()
        try:
            client.power(dev["sku"], dev["device"], False)
            print(json.dumps({"sunrise": "stopped", "light": args.light, "power": "off"}))
        except GoveeError as e:
            # The flag is already written, so the ramp still ends even if this call failed.
            print(json.dumps({"sunrise": "stopped", "light": args.light, "power_off_failed": str(e)[:160]}))
            sys.exit(1)
        return

    if not args.duration_seconds:
        ap.error("--duration-seconds is required unless --stop")

    if args.follow_hatch:
        verdict = hatch_verdict(datetime.now().astimezone())
        if verdict:
            print(json.dumps(verdict))
            return

    if args.skip_if_misfire:
        if args.expected_start:
            ok, reason = near_slot(
                args.expected_start,
                grace_min=MISFIRE_EARLY_GRACE_MIN,
                late_grace_min=MISFIRE_LATE_GRACE_MIN,
            )
        else:
            ok, reason = within_slot(None, *MISFIRE_WINDOW)
        if not ok:
            print(json.dumps({"skipped": reason}))
            return

    if args.skip_if_away and is_away_from_home():
        print(json.dumps({"skipped": "away from home"}))
        return

    dev = load_device(args.light)
    sku, device_id = dev["sku"], dev["device"]
    client = GoveeClient()

    interval = max(args.duration_seconds, 0) / (len(CURVE) - 1)
    started = time.time()
    clear_stop()  # a stop from a previous morning must not cancel this one

    if not _power_on_with_grace(client, sku, device_id):
        # Genuinely unreachable after the full grace window — nothing a ramp
        # can do. Exit non-zero so the cron failure alert fires.
        print(json.dumps({"error": "device unreachable after reconnect grace; sunrise skipped"}))
        sys.exit(1)

    time.sleep(CALL_SPACING_SECONDS)
    try:
        client.clear_effect_modes(sku, device_id)
    except GoveeError as e:
        print(json.dumps({"clear_effect_modes_failed": str(e)[:120]}), flush=True)
    time.sleep(CALL_SPACING_SECONDS)

    succeeded = 0
    for i, (brightness, rgb) in enumerate(CURVE):
        stop = stop_requested(started)
        if stop or (i and switched_off(device_id)):
            clear_stop()
            print(json.dumps({"sunrise": "stopped", "at_step": i + 1, "of": len(CURVE),
                              "why": (stop or {}).get("reason", "the light was switched off")}))
            return
        if _apply_step(client, sku, device_id, brightness, rgb):
            succeeded += 1
            print(json.dumps({"step": i + 1, "of": len(CURVE), "brightness": brightness, "color": list(rgb)}), flush=True)
        if i < len(CURVE) - 1:
            time.sleep(max(interval - CALL_SPACING_SECONDS, 0))

    if succeeded >= len(CURVE) - 2:
        # Full ramp, or only a step or two dropped and the light still
        # ended at/near full daylight — good enough, exit clean.
        if succeeded != len(CURVE):
            print(json.dumps({"partial_ok": True, "steps_ok": succeeded, "of": len(CURVE)}))
        return

    # The ramp mostly failed. Put the light in a gentle warm state (NOT full
    # white) so the user still wakes to something soft, and exit non-zero so the
    # failure alert fires.
    fb, frgb = FALLBACK_STATE
    landed = _apply_step(client, sku, device_id, fb, frgb)
    print(json.dumps({
        "ramp_failed": True,
        "steps_ok": succeeded,
        "of": len(CURVE),
        "fell_back_to": {"brightness": fb, "color": list(frgb)} if landed else None,
        "fallback_landed": landed,
    }))
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except GoveeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
