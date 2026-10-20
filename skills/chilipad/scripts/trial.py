#!/usr/bin/env python3
"""
Find the user's temperature by testing it, not guessing it.

The user, 2026-10-06, asked which temperature the science supports and the honest
answer is that no study names one — the one trial with objective sleep measures
individualised the setting and ~68% of participants adjusted it themselves
(Clocks & Sleep 2026). Sleepme say to expect 2-3 weeks of trial. His own
situation is mixed: he wants a blanket over him but tends to run warm,
which points at a cool surface under a warm cover rather than at
either end of the range. So the base temperature steps down a ladder, a few
nights per rung, and he rates the nights.

    python3 trial.py --status
    python3 trial.py --rate good|hot|cold [--note "woke at 3"]
    python3 trial.py --report
    python3 trial.py --advance        # called nightly by night_schedule.py
    python3 trial.py --stop           # settle on the current rung

Ratings are HIS words, recorded verbatim with the temperature they belong to.
Nothing here interprets them or scores his sleep — interpretation is not this
skill's job (feedback-no-medical-interpretation).
--report lays out what he said at each temperature and stops there.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

from data_paths import path as data_path  # noqa: E402
from sleepme_client import at_least_floor, SleepMeError, clamp_f, load_config  # noqa: E402

RATINGS = ("good", "hot", "cold")


def _state_file() -> Path:
    return data_path("chilipad_nights.json")


def load_state() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except (FileNotFoundError, ValueError):
        return {"step": 0, "nights_on_step": 0, "settled": False, "nights": []}


def save_state(state: dict) -> None:
    target = _state_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2) + "\n")


def ladder(cfg: dict) -> list:
    """The rungs, with any that sit below the user's floor lifted to it (see floor_f)."""
    rungs = (cfg.get("trial") or {}).get("ladder_f") or []
    lifted = [at_least_floor(r, cfg) for r in rungs]
    seen, out = set(), []
    for rung in lifted:  # lifting can create duplicates; a rung tested twice is wasted nights
        if rung not in seen:
            seen.add(rung)
            out.append(rung)
    return out


def tonight_base(cfg: dict, state: dict | None = None) -> tuple[int, str]:
    """(the base temperature to use tonight, why).

    The trial's rung while it runs, the settled number once it is over. Always
    falls back to base_temp_f, so a missing or finished trial can never leave
    the night without a temperature.
    """
    base = at_least_floor(cfg.get("base_temp_f", 76), cfg)
    trial = cfg.get("trial") or {}
    if not trial.get("active"):
        return base, "base_temp_f (no trial running)"
    state = state if state is not None else load_state()
    if state.get("settled"):
        return base, "base_temp_f (trial settled)"
    rungs = ladder(cfg)
    step = int(state.get("step", 0))
    if not rungs or step >= len(rungs):
        return base, "base_temp_f (ladder finished)"
    per = int(trial.get("nights_per_step", 3))
    # Which night is being SLEPT. advance() runs at 21:20, before the night, so once tonight has
    # been counted the night in progress is nights_on_step itself; before that it is the next one.
    # (It always read nights_on_step + 1, so after the evening retime it said "night 3 of 3" on
    # the night that was only the second — 2026-10-07.)
    counted_tonight = state.get("last_counted") == date.today().isoformat()
    night = max(1, int(state.get("nights_on_step", 0)) + (0 if counted_tonight else 1))
    return rungs[step], f"trial rung {step + 1} of {len(rungs)}, night {night} of {per}"


def advance(cfg: dict, today: str | None = None) -> dict:
    """Count tonight against the current rung and step on when it is done.

    IDEMPOTENT PER NIGHT. night_schedule.py calls this, and night_schedule can
    legitimately run more than once in an evening — the 9:20pm cron, plus any time
    the Hatch alarm changes (it is in alarm.py's FOLLOWERS). Counting each run
    would step the ladder several times a night and attribute the user's rating to a
    temperature he never slept at.
    """
    state = load_state()
    trial = cfg.get("trial") or {}
    if not trial.get("active") or state.get("settled"):
        return {"trial": "not running"}
    today = today or date.today().isoformat()
    if state.get("last_counted") == today:
        rungs_now = ladder(cfg)
        return {"trial": f"rung {min(int(state.get('step', 0)), max(0, len(rungs_now) - 1)) + 1} "
                         f"of {len(rungs_now)}",
                "nights_on_rung": int(state.get("nights_on_step", 0)),
                "already_counted": today}
    rungs = ladder(cfg)
    step = int(state.get("step", 0))
    if step >= len(rungs):
        state["settled"] = True
        save_state(state)
        return {"trial": "ladder finished — settle on a number with --stop or set base_temp_f"}

    per = max(1, int(trial.get("nights_per_step", 3)))
    state["nights_on_step"] = int(state.get("nights_on_step", 0)) + 1
    state["last_counted"] = today
    moved = None
    if state["nights_on_step"] >= per:
        state["nights_on_step"] = 0
        state["step"] = step + 1
        if state["step"] >= len(rungs):
            state["settled"] = True
            moved = "ladder finished"
        else:
            moved = f"moving to {rungs[state['step']]}°F"
    save_state(state)
    out = {"trial": f"rung {min(state['step'], len(rungs) - 1) + 1} of {len(rungs)}",
           "nights_on_rung": state["nights_on_step"]}
    if moved:
        out["moved"] = moved
    return out


def restart(cfg: dict, why: str | None = None) -> dict:
    """Start the ladder again from its first rung, KEEPING every night already rated.

    Needed whenever the ladder itself is re-based: on 2026-10-07 the rungs went from
    [72, 70, 68, 74, 76] to [80, 78, 82, 76, 84] and the floor moved to 80°F, so the two nights
    already counted had been slept at 72°F and 82°F in a different shape of night. Counting them
    towards the new rung would attribute the user's next rating to a temperature he never slept at."""
    state = load_state()
    state.update(step=0, nights_on_step=0, settled=False)
    state.pop("last_counted", None)
    state.setdefault("restarts", []).append({"at": datetime.now().isoformat(timespec="seconds"),
                                             "ladder": ladder(cfg), "why": why or "ladder re-based"})
    save_state(state)
    return {"restarted": True, "rung_1_f": ladder(cfg)[0],
            "nights_kept": len(state.get("nights") or []), "why": why or "ladder re-based"}


def rate(cfg: dict, verdict: str, note: str | None, for_date: str | None = None) -> dict:
    """Record how a night felt, against the temperature it was actually run at."""
    if verdict not in RATINGS:
        raise SleepMeError(f"rating must be one of {', '.join(RATINGS)}")
    state = load_state()
    base, why = tonight_base(cfg, state)
    entry = {
        "date": for_date or date.today().isoformat(),
        "base_f": base,
        "rating": verdict,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    if note:
        entry["note"] = note
    state.setdefault("nights", []).append(entry)
    save_state(state)
    return {"recorded": entry, "rung": why}


def report(cfg: dict) -> dict:
    """What he said at each temperature. No scoring, no conclusion."""
    state = load_state()
    by_temp: dict = {}
    for night in state.get("nights", []):
        row = by_temp.setdefault(night.get("base_f"), {"good": 0, "hot": 0, "cold": 0, "notes": []})
        if night.get("rating") in row:
            row[night["rating"]] += 1
        if night.get("note"):
            row["notes"].append(f"{night.get('date')}: {night['note']}")
    base, why = tonight_base(cfg, state)
    return {
        "tonight_f": base,
        "why": why,
        "nights_rated": len(state.get("nights", [])),
        "by_temperature": dict(sorted(by_temp.items(), reverse=True)),
        "settled": bool(state.get("settled")),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the ChiliPad temperature trial.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Tonight's temperature and where the trial is.")
    group.add_argument("--rate", choices=RATINGS, help="How last night felt.")
    group.add_argument("--report", action="store_true", help="What he said at each temperature.")
    group.add_argument("--advance", action="store_true", help="Count a night (the nightly job does this).")
    group.add_argument("--stop", action="store_true", help="Settle on the current rung and end the trial.")
    group.add_argument("--restart", action="store_true",
                       help="Begin the ladder again at rung 1, keeping the nights already rated "
                            "(use after changing ladder_f or base_temp_f — their ratings belong to "
                            "the old rungs).")
    ap.add_argument("--note", help="With --rate: anything worth remembering about the night.")
    ap.add_argument("--date", help="With --rate: the night's date (default today).")
    args = ap.parse_args()

    try:
        cfg = load_config()
        if args.status:
            base, why = tonight_base(cfg)
            print(json.dumps({"tonight_f": base, "why": why}))
        elif args.rate:
            print(json.dumps(rate(cfg, args.rate, args.note, args.date)))
        elif args.report:
            print(json.dumps(report(cfg), indent=2))
        elif args.advance:
            print(json.dumps(advance(cfg)))
        elif args.restart:
            print(json.dumps(restart(cfg, args.note)))
        elif args.stop:
            state = load_state()
            base, _ = tonight_base(cfg, state)
            state["settled"] = True
            save_state(state)
            print(json.dumps({"settled_at_f": base,
                              "next": f"set base_temp_f to {base} in chilipad_config.json to make it permanent"}))
    except SleepMeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
