#!/usr/bin/env python3
"""
Put the house on eco while the user is travelling, and take it off when he is back.

    python3 away.py --check      # the scheduled job: decide and act
    python3 away.py --check --dry-run
    python3 away.py --status     # what it thinks, without touching anything

Driven by `is_away_from_home()` — the same signal the freezer reminder, the
bedroom lights and the ChiliPad use, so there is one answer to "is he home"
across the whole deployment.

**IT ONLY EVER UNDOES ITS OWN WORK.** When it turns eco on it records that in
`state/nest_setback.json`, and on return it clears eco only if that record says
this script set it. Eco that the user switched on himself in the Nest app is left
exactly alone — the same restraint `sync_bedroom_sunrise` shows in never
re-enabling a cron job someone switched off by hand. Without that flag, coming
home would silently cancel a setting he had chosen.

Eco rather than a plain setpoint, deliberately: eco is Nest's own away mode, it
keeps the house inside safe bounds rather than a number this script invents, and
it is a single reversible switch. The cost is that **eco rejects setpoint
commands**, so while it is on, `control.py --temp` will clear it first — which is
correct, because a manual temperature change means he is home.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

from current_location import is_away_from_home  # noqa: E402
from data_paths import path as data_path  # noqa: E402
from nest_client import NestClient, NestError, resolve_device  # noqa: E402


def load_state() -> dict:
    try:
        return json.loads(data_path("nest_setback.json").read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state: dict) -> None:
    target = data_path("nest_setback.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2) + "\n")


def decide(away: bool, eco_on: bool, we_set_it: bool) -> tuple[str, str]:
    """(action, why) — pure, so the whole truth table is testable.

    Actions: "set_eco", "clear_eco", "none".
    """
    if away and not eco_on:
        return "set_eco", "away from home and the house is not on eco"
    if away and eco_on:
        return "none", "away from home, already on eco"
    if not away and eco_on and we_set_it:
        return "clear_eco", "home again, and this script set the eco it is on"
    if not away and eco_on and not we_set_it:
        return "none", "home, but the eco on the thermostat was not set by this script"
    return "none", "home, eco already off"


def main() -> None:
    ap = argparse.ArgumentParser(description="Eco the house while the user is away; restore it on return.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Decide and act.")
    group.add_argument("--status", action="store_true", help="Report the decision without acting.")
    ap.add_argument("--device", help="Nickname (default: the one in nest_config.json).")
    ap.add_argument("--dry-run", action="store_true", help="With --check: decide, act on nothing.")
    args = ap.parse_args()

    try:
        name, nickname = resolve_device(args.device)
        client = NestClient()
        state = client.read(client.get_device(name))
    except NestError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    away = is_away_from_home()
    eco_on = state.get("eco") == "MANUAL_ECO"
    record = load_state()
    we_set_it = bool(record.get("eco_set_by_script"))
    action, why = decide(away, eco_on, we_set_it)

    out = {"device": nickname, "away": away, "eco_on": eco_on,
           "eco_set_by_script": we_set_it, "action": action, "why": why}

    if args.status or args.dry_run or action == "none":
        print(json.dumps(out, indent=2 if args.status else None))
        return

    try:
        client.set_eco(name, action == "set_eco")
    except NestError as e:
        out["error"] = str(e)
        print(json.dumps(out))
        sys.exit(1)

    record["eco_set_by_script"] = (action == "set_eco")
    record["changed_at"] = datetime.now().isoformat(timespec="seconds")
    record["last_action"] = action
    save_state(record)
    out["done"] = True
    print(json.dumps(out))


if __name__ == "__main__":
    main()
