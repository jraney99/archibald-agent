#!/usr/bin/env python3
"""
Regression test for the meal_reminder_state.json path collision
(2026-09-15): the freezer-meals reminder (this skill) and the nutrition
skill's meal-choice tracking used to share the exact same state
file path with incompatible schemas — flat {"source_tg_msg_id": N} here vs.
a dict keyed by "{date}-{slot}" there. Whichever fired last each night
silently destroyed the other's data, which is why nutrition's dinner
meal-choice buttons stopped getting edited
away after a tap even though the meal itself logged fine on the backend.

Fix: this skill's reminder now owns a dedicated
freezer_meals_reminder_state.json, leaving meal_reminder_state.json
exclusively to nutrition.

No network, no real state files touched.

    python3 test_meals_state_path.py
"""
import json
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parents[1] / "nutrition" / "scripts"))
sys.path.insert(0, str(SCRIPT_DIR.parents[1] / "shared"))

import send_meals_reminder as smr  # noqa: E402
import log_meals as lm  # noqa: E402
import confirm_meal as cm  # noqa: E402
import send_meal_reminder as nutrition_sender  # noqa: E402
import log_supplement as ls  # noqa: E402
from pending_proposal import load_message_id  # noqa: E402

_fails = []


def check(name, got, want):
    if got != want:
        _fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok  {name}")


# --- the collision itself must not exist ---------------------------------
check("send_meals_reminder.STATE_PATH != nutrition confirm_meal.STATE_FILE",
      smr.STATE_PATH == cm.STATE_FILE, False)
check("log_meals.STATE_PATH != nutrition confirm_meal.STATE_FILE",
      lm.STATE_PATH == cm.STATE_FILE, False)
check("send_meals_reminder and log_meals agree on the same (freezer) path",
      smr.STATE_PATH, lm.STATE_PATH)
check("nutrition's three scripts still agree on one shared path",
      len({cm.STATE_FILE, nutrition_sender.STATE_FILE, ls.STATE_FILE}), 1)
check("freezer path is the dedicated file, not the old shared name",
      smr.STATE_PATH.name, "freezer_meals_reminder_state.json")
check("nutrition path is the original shared name (now exclusive to it)",
      cm.STATE_FILE.name, "meal_reminder_state.json")

# --- functional: a freezer-reminder write must not perturb nutrition -----
freezer_tmp = Path(tempfile.mktemp())
nutrition_tmp = Path(tempfile.mktemp())
orig_smr_path, orig_lm_path, orig_cm_path = smr.STATE_PATH, lm.STATE_PATH, cm.STATE_FILE
try:
    smr.STATE_PATH = freezer_tmp
    lm.STATE_PATH = freezer_tmp
    cm.STATE_FILE = nutrition_tmp

    # Seed nutrition's file as it would look mid-day: a real per-slot record.
    nutrition_tmp.write_text(json.dumps({
        "2026-09-15-5": {"message_id": 999, "supp_row": None, "meal_rows": None},
    }))

    # Simulate the freezer reminder firing and writing its own state.
    freezer_tmp.write_text(json.dumps({"source_tg_msg_id": 12232}))

    check("freezer write left nutrition's per-slot record untouched",
          json.loads(nutrition_tmp.read_text()),
          {"2026-09-15-5": {"message_id": 999, "supp_row": None, "meal_rows": None}})
    check("log_meals reads back the freezer's own message id",
          load_message_id(freezer_tmp), 12232)
finally:
    smr.STATE_PATH, lm.STATE_PATH, cm.STATE_FILE = orig_smr_path, orig_lm_path, orig_cm_path
    for p in (freezer_tmp, nutrition_tmp):
        if p.exists():
            p.unlink()

if _fails:
    print("\nFAILURES:")
    for f in _fails:
        print("  " + f)
    sys.exit(1)
print("\nall pass")
