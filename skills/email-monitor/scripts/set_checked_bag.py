#!/usr/bin/env python3
"""
Mark an already-scheduled flight as having a checked bag, so the leave-time
calculator floors terminal arrival at departure-60 (see compute_leave_time.py's
checked-bag adjustment). Called by Archibald either when the user mentions
checking a bag in normal conversation, or when he replies "yes" to the T-24h
check-in reminder's bag question (prompt_checkin.py / pending_checked_bag.json
— see RULES.md) — bag status isn't knowable from a confirmation email, so
this is the only way the calculator ever learns about it.

Reuses the same cancel-and-reschedule path set_trip_lodging.py uses for a
manually-told lodging update.
"""
import os
import argparse, json, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/Skills/email-monitor/scripts"))

GOG     = "/opt/homebrew/bin/gog"
EMAIL   = "owner@example.com"
PENDING = Path(os.path.expanduser("~/.openclaw/pending_checked_bag.json")).expanduser()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmation", default="",
                         help="Flight confirmation number, if known. If omitted, applies to the "
                              "single soonest upcoming flight not already marked checked_bag — "
                              "errors out if there's more than one, rather than guessing which trip.")
    args = parser.parse_args()

    import daemon as _d

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
            if entry.get("spec", {}).get("checked_bag"):
                continue
            try:
                dep_dt = datetime.fromisoformat(entry["dep_iso"])
            except Exception:
                continue
            if dep_dt > datetime.now(tz=dep_dt.tzinfo):
                candidates.append((dep_dt, conf, entry))
        if not candidates:
            print("No upcoming flight is pending a checked-bag update.", file=sys.stderr)
            sys.exit(1)
        if len(candidates) > 1:
            print(
                "More than one upcoming flight is pending — pass --confirmation to say which one:\n"
                + "\n".join(f"  {c} — {e['orig']} on {e['dep_iso'][:10]}" for _, c, e in candidates),
                file=sys.stderr,
            )
            sys.exit(1)
        _, conf, entry = candidates[0]
        matches = [(conf, entry)]

    for conf, entry in matches:
        # No override_home passed: _schedule_flight_reminders re-derives lodging
        # from cfg/spec, same as a fresh booking. If this trip's home was set
        # manually via set_trip_lodging.py (a friend's place, not a home base or
        # hotel-email lodging), that address isn't persisted anywhere and won't
        # survive this reschedule — known gap, same one set_trip_lodging.py has
        # if called twice.
        _d._cancel_stale_flight_reminders(conf, keep_names=set())
        leave_id = entry.get("leave_event_id")
        if leave_id:
            import subprocess
            subprocess.run(
                [GOG, "cal", "delete", "--account", EMAIL, "primary", leave_id, "--force"],
                capture_output=True, text=True, timeout=30,
            )
        entry["spec"]["checked_bag"] = True
        _d._schedule_flight_reminders(entry["spec"])
        print(f"Marked checked bag for confirmation {conf} — reminders rescheduled.")

    # Clear the pending yes/no question (prompt_checkin.py's --ask-bag) if this
    # answered it — avoids a stale pending file routing a later unrelated reply
    # here by mistake.
    try:
        if PENDING.exists() and json.loads(PENDING.read_text()).get("confirmation") in {c for c, _ in matches}:
            PENDING.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    main()
