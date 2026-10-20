#!/usr/bin/env python3
"""On-demand light modes the user asks for by name — "night light", "movie mode" — each held
until the lights go off for the night. Nothing here is ever scheduled.

    evening_hold.py on [--mode night_light|movie]   # apply that mode and hold it
    evening_hold.py off     # end it now: those lights off, the normal schedule resumes
    evening_hold.py check   # cron: switch off + clear once the hold is due (silent no-op otherwise)
    evening_hold.py status

The modes themselves live in config/light_modes.json — a new mode is an entry there plus its
phrases in ROUTING.md, no new code. `night_light` takes its brightness from winddown_fade's own
curve (fade_step_from_end) rather than keeping a second copy of that value.

(The file is named for the night light because that was the first one, 2026-09-30; the state
file name is registered in data_paths, so both kept their names when `movie` was added 10-01.)

While a hold is live the scheduled evening steps stand down, so nothing brightens the room
back up behind him: the 8pm winddown jobs (control.py --skip-if-held), the console curve and
its post-Zoom-call restore (console_schedule.py) and the 10pm fade (winddown_fade.py) all ask
held() here first.

It ends at 22:45, where the normal fade ends, and the govee-evening-hold-off job switches the
lights off then. Asked for after 22:45 it ends at the next 01:00 instead (the sweeper job), so
a late night can't leave the lights burning until morning; asked for in the small hours or the
morning it gets POST_HOLD_MINUTES and the day's own schedule takes it from there.

An EXPIRED hold counts as no hold at all, so a missed off-run can never suppress tomorrow's
schedule — the worst case is lights left dim until the next scheduled step.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "shared"))
from current_location import get_tz  # noqa: E402
from data_paths import path  # noqa: E402
from misfire_guard import near_slot  # noqa: E402
from winddown_fade import BRIGHTNESS_CURVE, COLOR  # noqa: E402

STATE = path("evening_hold.json")
CONTROL_PY = str(HERE / "control.py")

CONFIG = path("light_modes.json")
# The user tried the night light at the fade's very last step (2) live on 2026-10-01 and found it
# too dark, so it sits one step above (fade_step_from_end 2 → brightness 6).
DEFAULT_MODES = {
    "night_light": {"lights": "all", "color": "255,170,90", "fade_step_from_end": 2},
    "movie": {"lights": "all", "color": "255,140,60", "brightness": 12},
}
EVENING_OFF = (22, 45)             # where the normal fade reaches off
LATE_OFF = (1, 0)                  # backstop for a hold started after the evening one
DAY_START_HOUR = 11                # before this, a dim hold isn't "the evening" any more
POST_HOLD_MINUTES = 45

MISFIRE_EARLY_GRACE_MIN = 20
MISFIRE_LATE_GRACE_MIN = 120  # switching the lights off late is harmless; doing it at noon isn't


def load_modes():
    try:
        return {**DEFAULT_MODES, **(json.loads(CONFIG.read_text()).get("modes") or {})}
    except (OSError, ValueError):
        return dict(DEFAULT_MODES)


def resolve(name):
    """A mode's settings, with fade_step_from_end turned into a brightness."""
    modes = load_modes()
    if name not in modes:
        raise KeyError(f"no light mode named {name!r} in {CONFIG} (have: {', '.join(sorted(modes))})")
    mode = dict(modes[name])
    step = mode.pop("fade_step_from_end", None)
    if step and "brightness" not in mode:
        mode["brightness"] = BRIGHTNESS_CURVE[-step]
    mode.setdefault("lights", "all")
    return mode


def control_args(mode):
    argv = ["--on"]
    if mode.get("brightness") is not None:
        argv += ["--brightness", str(mode["brightness"])]
    if mode.get("color"):
        argv += ["--color", mode["color"]]
    if mode.get("temp"):
        argv += ["--temp", str(mode["temp"])]
    if mode.get("scene"):
        argv += ["--scene", mode["scene"]]
    return argv


def off_time(now):
    """When a hold started at `now` should switch the lights off."""
    evening = now.replace(hour=EVENING_OFF[0], minute=EVENING_OFF[1], second=0, microsecond=0)
    late = now.replace(hour=LATE_OFF[0], minute=LATE_OFF[1], second=0, microsecond=0)
    if now < late:
        return late
    if now.hour < DAY_START_HOUR:
        return now + timedelta(minutes=POST_HOLD_MINUTES)
    if now < evening:
        return evening
    return late + timedelta(days=1)


def _record():
    """The hold on disk, expired or not."""
    try:
        data = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None
    try:
        datetime.fromisoformat(data["off_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return data


def hold(now=None):
    """The LIVE hold, or None. An expired hold is no hold."""
    rec = _record()
    if not rec:
        return None
    now = now or datetime.now(get_tz())
    return rec if now < datetime.fromisoformat(rec["off_at"]) else None


def held(light=None, now=None):
    """What the scheduled evening jobs call to decide whether to stand down.

    `light` is the nickname (or "all") that job drives: a hold only stands down the jobs for
    the lights it actually covers, so a one-light mode can't silence the other light's evening.
    """
    live = hold(now)
    if not live:
        return False
    covers = live.get("lights", "all")
    return light is None or covers == "all" or light == "all" or light == covers


# The living-room light is a different brand (zengge-lights, Bluetooth) and follows the Govee
# evening since 2026-10-12: a hold on "all" covers it too, and so does the 10:45pm off.
ZENGGE_CONTROL = str(HERE.parent.parent / "zengge-lights" / "scripts" / "control.py")
ZENGGE_LIGHTS = ("living_room", "living_room_2")  # its own "all" covers every entry in zengge_devices.json


def _zengge_args(args):
    """The same request for the living-room light, or None when it has no way to do it: it
    has no scenes, and its only white is --white (no colour temperature)."""
    out, it = [], iter(args)
    for a in it:
        if a == "--scene":
            return None
        if a == "--temp":
            next(it, None)
            out.append("--white")
            continue
        out.append(a)
    return out


def _control(lights, *args):
    code = 0
    if lights not in ZENGGE_LIGHTS:
        code = subprocess.run(["python3", CONTROL_PY, "--light", lights, *args],
                              capture_output=True, text=True).returncode
    if lights == "all" or lights in ZENGGE_LIGHTS:
        zargs = _zengge_args(args)
        if zargs is None:
            return code if lights == "all" else 1   # a scene can't be held on that light alone
        zcode = subprocess.run(["python3", ZENGGE_CONTROL, "--light", lights, *zargs],
                               capture_output=True, text=True).returncode
        code = code or zcode
    return code


def turn_on(name="night_light", now=None, scene=None, lights=None):
    """`scene` holds one of the strips' own scenes by name without it being a configured mode —
    The user naming any of the 156 ("war movie" → "War Films") should behave like a mode, not like a
    bare scene change the 8pm winddown then stomps."""
    now = now or datetime.now(get_tz())
    mode = {"lights": lights or "all", "scene": scene} if scene else resolve(name)
    if scene:
        name = f"scene: {scene}"
    elif lights:
        mode = {**mode, "lights": lights}
    until = off_time(now)
    # Written BEFORE the lights are driven, so an 8pm winddown job firing in the same
    # second sees the hold rather than racing it.
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"mode": name, "lights": mode["lights"], "on_at": now.isoformat(),
                                 "off_at": until.isoformat(), "applied": control_args(mode)}, indent=1))
    code = _control(mode["lights"], *control_args(mode))
    return {"mode": name, "on": mode["lights"], "applied": control_args(mode)[1:],
            "off_at": until.strftime("%-I:%M %p"), "control_exit": code}


def turn_off():
    rec = _record() or {}
    code = _control(rec.get("lights", "all"), "--off")
    STATE.unlink(missing_ok=True)
    return {"mode": rec.get("mode", "night_light"), "on": False, "control_exit": code}


def check(now=None):
    """The off/sweeper jobs: act only on a hold that has reached its off time."""
    rec = _record()
    if not rec:
        return {"skipped": "no night light hold"}
    now = now or datetime.now(get_tz())
    if now < datetime.fromisoformat(rec["off_at"]):
        return {"holding_until": rec["off_at"]}
    return {**turn_off(), "was_due": rec["off_at"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("on", "off", "check", "status"))
    ap.add_argument("--mode", default="night_light",
                    help="With `on`: which mode in config/light_modes.json (default night_light).")
    ap.add_argument("--scene", help="With `on`: hold one of the lights' own scenes by name "
                                    "(e.g. --scene 'War Films') instead of a configured mode.")
    ap.add_argument("--lights", help="With `on`: override which lights (all|console_light|bedroom|living_room|living_room_2).")
    ap.add_argument("--expected-start", metavar="HH:MM",
                    help="With `check`: the job's own scheduled time, so a gateway catch-up "
                         "hours later doesn't switch the lights off mid-afternoon.")
    args = ap.parse_args()

    if args.action == "check" and args.expected_start:
        ok, reason = near_slot(args.expected_start, grace_min=MISFIRE_EARLY_GRACE_MIN,
                               late_grace_min=MISFIRE_LATE_GRACE_MIN)
        if not ok:
            print(json.dumps({"skipped": reason}))
            return

    if args.action == "on":
        out = turn_on(args.mode, scene=args.scene, lights=args.lights)
    elif args.action == "off":
        out = turn_off()
    elif args.action == "check":
        out = check()
    else:
        live = hold()
        out = {"mode": live.get("mode", "night_light"), "on": live.get("lights", "all"),
               "off_at": live["off_at"]} if live else {"mode": None, "on": False}
    print(json.dumps(out))
    if out.get("control_exit"):
        sys.exit(out["control_exit"])


if __name__ == "__main__":
    main()
