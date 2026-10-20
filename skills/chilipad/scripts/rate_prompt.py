#!/usr/bin/env python3
"""
The one question the temperature trial needs: how was last night?

NOT LIVE. `rate_prompt.enabled` is false in chilipad_config.json and this script
refuses to send while it is, because a new automated message needs the user's
go-ahead on the exact wording and the exact trigger first (CLAUDE.md's rollout
gate). `--dry-run` prints what it would send and is the thing to run when showing
him.

WHY IT EXISTS. `trial.py` ladders base_temp_f three nights per rung and records
his verdict against the temperature actually slept at — but only if he remembers
to run `trial.py --rate`. Over a 15-night ladder that is 15 remembered commands,
and an unrated night is not neutral: `advance()` steps the rung anyway, so the
ladder walks on with nothing recorded against it.

WHEN IT ASKS. In the morning, `morning_offset_minutes` (60) after the Hatch wake
time, read live the same way every other job in this skill reads it. Deliberately
not the evening: rating a night fourteen hours later is recall, not experience.

It must ask BEFORE `night_schedule.py --advance` runs at 9:20pm, because `rate()`
records against `tonight_base()` — the rung currently set. A morning answer lands
on the rung he actually slept on; an answer typed after 9:20pm would be filed
against the next rung. `--dry-run` and the tests cover that boundary.

    python3 rate_prompt.py --dry-run   # print it, send nothing
    python3 rate_prompt.py             # refuses while enabled is false
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import trial  # noqa: E402
from current_location import is_away_from_home  # noqa: E402
from data_paths import path as data_path  # noqa: E402
from sleepme_client import SleepMeError, load_config  # noqa: E402

PROMPT_STATE = data_path("chilipad_rate_prompt.json")

# verdict -> (button text, callback). trial.RATINGS is the source of truth for
# which verdicts exist; these are the words the user sees.
BUTTONS = (
    ("good", "✅ Good", "confirm:chilipad-rate-good"),
    ("hot", "🔥 Too hot", "confirm:chilipad-rate-hot"),
    ("cold", "🧊 Too cold", "confirm:chilipad-rate-cold"),
)


def compose(base_f: int) -> str:
    """One line, naming the temperature so he is rating a number rather than a
    vague memory. No second line: he cut the upkeep prompt's own second line
    ("dont need the entire second line", 2026-10-06)."""
    return f"🛏 How did you sleep at {base_f}°F?"


def already_asked_today(today: date | None = None) -> bool:
    today = (today or date.today()).isoformat()
    try:
        return json.loads(PROMPT_STATE.read_text()).get("asked_on") == today
    except (FileNotFoundError, ValueError):
        return False


def already_rated_today(today: date | None = None) -> bool:
    """He may have run `trial.py --rate` himself. Asking anyway would file a
    second rating for one night."""
    today = (today or date.today()).isoformat()
    try:
        return any(n.get("date") == today for n in trial.load_state().get("nights", []))
    except Exception:
        return False


def should_ask(cfg: dict, away: bool, today: date | None = None) -> tuple[bool, str]:
    """Every reason not to ask, in one pure function so the gate is testable
    without a subprocess that might send the user a real message."""
    prompt_cfg = cfg.get("rate_prompt", {})
    if not prompt_cfg.get("enabled"):
        return False, "rate_prompt.enabled is false — not approved to send yet"
    if not cfg.get("trial", {}).get("active"):
        return False, "the trial is not running — nothing to rate"
    if away:
        return False, "away from home — he did not sleep in this bed"
    if already_rated_today(today):
        return False, "already rated today"
    if already_asked_today(today):
        return False, "already asked today"
    return True, ""


def send(text: str, base_f: int) -> dict:
    from send_telegram import send_chunk  # noqa: E402

    receipt = send_chunk(text, reply_markup={"inline_keyboard": [[
        {"text": label, "callback_data": cb} for _, label, cb in BUTTONS
    ]]})
    message_id = ((receipt or {}).get("result") or {}).get("message_id", 0)
    PROMPT_STATE.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_STATE.write_text(json.dumps({
        "message_id": message_id,
        "text": text,
        "base_f": base_f,
        "asked_on": date.today().isoformat(),
    }, indent=2) + "\n")
    return {"sent": True, "message_id": message_id, "base_f": base_f}


def pending() -> tuple[int | None, str | None]:
    """(message_id, text) of the prompt a tap belongs to."""
    try:
        state = json.loads(PROMPT_STATE.read_text())
    except (FileNotFoundError, ValueError):
        return None, None
    return state.get("message_id") or None, state.get("text")


def record(verdict: str) -> dict:
    """Handle a tap: file the rating and edit the prompt in place so no second
    message appears and a second tap cannot file a duplicate."""
    cfg = load_config()
    out = trial.rate(cfg, verdict, None)
    base = out["recorded"]["base_f"]
    said = {"good": "Good", "hot": "Too hot", "cold": "Too cold"}[verdict]
    line = f"✅ Logged — {said} at {base}°F"

    message_id, text = pending()
    if message_id:
        try:
            from send_telegram import edit_message_text  # noqa: E402
            if edit_message_text(message_id, f"{text}\n\n{line}"):
                return {"verdict": verdict, "base_f": base, "edited": True}
        except Exception as e:  # noqa: BLE001 — a failed edit must not lose the rating
            return {"verdict": verdict, "base_f": base, "edited": False, "error": str(e)[:120]}
    return {"verdict": verdict, "base_f": base, "edited": False, "line": line}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="print the exact message that would be sent, and exit")
    parser.add_argument("--rate", choices=[v for v, _, _ in BUTTONS],
                        help="handle a button tap")
    args = parser.parse_args()

    cfg = load_config()

    if args.rate:
        result = record(args.rate)
        # NO_REPLY: the prompt was edited in place, so the dispatcher must not
        # also send a second message.
        print("NO_REPLY" if result.get("edited") else result.get("line", json.dumps(result)))
        return 0

    try:
        base_f, why = trial.tonight_base(cfg, trial.load_state())
    except SleepMeError as e:
        print(json.dumps({"sent": False, "error": str(e)}))
        return 1
    text = compose(base_f)

    if args.dry_run:
        prompt_cfg = cfg.get("rate_prompt", {})
        print(text)
        print()
        print(json.dumps({
            "dry_run": True,
            "enabled": bool(prompt_cfg.get("enabled")),
            "fires": f"wake + {prompt_cfg.get('morning_offset_minutes')} min, "
                     f"retimed nightly off the Hatch alarm",
            "buttons": [label for _, label, _ in BUTTONS],
            "rung": why,
        }, indent=2))
        return 0

    ok, why_not = should_ask(cfg, is_away_from_home())
    if not ok:
        print(json.dumps({"sent": False, "skipped": why_not}))
        return 0

    print(json.dumps(send(text, base_f)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
