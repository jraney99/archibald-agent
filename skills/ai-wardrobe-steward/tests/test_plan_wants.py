#!/usr/bin/env python3
"""The wardrobe plan's gaps meeting the shopping list. Sandboxed; --add is never run for real.

2026-10-13: `plan_progress.py` knew the holes and the watchlist was hand-kept, so the two
drifted — P1 gaps sat unwatched while the scan hunted for things already filled.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

SANDBOX = tempfile.TemporaryDirectory()
os.environ["OPENCLAW_HOME"] = SANDBOX.name
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
import plan_wants as pw  # noqa: E402

MD = """## 2. The finished wardrobe — target set (permanent layer)
| Category | Target | Have | Gap / action | Priority |
|---|---|---|---|---|
| Casual button-ups | 8 | 2 | +6 | **P1** |
| Knit polos (worn as shirts, open collar) | 3 | 0 | +3 | P2 |
| Field jacket (waxed) | 1 | 0 | +1 | P2 |
| Suits | 3 | 3 | — | done |
### Rotation caps
## 2a. Wear capacity
| Category | Wear-days available/yr | ÷ min wears | **Ceiling** | Have | Read |
|---|---|---|---|---|---|
| Jeans | ~70 | 15 | ~4–5 | 3 | room |
## 3. Build sequence
"""


def want(wid, category, notes, status="active"):
    return {"id": wid, "category": category, "status": status, "criteria": {"notes": notes}}


class MissingTests(unittest.TestCase):
    def rows(self, wants):
        return pw.missing(MD, closet=[], wants=wants)

    def test_an_open_gap_with_no_want_is_reported(self):
        rows = [r for r in self.rows([]) if not r["covered_by"]]
        self.assertEqual([r["row"] for r in rows],
                         ["Casual button-ups", "Knit polos (worn as shirts, open collar)", "Field jacket (waxed)"])
        self.assertEqual(rows[0]["priority"], "P1", "P1 first")
        self.assertEqual(rows[0]["category"], "shirt")

    def test_a_matching_want_covers_the_row(self):
        rows = self.rows([want("w1", "shirt", "white OCBD and a chambray")])
        button_ups = next(r for r in rows if r["row"] == "Casual button-ups")
        self.assertEqual(button_ups["covered_by"], ["w1"])

    def test_the_same_category_is_not_enough(self):
        """A plain golf-polo want must not make "knit polos" look covered."""
        rows = self.rows([want("w1", "polo", "Golf polos - ongoing, he likes variety")])
        knit = next(r for r in rows if r["row"].startswith("Knit polos"))
        self.assertEqual(knit["covered_by"], [])
        rows = self.rows([want("w2", "polo", "KNIT polo, ribbed, open collar")])
        self.assertEqual(next(r for r in rows if r["row"].startswith("Knit polos"))["covered_by"], ["w2"])

    def test_a_cancelled_want_covers_nothing(self):
        rows = self.rows([want("w1", "shirt", "white OCBD", status="fulfilled")])
        self.assertEqual(next(r for r in rows if r["row"] == "Casual button-ups")["covered_by"], [])

    def test_filled_rows_are_not_gaps(self):
        self.assertNotIn("Suits", [r["row"] for r in self.rows([])])


class AddTests(unittest.TestCase):
    def test_add_shells_out_once_per_gap_with_the_plan_in_the_note(self):
        calls = []

        class R:
            returncode = 0; stdout = "Added watch-x"; stderr = ""
        rows = [r for r in pw.missing(MD, closet=[], wants=[]) if not r["covered_by"]]
        made = pw.add(rows, run=lambda *a, **k: (calls.append(a[0]), R())[1])
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(x["ok"] for x in made))
        first = calls[0]
        self.assertTrue(str(first[1]).endswith("shopping_watchlist.py"))
        self.assertEqual(first[first.index("--category") + 1], "shirt")
        self.assertEqual(first[first.index("--source") + 1], "gap-detected")
        note = first[first.index("--notes") + 1]
        self.assertIn("WARDROBE PLAN GAP (P1)", note, "the plan table's ** markers are stripped")
        self.assertIn("2 of 8", note); self.assertIn("OCBD", note)

    def test_reporting_is_the_default(self):
        src = (SCRIPTS / "plan_wants.py").read_text()
        self.assertIn('ap.add_argument("--add"', src)
        self.assertIn("It REPORTS by default and only writes with --add", src)


if __name__ == "__main__":
    unittest.main()
