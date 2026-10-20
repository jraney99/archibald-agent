#!/usr/bin/env python3
"""
List pending reminders — both one-shot (cron jobs named "reminder-*") and
recurring (named "recurring-*").

Usage:
    python3 list_reminders.py
"""
import json
import subprocess
import sys

OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"


def describe_schedule(schedule: dict) -> str:
    kind = schedule.get("kind")
    if kind == "at":
        return schedule.get("at", "")
    if kind == "cron":
        return f"{schedule.get('expr', '')} @ {schedule.get('tz', '')}"
    if kind == "every":
        return f"every {schedule.get('everyMs', 0) // 86400000}d"
    return json.dumps(schedule)


def main():
    result = subprocess.run(
        [OPENCLAW_BIN, "cron", "list", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"ERROR: {result.stderr.strip()}")
        sys.exit(1)

    data = json.loads(result.stdout)
    jobs = data if isinstance(data, list) else data.get("jobs", data)
    reminders = [
        j for j in jobs
        if j.get("name", "").startswith("reminder-") or j.get("name", "").startswith("recurring-")
    ]

    print(json.dumps([
        {
            "id": j["id"],
            "type": "one-shot" if j.get("name", "").startswith("reminder-") else "recurring",
            "text": j.get("description", "").split(": ", 1)[-1],
            "schedule": describe_schedule(j.get("schedule", {})),
            "status": j.get("status"),
        }
        for j in reminders
    ], indent=2))


if __name__ == "__main__":
    main()
