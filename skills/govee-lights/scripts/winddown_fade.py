"""
Gradual fade-to-off for one or more Govee lights, ending with the light(s)
fully powered off — the reverse of sunrise_ramp.py. Meant to be invoked
once, at the fade's start time, by a cron job whose --duration-seconds
value determines how long the fade takes before the final power-off.

Holds a fixed color (COLOR below) and steps brightness down through
BRIGHTNESS_CURVE. COLOR matches the user's chosen winddown color ("Sunset
Gold", 2026-08-11) — if the govee-winddown cron's color/brightness is ever
changed, update COLOR here to match; there's no automatic link between the
two jobs.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))
from govee_client import GoveeClient, GoveeError  # noqa: E402
from data_paths import path as data_path  # noqa: E402
from detach import DETACH_FLAG, relaunch_detached  # noqa: E402
from misfire_guard import near_slot  # noqa: E402

CONFIG_PATH = Path(__file__).parent.parent / "config" / "devices.json"

COLOR = (255, 170, 90)  # Sunset Gold, matches govee-winddown
BRIGHTNESS_CURVE = [50, 40, 30, 20, 12, 6, 2]  # last step, then power off

CALL_SPACING_SECONDS = 0.4  # keeps us well under Govee's 2 req/sec/device limit

# --expected-start: a gateway restart replays a "missed" fade at whatever hour it
# comes back (shared/misfire_guard.py). On time → the normal fade. Late but still
# the same night → power off only: the fade opens by setting 50% gold, which would
# turn a light the user already switched off back on at 2am. Any other hour (the next
# afternoon, or before the slot) → do nothing.
MISFIRE_EARLY_GRACE_MIN = 20
FADE_LATE_GRACE_MIN = 30
OFF_LATE_GRACE_MIN = 300  # 22:00 → 03:00, well before the earliest sunrise


def misfire_mode(expected_start: str, now=None) -> tuple[str, str]:
    """'fade', 'off' or 'skip' for a run happening at `now`, and why."""
    ok, reason = near_slot(expected_start, grace_min=MISFIRE_EARLY_GRACE_MIN,
                           late_grace_min=FADE_LATE_GRACE_MIN, now=now)
    if ok:
        return "fade", reason
    ok, reason = near_slot(expected_start, grace_min=MISFIRE_EARLY_GRACE_MIN,
                           late_grace_min=OFF_LATE_GRACE_MIN, now=now)
    return ("off" if ok else "skip"), reason


def load_targets(name: str) -> list[tuple[str, dict]]:
    devices = json.loads(CONFIG_PATH.read_text()).get("devices", {})
    if name == "all":
        return list(devices.items())
    if name not in devices:
        raise GoveeError(f"Unknown light nickname {name!r}.")
    return [(name, devices[name])]


FADE_LOG = data_path("fade_detached.log")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--light", required=True, help="Nickname from config/devices.json, or 'all'")
    ap.add_argument("--duration-seconds", type=int, required=True)
    ap.add_argument("--expected-start", metavar="HH:MM",
                    help="The cron job's own scheduled time (see misfire_mode). Omit on manual calls.")
    ap.add_argument(DETACH_FLAG, action="store_true",
                    help="Run the fade in a process of its own and return at once. The "
                         "cron job uses this: a 45-minute fade inside the cron worker "
                         "freezes every other job (2026-10-08, see shared/detach.py).")
    args = ap.parse_args()

    mode = "fade"
    if args.expected_start:
        mode, reason = misfire_mode(args.expected_start)
        if mode == "skip":
            print(json.dumps({"skipped": reason}))
            return

    from evening_hold import held  # here, not at the top: evening_hold imports this module

    if held(args.light):  # the user asked for a held mode; don't restart the fade above it
        print(json.dumps({"skipped": "night light hold is on"}))
        return

    if args.detach:
        print(json.dumps(relaunch_detached(FADE_LOG)))
        return

    targets = load_targets(args.light)
    client = GoveeClient()

    if mode == "off":
        print(json.dumps({"late_run": reason, "action": "power off, no fade"}), flush=True)
        for nickname, dev in targets:
            client.power(dev["sku"], dev["device"], False)
            time.sleep(CALL_SPACING_SECONDS)
        print(json.dumps({"power": "off"}), flush=True)
        return

    total_steps = len(BRIGHTNESS_CURVE) + 1  # + final power-off
    interval = max(args.duration_seconds, 0) / total_steps

    try:
        for nickname, dev in targets:
            client.clear_effect_modes(dev["sku"], dev["device"])
            time.sleep(CALL_SPACING_SECONDS)
        for nickname, dev in targets:
            client.color_rgb(dev["sku"], dev["device"], *COLOR)
            time.sleep(CALL_SPACING_SECONDS)

        for i, brightness in enumerate(BRIGHTNESS_CURVE):
            for nickname, dev in targets:
                client.brightness(dev["sku"], dev["device"], brightness)
                time.sleep(CALL_SPACING_SECONDS)
            print(json.dumps({"step": i + 1, "of": total_steps, "brightness": brightness}), flush=True)
            time.sleep(max(interval - CALL_SPACING_SECONDS * len(targets), 0))

        # Final interval's wait, so power-off (step 8 of 8) lands at the full
        # duration mark instead of firing right after step 7's sleep — this was
        # missing before, which cut the fade ~1/8 short (e.g. ~39.5min instead
        # of the intended 45min for a 2700s duration). That fix introduced a new
        # bug: this sleep is back-to-back with the loop's own last-iteration
        # sleep, so the gap between the step-7 print and the step-8 print is
        # ~2x interval (~675s for a 2700s duration) — longer than the cron job's
        # 600s no-output timeout, so the job gets SIGTERM'd here every night
        # before power-off ever runs. The heartbeat print below breaks that gap
        # in half so no single silent stretch exceeds one interval.
        print(json.dumps({"waiting": "final power-off"}), flush=True)
        time.sleep(max(interval - CALL_SPACING_SECONDS * len(targets), 0))

        for nickname, dev in targets:
            client.power(dev["sku"], dev["device"], False)
            time.sleep(CALL_SPACING_SECONDS)
        print(json.dumps({"step": total_steps, "of": total_steps, "power": "off"}), flush=True)
    except GoveeError as e:
        # A step failed even after the client's own retries — don't strand
        # the light mid-fade at some arbitrary dim brightness overnight,
        # same failure shape as the 2026-08-17 sunrise-ramp incident. Fail
        # safe by forcing straight to power-off, the fade's actual goal.
        print(json.dumps({"step_error": str(e), "recovering_to_final_state": True}), flush=True)
        for nickname, dev in targets:
            client.power(dev["sku"], dev["device"], False)
            time.sleep(CALL_SPACING_SECONDS)
        raise


if __name__ == "__main__":
    try:
        main()
    except GoveeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
