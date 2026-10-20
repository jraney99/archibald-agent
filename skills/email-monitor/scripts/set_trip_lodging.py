#!/usr/bin/env python3
"""
Apply a manually-told lodging location to a flight's already-scheduled
leave-for-airport reminder — for trips with no hotel confirmation email to
auto-detect from (e.g. "I'm staying at a friend's place downtown"). Called by Archibald when the user mentions where he's staying for an
upcoming trip in normal conversation (see RULES.md).

Reuses the exact same cancel-and-reschedule path _maybe_upgrade_flight_reminders
(confirm_cal_event.py) uses for a hotel confirmation arriving after the fact —
this is that same mechanism, just triggered by the user telling Archibald directly
instead of an email.
"""
import argparse, sys
from datetime import datetime

sys.path.insert(0, "~/.openclaw/workspace/Skills/email-monitor/scripts")
sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")

GOG   = "/opt/homebrew/bin/gog"
EMAIL = "owner@example.com"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True,
                         help="Whatever the user said — a street address, a neighborhood, a landmark.")
    parser.add_argument("--confirmation", default="",
                         help="Flight confirmation number, if known. If omitted, applies to the "
                              "single soonest upcoming flight still on the tier estimate — errors "
                              "out if there's more than one, rather than guessing which trip.")
    args = parser.parse_args()

    import daemon as _d
    from drive_time import geocode

    state     = _d._load()
    scheduled = state.get("scheduled_flights", {})

    if args.confirmation:
        entry = scheduled.get(args.confirmation)
        if not entry:
            print(f"No scheduled flight found for confirmation {args.confirmation}.", file=sys.stderr)
            sys.exit(1)
        matches = [(args.confirmation, entry)]
    else:
        candidates = []
        for conf, entry in scheduled.items():
            if entry.get("home_source") != "tier":
                continue
            try:
                dep_dt = datetime.fromisoformat(entry["dep_iso"])
            except Exception:
                continue
            if dep_dt > datetime.now(tz=dep_dt.tzinfo):
                candidates.append((dep_dt, conf, entry))
        if not candidates:
            print("No upcoming flight is currently running on a tier estimate — nothing to update.", file=sys.stderr)
            sys.exit(1)
        if len(candidates) > 1:
            print(
                "More than one upcoming flight is on a tier estimate — pass --confirmation to "
                "say which one:\n" + "\n".join(f"  {c} — {e['orig']} on {e['dep_iso'][:10]}" for _, c, e in candidates),
                file=sys.stderr,
            )
            sys.exit(1)
        _, conf, entry = candidates[0]
        matches = [(conf, entry)]

    # Try a real street address first; only fall back to a coarser
    # neighborhood/landmark match if that fails — see geocode()'s docstring.
    coords = geocode(args.address, strict=True) or geocode(args.address, strict=False)
    if not coords:
        print(f"Could not resolve '{args.address}' to a location I trust.", file=sys.stderr)
        sys.exit(2)

    new_home = {"lat": coords[0], "lng": coords[1], "address": args.address, "label": "Friend's place"}

    for conf, entry in matches:
        _d._cancel_stale_flight_reminders(conf, keep_names=set())
        leave_id = entry.get("leave_event_id")
        if leave_id:
            import subprocess
            subprocess.run(
                [GOG, "cal", "delete", "--account", EMAIL, "primary", leave_id, "--force"],
                capture_output=True, text=True, timeout=30,
            )
        _d._schedule_flight_reminders(entry["spec"], override_home=new_home)
        print(f"Updated flight reminder for confirmation {conf} using '{args.address}'.")


if __name__ == "__main__":
    main()
