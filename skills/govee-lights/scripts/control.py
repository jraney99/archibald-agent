"""
CLI to control one or more Govee lights by nickname.

Examples:
  python3 control.py --light office --on
  python3 control.py --light office --off
  python3 control.py --light all --brightness 50
  python3 control.py --light bedroom --color "255,180,60"
  python3 control.py --light bedroom --color "#FFB43C"
  python3 control.py --light office --temp 4000
  python3 control.py --light office --scene "Sunrise"
  python3 control.py --list-scenes --light office
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))
from govee_client import GoveeClient, GoveeError, hex_to_int  # noqa: E402
from current_location import is_away_from_home  # noqa: E402
from evening_hold import held  # noqa: E402
from sunrise_ramp import request_stop  # noqa: E402
from misfire_guard import near_slot  # noqa: E402

# Govee's cloud API can report "ok" for a control call that never actually
# reaches the physical device (BLE/WiFi drop) — seen 2026-08-12 when the
# console light silently stuck on a stale color despite a successful cron
# run. There's no reliable way to confirm delivery (device/state is also
# unreliable, see SKILL.md), so instead every control command is applied
# twice with a short gap, unconditionally, to make a single dropped delivery
# far less likely to leave a light in the wrong state.
REPEAT_COUNT = 2
REPEAT_DELAY_SECONDS = 3

# --expected-start: the scheduler replays a "missed" job when the gateway restarts,
# at whatever hour that is (shared/misfire_guard.py). A fire before the slot can
# only be a catch-up, so early grace is tight; how late a run is still wanted
# depends on the job (until the next step that owns the light), so each cron job
# passes its own --late-grace-min.
MISFIRE_EARLY_GRACE_MIN = 20
DEFAULT_LATE_GRACE_MIN = 60

CONFIG_PATH = Path(__file__).parent.parent / "config" / "devices.json"


def load_devices() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text())
    return cfg.get("devices", {})


def resolve_targets(name: str, devices: dict) -> list[tuple[str, dict]]:
    if name == "all":
        return list(devices.items())
    if name not in devices:
        raise GoveeError(
            f"Unknown light nickname {name!r}. Known: {', '.join(devices) or '(none configured yet)'}"
        )
    return [(name, devices[name])]


def parse_color(spec: str) -> tuple[int, int, int]:
    if spec.startswith("#") or (len(spec) == 6 and all(c in "0123456789abcdefABCDEF" for c in spec)):
        val = hex_to_int(spec)
        return (val >> 16) & 0xFF, (val >> 8) & 0xFF, val & 0xFF
    parts = [int(p.strip()) for p in spec.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Color must be 'R,G,B' or hex, got {spec!r}")
    return tuple(parts)  # type: ignore[return-value]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--light", required=True, help="Nickname from config/devices.json, or 'all'")
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--brightness", type=int, help="1-100")
    ap.add_argument("--color", help="'R,G,B' or hex like #FFB43C")
    ap.add_argument("--temp", type=int, help="Color temperature in Kelvin")
    ap.add_argument("--scene", help="Scene name (matched case-insensitively via list_scenes)")
    ap.add_argument("--list-scenes", action="store_true", help="Print available scenes for --light and exit")
    ap.add_argument(
        "--skip-if-away",
        action="store_true",
        help="No-op (exit 0) when is_away_from_home() is true. Put this on scheduled "
             "cron jobs so they don't drive lights in an empty house; omit it on "
             "manual/chat-driven calls so remote control still works while travelling.",
    )
    ap.add_argument(
        "--skip-if-held",
        action="store_true",
        help="No-op (exit 0) while the user's night light hold is live (evening_hold.py). Put "
             "this on the scheduled evening steps so they don't brighten the room back up "
             "after he's asked for the last dim light; omit it on manual/chat-driven calls.",
    )
    ap.add_argument(
        "--expected-start",
        metavar="HH:MM",
        help="The cron job's own scheduled time. No-op (exit 0) when the run is "
             "more than 20 min early or --late-grace-min late — a gateway catch-up "
             "of a 'missed' job, not a real run (see shared/misfire_guard.py). "
             "Put it on every scheduled job; omit it on manual calls.",
    )
    ap.add_argument(
        "--late-grace-min",
        type=int,
        default=DEFAULT_LATE_GRACE_MIN,
        help="With --expected-start: how many minutes late a run still applies "
             "(e.g. until the next scheduled step for this light).",
    )
    args = ap.parse_args()

    if args.expected_start:
        ok, reason = near_slot(args.expected_start, grace_min=MISFIRE_EARLY_GRACE_MIN,
                               late_grace_min=args.late_grace_min)
        if not ok:
            print(json.dumps({"skipped": reason}))
            return

    if args.skip_if_away and is_away_from_home():
        print(json.dumps({"skipped": "away from home"}))
        return

    if args.skip_if_held and held(args.light):
        print(json.dumps({"skipped": "night light hold is on"}))
        return

    devices = load_devices()
    try:
        targets = resolve_targets(args.light, devices)
    except GoveeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    if not targets:
        print(json.dumps({"error": "No devices configured yet — run discover_devices.py first."}))
        sys.exit(1)

    # "Turn the light off" has to mean the sunrise is over too, or the ramp's next step
    # lights it back up ~37s later (The user, 2026-10-01: "super annoying").
    if args.off:
        request_stop("the light was switched off")

    client = GoveeClient()
    results = []

    if args.list_scenes:
        for nickname, dev in targets:
            try:
                scenes = client.list_scenes(dev["sku"], dev["device"])
                results.append({"light": nickname, "scenes": [s.get("name") for s in scenes]})
            except GoveeError as e:
                results.append({"light": nickname, "error": str(e)})
        print(json.dumps(results, indent=2))
        return

    # A flat color/brightness/temp command doesn't reliably override
    # gradient/DreamView (screen-sync) effect modes if either was left on
    # from a past scene — the light can keep rendering that effect even
    # though the flat command reports success (2026-08-17 bedroom-sunrise
    # incident: logged/confirmed amber, light visually showed blue). Clear
    # both before applying, but only for flat commands — --scene requests
    # are allowed to use dynamic effects on purpose.
    clear_effects = not args.scene and not args.list_scenes and (args.color or args.temp or args.brightness is not None)

    for nickname, dev in targets:
        sku, device_id = dev["sku"], dev["device"]
        warning = None
        try:
            if clear_effects:
                # Cloud-only (the LAN API has no gradient command), so it's best effort: on
                # 2026-10-01 the cloud refused this with a bogus "Device is offline" while the
                # strip was answering locally, and failing the whole command for that would
                # mean not setting the colour the user asked for. The flat command below still runs.
                try:
                    client.clear_effect_modes(sku, device_id)
                except GoveeError as e:
                    warning = f"could not clear gradient mode first: {e}"
                time.sleep(REPEAT_DELAY_SECONDS)
            for attempt in range(REPEAT_COUNT):
                replies = []
                if args.on:
                    replies.append(client.power(sku, device_id, True))
                if args.off:
                    replies.append(client.power(sku, device_id, False))
                if args.brightness is not None:
                    replies.append(client.brightness(sku, device_id, args.brightness))
                if args.color:
                    r, g, b = parse_color(args.color)
                    replies.append(client.color_rgb(sku, device_id, r, g, b))
                if args.temp is not None:
                    replies.append(client.color_temp(sku, device_id, args.temp))
                if args.scene:
                    scenes = client.list_scenes(sku, device_id)
                    match = next((s for s in scenes if s.get("name", "").lower() == args.scene.lower()), None)
                    if not match:
                        available = ", ".join(s.get("name", "") for s in scenes)
                        raise GoveeError(f"Scene {args.scene!r} not found for {nickname}. Available: {available}")
                    client.scene(sku, device_id, match["value"])
                # Local control already asked the strip whether it changed, which is all the
                # blind second pass was ever standing in for (REPEAT_COUNT above). Confirmed
                # → stop. Cloud-only calls (scenes) still get the second pass.
                if replies and not args.scene and all(r.get("verified") for r in replies):
                    break
                if attempt < REPEAT_COUNT - 1:
                    time.sleep(REPEAT_DELAY_SECONDS)
            results.append({"light": nickname, "status": "ok", **({"warning": warning} if warning else {})})
        except GoveeError as e:
            results.append({"light": nickname, "status": "error", "error": str(e)})

    print(json.dumps(results, indent=2))
    if any(r.get("status") == "error" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
