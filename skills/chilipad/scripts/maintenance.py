#!/usr/bin/env python3
"""
Keeps the ChiliPad's upkeep on schedule — the three things that actually have to
happen, two on a clock and one on real data from the device.

    python3 maintenance.py --status            # what is due, and when the rest falls
    python3 maintenance.py --check             # the daily job: send a prompt if due
    python3 maintenance.py --check --dry-run   # print what it WOULD send, send nothing
    python3 maintenance.py --done              # the ✅ button, or "I did it" in chat
    python3 maintenance.py --snooze            # the Later button: ask again tomorrow
    python3 maintenance.py --log               # what was done when

WHY THIS EXISTS AT ALL. sleep.me's manual states plainly that **"inability to
follow maintenance instructions voids the customer warranty"**, and the schedule
is not obvious:

  water      Top off when the droplet icon shows. DISTILLED only — the manual is
             explicit that tap-water minerals accumulate and reduce efficiency.
             This one is NOT a calendar guess: the API reports `is_water_low` and
             `water_level`, so the prompt fires off the device's own reading.
  cleaner    MONTHLY: one Chilipad cleaner packet (or ¼ cup / 2 oz hydrogen
             peroxide) into the reservoir, then top off with distilled water.
  deep       SEMI-ANNUAL: disconnect the hose, remove and rinse the reservoir,
             fresh distilled water plus a cleaner packet, drain the dock with the
             drain key, and wash the pad cold on gentle, tumble dry medium.

Every prompt is TRAVEL-GATED (`is_away_from_home()`): these are all physical jobs
at home, and a reminder to go and pour distilled water into a dock at home is
useless while travelling — the same rule the freezer reminder follows.

Each prompt is sent ONCE and then held off for COOLDOWN_DAYS, so a task that
stays undone nags on a known cadence rather than every single day. Tapping ✅
edits the prompt in place and clears its buttons rather than sending a second
message (The user's no-double-message rule), exactly like the nightly putting cue.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

from current_location import is_away_from_home  # noqa: E402
from data_paths import path as data_path  # noqa: E402
from sleepme_client import SleepMeClient, SleepMeError, resolve_device  # noqa: E402

# The manual's intervals. Changing one here changes when it is asked for; nothing
# else needs touching.
CLEANER_DAYS = 30          # monthly: cleaner packet + top off
DEEP_DAYS = 182            # semi-annual: full drain, rinse, fresh water, wash the pad
# How long to wait before asking again about a task he has not marked done. Long
# enough not to nag, short enough that a monthly job does not slip a month.
COOLDOWN_DAYS = 4
# A low reservoir is the one that actually stops the bed working, so it is asked
# about sooner.
WATER_COOLDOWN_DAYS = 2

TASKS = ("water", "cleaner", "deep")

# The action first, then only what is needed to do the job (The user, 2026-10-06:
# "Add water to ChiliPad. Thats all we need... If there are instructions keep the
# instructions so that I can complete the task"). Peroxide rather than the cleaner
# packets — his choice, so the packet alternative is gone. "distilled" stays because
# it IS an instruction: the manual is explicit that tap-water minerals accumulate and
# cut the cooling.
PROMPTS = {
    "water": "🛏 Add distilled water to ChiliPad.",
    "cleaner": (
        "🛏 Clean the ChiliPad. ¼ cup (2 oz) hydrogen peroxide into the reservoir, "
        "cap off ~20 min until it stops bubbling, then top off with distilled water."
    ),
    "deep": (
        "🛏 Deep clean the ChiliPad. Disconnect the hose, rinse the reservoir, refill "
        "with distilled water + peroxide, drain the dock with the drain key, then wash "
        "the pad cold on gentle and tumble dry medium."
    ),
}

DONE_LINE = {
    "water": "✅ ChiliPad topped off",
    "cleaner": "✅ ChiliPad monthly upkeep done",
    "deep": "✅ ChiliPad deep clean done",
}


def _record() -> Path:
    return data_path("chilipad_maintenance.json")


def _prompt_state() -> Path:
    return data_path("chilipad_maintenance_prompt.json")


def load_record() -> dict:
    try:
        return json.loads(_record().read_text())
    except (FileNotFoundError, ValueError):
        return {"last_done": {}, "last_asked": {}, "log": []}


def save_record(rec: dict) -> None:
    target = _record()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rec, indent=2) + "\n")


def _as_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def due_in_days(task: str, rec: dict, today: date | None = None) -> int | None:
    """Days until `task` falls due; negative means overdue. None for water, which
    is driven by the device rather than a date."""
    if task == "water":
        return None
    today = today or date.today()
    interval = CLEANER_DAYS if task == "cleaner" else DEEP_DAYS
    last = _as_date((rec.get("last_done") or {}).get(task))
    if last is None:
        return 0            # never recorded: due now, and the first --done starts the clock
    return (last + timedelta(days=interval) - today).days


def asked_recently(task: str, rec: dict, today: date | None = None) -> bool:
    today = today or date.today()
    asked = _as_date((rec.get("last_asked") or {}).get(task))
    if asked is None:
        return False
    cooldown = WATER_COOLDOWN_DAYS if task == "water" else COOLDOWN_DAYS
    return (today - asked).days < cooldown


def water_is_low(device: str | None = None) -> tuple[bool, dict]:
    """Ask the device, not a calendar. Returns (low, detail)."""
    device_id, _ = resolve_device(device)
    status = (SleepMeClient().get_device(device_id) or {}).get("status") or {}
    return bool(status.get("is_water_low")), {
        "water_level": status.get("water_level"),
        "is_water_low": status.get("is_water_low"),
        "connected": status.get("is_connected"),
    }


def next_task(rec: dict, water_low: bool, today: date | None = None) -> str | None:
    """The one task worth asking about right now, or None.

    One prompt at a time, most urgent first: an empty reservoir stops the bed
    working tonight, the deep clean is the one that voids the warranty, the
    monthly is the routine one. Three messages at once is the kind of pile-up he
    has asked not to get.
    """
    today = today or date.today()
    if water_low and not asked_recently("water", rec, today):
        return "water"
    for task in ("deep", "cleaner"):
        overdue = due_in_days(task, rec, today)
        if overdue is not None and overdue <= 0 and not asked_recently(task, rec, today):
            return task
    return None


def send_prompt(task: str, dry_run: bool = False) -> dict:
    """Send the prompt with its two buttons and remember the message, so the tap
    can edit it in place. The state file is the SENDER's, which is what the
    button contract requires of the handler that reads it."""
    text = PROMPTS[task]
    if dry_run:
        return {"would_send": task, "text": text}

    # Imported late: --status and --log need no Telegram, and send_telegram
    # resolves the bot token at import time.
    from send_telegram import send_chunk  # noqa: E402

    receipt = send_chunk(text, reply_markup={"inline_keyboard": [[
        {"text": "✅ Done", "callback_data": "confirm:chilipad-maint"},
        {"text": "Later", "callback_data": "skip:chilipad-maint"},
    ]]})
    message_id = ((receipt or {}).get("result") or {}).get("message_id", 0)
    _prompt_state().parent.mkdir(parents=True, exist_ok=True)
    _prompt_state().write_text(json.dumps({
        "task": task,
        "message_id": message_id,
        "text": text,
        "asked_on": date.today().isoformat(),
    }, indent=2) + "\n")
    return {"sent": task, "message_id": message_id}


def pending_task() -> tuple[str | None, int | None, str | None]:
    """(task, message_id, text) from the prompt this tap belongs to."""
    try:
        state = json.loads(_prompt_state().read_text())
    except (FileNotFoundError, ValueError):
        return None, None, None
    return state.get("task"), state.get("message_id") or None, state.get("text")


def mark(task: str, done: bool) -> dict:
    """Record a completion (or a snooze) and clear the prompt's buttons."""
    rec = load_record()
    today = date.today().isoformat()
    if done:
        rec.setdefault("last_done", {})[task] = today
        rec.setdefault("log", []).append({"task": task, "done_on": today})
    rec.setdefault("last_asked", {})[task] = today if not done else today
    save_record(rec)

    _, message_id, text = pending_task()
    line = DONE_LINE[task] if done else "⏳ Left for later — I'll ask again in a couple of days"
    if message_id:
        try:
            from send_telegram import edit_message_text  # noqa: E402
            # drop_buttons default clears the keyboard, so a second tap cannot
            # re-log the same job.
            ok = edit_message_text(message_id, f"{text}\n\n{line}")
            return {"task": task, "done": done, "edited": bool(ok)}
        except Exception as e:  # noqa: BLE001 — a failed edit must never swallow the receipt
            return {"task": task, "done": done, "edited": False, "error": str(e)[:120]}
    return {"task": task, "done": done, "edited": False}


def status_report(check_water: bool = True) -> dict:
    rec = load_record()
    out = {"due": {}, "last_done": rec.get("last_done", {})}
    for task in ("cleaner", "deep"):
        days = due_in_days(task, rec)
        out["due"][task] = "now" if days <= 0 else f"in {days} days"
    if check_water:
        try:
            low, detail = water_is_low()
            out["water"] = {"low": low, **detail}
        except SleepMeError as e:
            out["water"] = {"error": str(e)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="ChiliPad upkeep: water, monthly cleaner, deep clean.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="What is due and when the rest falls.")
    group.add_argument("--check", action="store_true", help="The daily job: prompt if something is due.")
    group.add_argument("--done", action="store_true", help="The ✅ button, or 'I did it' in chat.")
    group.add_argument("--snooze", action="store_true", help="The Later button.")
    group.add_argument("--log", action="store_true", help="What was done when.")
    group.add_argument("--set-last", choices=("cleaner", "deep"), metavar="TASK",
                       help="Seed when a task was last done, so the first prompt is not a "
                            "false 'overdue'. Needs --date.")
    ap.add_argument("--task", choices=TASKS, help="With --done/--snooze: which task (default: the one prompted).")
    ap.add_argument("--date", help="With --set-last: YYYY-MM-DD, or 'today'.")
    ap.add_argument("--dry-run", action="store_true", help="With --check: print, send nothing.")
    args = ap.parse_args()

    try:
        if args.status:
            print(json.dumps(status_report(), indent=2))
            return

        if args.log:
            print(json.dumps(load_record().get("log", []), indent=2))
            return

        if args.set_last:
            when = date.today() if (args.date or "today") == "today" else _as_date(args.date)
            if when is None:
                print(json.dumps({"error": f"--date {args.date!r} is not a YYYY-MM-DD date"}))
                sys.exit(2)
            rec = load_record()
            rec.setdefault("last_done", {})[args.set_last] = when.isoformat()
            rec.setdefault("log", []).append({"task": args.set_last, "done_on": when.isoformat(),
                                              "source": "told to me, not observed"})
            save_record(rec)
            days = due_in_days(args.set_last, rec)
            print(json.dumps({"task": args.set_last, "last_done": when.isoformat(),
                              "next_due": "now" if days <= 0 else f"in {days} days"}))
            return

        if args.done or args.snooze:
            task = args.task or pending_task()[0]
            if task not in TASKS:
                print("No ChiliPad upkeep prompt is waiting — say which task with --task.")
                return
            result = mark(task, done=args.done)
            # NO_REPLY: the prompt was edited in place, so the dispatcher must not
            # also send a second message.
            print("NO_REPLY" if result.get("edited") else
                  (DONE_LINE[task] if args.done else "⏳ Left for later."))
            return

        # --check
        if is_away_from_home():
            print(json.dumps({"skipped": "away from home"}))
            return
        rec = load_record()
        low = False
        try:
            low, _ = water_is_low()
        except SleepMeError as e:
            # A dock that cannot be read is not a reason to skip the dated tasks.
            print(json.dumps({"water_unknown": str(e)[:120]}), file=sys.stderr)
        task = next_task(rec, low)
        if task is None:
            print(json.dumps({"nothing_due": True}))
            return
        result = send_prompt(task, dry_run=args.dry_run)
        if not args.dry_run:
            rec.setdefault("last_asked", {})[task] = date.today().isoformat()
            save_record(rec)
        print(json.dumps(result, indent=2 if args.dry_run else None))
    except SleepMeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
