#!/usr/bin/env python3
"""
Calendar duplicate cleaner.
Scans the next 60 days for Google auto-imported events that duplicate
a properly formatted '<> the user @' event at the same time, and deletes them.

Google auto-imports (via 'Events from Gmail') always have '_'-prefixed event IDs
and never use our '<> the user @' title convention.

Safe-delete rules — only removes an event if ALL three are true:
  1. Event ID starts with '_' (Google auto-import signature)
  2. Title does NOT contain '<> the user @' (not our format)
  3. A '<> the user @' event exists at the same start time (±10 min)

Run: python3 cleanup_cal_duplicates.py
Output: JSON {"deleted": [...titles...], "scanned": N}
"""
import json, subprocess, sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from fetch_calendar import gog_env  # noqa: E402

GOG   = "/opt/homebrew/bin/gog"
EMAIL = "owner@example.com"


class CalendarUnavailable(RuntimeError):
    """gog could not be read. Never silently an empty calendar."""


def fetch_events(from_str: str, to_str: str) -> list:
    """Every event in the window, or raise.

    2026-10-09: this returned [] on any failure, so after the 2026-10-02 gog
    keyring migration (GOG_KEYRING_PASSWORD, supplied by gog_env) it printed
    {"deleted": [], "scanned": 0} and looked like a clean run with nothing to do.
    An empty calendar and an unreadable one must never be the same answer — the
    whole point of this script is deciding what to DELETE from that list.
    """
    r = subprocess.run(
        [GOG, "cal", "ls", "--account", EMAIL,
         "--from", from_str, "--to", to_str, "-j", "--max", "200"],
        capture_output=True, text=True, timeout=30, env=gog_env(),
    )
    blob = (r.stdout or "").strip()
    if r.returncode != 0 or not blob.startswith("{"):
        detail = ((r.stderr or "") + blob).strip()[:200] or "no output"
        raise CalendarUnavailable(f"gog cal ls failed: {detail}")
    try:
        return json.loads(blob).get("events", [])
    except ValueError as e:
        raise CalendarUnavailable(f"gog cal ls returned unparseable JSON: {e}") from e


def event_start_dt(ev: dict):
    s = ev.get("start", {})
    raw = s.get("dateTime") or s.get("date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def main():
    today    = date.today()
    from_str = today.isoformat()
    to_str   = (today + timedelta(days=60)).isoformat()

    events  = fetch_events(from_str, to_str)
    scanned = len(events)

    # Split into our events (have '<> the user @') and candidates for deletion
    ours       = [e for e in events if "<> the user @" in e.get("summary", "")]
    candidates = [e for e in events if e.get("id", "").startswith("_")
                                    and "<> the user @" not in e.get("summary", "")]

    # Index our events by approximate start time (rounded to 10-min buckets)
    def bucket(dt):
        if dt is None:
            return None
        return dt.replace(minute=(dt.minute // 10) * 10, second=0, microsecond=0)

    our_buckets = set()
    for e in ours:
        b = bucket(event_start_dt(e))
        if b:
            our_buckets.add(b)

    deleted = []
    for ev in candidates:
        ev_dt = event_start_dt(ev)
        if ev_dt is None:
            continue
        # Check if a '<> the user @' event exists in the same 10-min bucket
        if bucket(ev_dt) not in our_buckets:
            continue
        ev_id      = ev["id"]
        ev_summary = ev.get("summary", "")
        d = subprocess.run(
            [GOG, "cal", "delete", "--account", EMAIL, "primary", ev_id, "--force"],
            capture_output=True, text=True, timeout=30, env=gog_env(),
        )
        if d.returncode == 0:
            deleted.append(ev_summary)
        else:
            print(f"Warning: could not delete '{ev_summary}': {d.stderr.strip()}", file=sys.stderr)

    print(json.dumps({"deleted": deleted, "scanned": scanned}))


if __name__ == "__main__":
    main()
