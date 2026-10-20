#!/usr/bin/env python3
"""The 10pm fade-to-off for the living-room Zengge light — the same curve as the Govee strips'
winddown_fade.py, driven over Bluetooth.

    python3 evening_fade.py --light living_room --duration-seconds 2700 --expected-start 22:00 --detach

The user asked on 2026-10-12 for this light to follow the Govee lights. The colour and brightness
curve are IMPORTED from govee-lights/scripts/winddown_fade.py so there is one source of truth:
Sunset Gold, seven steps down over the duration, then off — reaching off at 10:45pm from a
10:00pm start. The misfire rules are the Govee ones too (on time → fade; late the same night →
straight to off; any other hour → nothing), as is standing down while a night-light / movie
hold is live, and running detached so the cron runner is not held for 45 minutes.

Each step is a brightness-only command (the colour is set once at the start, carrying the
first brightness), confirmed against the light; a step the light disagrees with is logged and
the next step corrects it, because every step is an absolute value. If the light cannot be
reached at all mid-fade, it is sent straight to off rather than left dim overnight.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOVEE_SCRIPTS = HERE.parent.parent / "govee-lights" / "scripts"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "shared"))
sys.path.insert(0, str(GOVEE_SCRIPTS))
from zengge_ble import BleUnavailable, apply, load_devices, render  # noqa: E402
from data_paths import path as data_path  # noqa: E402
from detach import DETACH_FLAG, relaunch_detached  # noqa: E402
from winddown_fade import BRIGHTNESS_CURVE, COLOR, misfire_mode  # noqa: E402  (the Govee curve)
from evening_hold import held  # noqa: E402

FADE_LOG = data_path("zengge_fade_detached.log")


def resolve_targets(name: str) -> list[tuple[str, dict]]:
    devices = load_devices()
    if name == "all":
        return list(devices.items())
    if name not in devices:
        raise KeyError(f"Unknown light nickname {name!r}. Known: {', '.join(devices)}")
    return [(name, devices[name])]


def run_fade(targets, duration_seconds: int, sleep=time.sleep, clock=time.monotonic) -> None:
    """The fade itself: step i lands at start + i * interval, power-off at the full duration."""
    total_steps = len(BRIGHTNESS_CURVE) + 1
    interval = max(duration_seconds, 0) / total_steps
    start = clock()
    try:
        for i, brightness in enumerate(BRIGHTNESS_CURVE):
            rgb, level = render(COLOR, brightness)   # the Govee gold, as these lamps show it
            for nickname, dev in targets:
                if i == 0:
                    done = apply(dev["name"], rgb=rgb, brightness=level)
                else:
                    done = apply(dev["name"], brightness=level)
                print(json.dumps({"step": i + 1, "of": total_steps, "light": nickname, "brightness": brightness,
                                  "verified": done["verified"]}), flush=True)
            sleep(max(start + (i + 1) * interval - clock(), 0))
        print(json.dumps({"waiting": "final power-off"}), flush=True)
        sleep(max(start + duration_seconds - clock(), 0))
        for nickname, dev in targets:
            done = apply(dev["name"], power=False)
            print(json.dumps({"step": total_steps, "of": total_steps, "light": nickname, "power": "off",
                              "verified": done["verified"]}), flush=True)
    except BleUnavailable as exc:
        print(json.dumps({"step_error": str(exc), "recovering_to_final_state": True}), flush=True)
        for nickname, dev in targets:
            try:
                apply(dev["name"], power=False)
            except BleUnavailable as exc2:
                print(json.dumps({"light": nickname, "off_failed": str(exc2)}), flush=True)
        raise


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--light", required=True, help="Nickname from config/zengge_devices.json, or 'all'")
    ap.add_argument("--duration-seconds", type=int, required=True)
    ap.add_argument("--expected-start", metavar="HH:MM",
                    help="The cron job's own scheduled time (govee winddown_fade.misfire_mode). Omit on manual calls.")
    ap.add_argument(DETACH_FLAG, action="store_true",
                    help="Run the fade in a process of its own and return at once (the cron job uses this).")
    args = ap.parse_args()

    mode, reason = "fade", ""
    if args.expected_start:
        mode, reason = misfire_mode(args.expected_start)
        if mode == "skip":
            print(json.dumps({"skipped": reason}))
            return

    if held(args.light):
        print(json.dumps({"skipped": "night light hold is on"}))
        return

    if args.detach:
        print(json.dumps(relaunch_detached(FADE_LOG)))
        return

    try:
        targets = resolve_targets(args.light)
        if mode == "off":
            print(json.dumps({"late_run": reason, "action": "power off, no fade"}), flush=True)
            for nickname, dev in targets:
                done = apply(dev["name"], power=False)
                print(json.dumps({"light": nickname, "power": "off", "verified": done["verified"]}), flush=True)
            return
        run_fade(targets, args.duration_seconds)
    except (KeyError, BleUnavailable) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
