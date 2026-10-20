#!/usr/bin/env python3
"""purchase_match, fit prompt + callback, closet_looks, plan_progress, cost_per_wear — sandboxed, no sends."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SANDBOX = tempfile.TemporaryDirectory()
os.environ["OPENCLAW_HOME"] = SANDBOX.name
os.environ["WARDROBE_NO_SEND"] = "1"
os.environ["TELEGRAM_SEND_DISABLED"] = "1"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
import purchase_match as pm  # noqa: E402
import send_fit_prompt as sfp  # noqa: E402
import fit_callback as fc  # noqa: E402
import closet_looks as cl  # noqa: E402
import plan_progress as pp  # noqa: E402
import cost_per_wear as cpw  # noqa: E402
import fit_detail as fd  # noqa: E402

SB = Path(SANDBOX.name)
sfp.GATE = SB / "wardrobe_prompts.json"   # never the live gate: it is ON since 2026-10-13
for mod, attr in ((sfp, "LIFECYCLE"), (fc, "LIFECYCLE"), (fc, "PENDING_FIT"), (fd, "LIFECYCLE"), (fd, "PENDING_FIT")):
    setattr(mod, attr, SB / f"{attr.lower()}.json")   # never the live profile
assert str(sfp.PROMPTS).startswith(SANDBOX.name) and str(fc.PROMPTS).startswith(SANDBOX.name)
assert str(fc.PENDING_DETAIL).startswith(SANDBOX.name) and str(fd.PENDING_DETAIL).startswith(SANDBOX.name)


class PurchaseMatchTests(unittest.TestCase):
    def test_decision_and_want_close(self):
        decisions = [{"id": "d1", "item_description": "Holderness & Bourne The Mills Jogger Heathered Navy, size L", "user_action": "unknown"},
                     {"id": "d2", "item_description": "Faherty Movement Polo", "user_action": "unknown"}]
        wants = [{"id": "w1", "status": "active", "category": "pants", "criteria": {"brands": ["Holderness & Bourne"]}},
                 {"id": "w2", "status": "active", "category": "polo", "criteria": {"brands": []}}]
        out = pm.close_loop("Holderness & Bourne", "The Mills Jogger - Heathered Navy - L", decisions=decisions, wants=wants, dry_run=True)
        self.assertEqual((out["category"], out["decision"], out["want"]), ("pants", "d1", "w1"))
        self.assertEqual(decisions[0]["user_action"], "unknown", "dry run writes nothing")
        out = pm.close_loop("Faherty", "Short-Sleeve Movement Polo Navy", decisions=decisions, wants=wants, dry_run=True)
        self.assertEqual((out["decision"], out["want"]), ("d2", "w2"))
        self.assertIsNone(pm.match_decision(decisions, "Todd Snyder", "Linen Polo"), "retailer must match")


class FitPromptTests(unittest.TestCase):
    def setUp(self):
        json.dump([{"id": "purchase-abc", "retailer": "Holderness & Bourne", "item": "The Mills Jogger Navy L", "status": "delivered", "fit_verdict": None, "notes": []}], open(sfp.LIFECYCLE, "w"))
        sfp.PROMPTS.parent.mkdir(parents=True, exist_ok=True); sfp.PROMPTS.write_text("{}")
        fc.PENDING_FIT.unlink(missing_ok=True)

    def test_prompt_is_held_until_enabled_and_matches_wording(self):
        pid, text, kb = sfp.build({"id": "purchase-abc", "retailer": "Holderness & Bourne", "item": "The Mills Jogger Navy L"})
        self.assertEqual(text, "📦 The Mills Jogger Navy L from Holderness & Bourne arrived. How's the fit?")
        self.assertEqual([b["callback_data"] for b in kb["inline_keyboard"][0]], [f"confirm:fit-{pid}-great", f"confirm:fit-{pid}-ok", f"confirm:fit-{pid}-poor"])
        sfp.GATE.write_text(json.dumps({"fit_prompts_enabled": False}))
        self.assertFalse(sfp.enabled(), "a closed gate holds the prompt")
        rc = sfp.main(["--purchase-id", "purchase-abc"])
        self.assertEqual(rc, 0); self.assertEqual(json.load(open(sfp.PROMPTS))[pid]["status"], "held")
        sfp.GATE.write_text(json.dumps({"fit_prompts_enabled": True}))
        self.assertTrue(sfp.enabled(), "an open gate lets it send")
        self.assertFalse(sfp.GATE.exists() and "live" in str(sfp.GATE))

    def test_callback_records_verdict_and_sizing(self):
        pid, _, _ = sfp.build({"id": "purchase-abc", "retailer": "Holderness & Bourne", "item": "The Mills Jogger Navy L"})
        sfp.main(["--purchase-id", "purchase-abc"])
        calls = []
        text, _ = fc.record(pid, "poor", run=lambda *a, **k: calls.append(a[0]))
        self.assertTrue(text.startswith("👎 Poor fit noted") and "Tell me what's off" in text, text)
        q = json.load(open(fc.PENDING_DETAIL))
        self.assertEqual((q["id"], q["verdict"], q["category"], q["size"]), (pid, "poor", "pants", "L"))
        self.assertEqual(json.load(open(fc.LIFECYCLE))[0]["fit_verdict"], "poor")
        pend = json.load(open(fc.PENDING_FIT))["add"]["brand_sizing"][0]
        self.assertEqual((pend["brand"], pend["category"], pend["size"]), ("Holderness & Bourne", "pants", "L"))
        self.assertTrue(str(calls[0][1]).endswith("confirm_body_fit_update.py"))
        self.assertTrue(fc.record(pid, "ok")[0].startswith("No open fit question"), "second tap is a no-op")
        # his typed words close the question and land on the purchase + brand sizing
        fc.PENDING_FIT.unlink(missing_ok=True)
        line = fd.record(pid, "tight across the thighs, waist fine, could go 36", run=lambda *a, **k: calls.append(a[0]))
        self.assertIn('"tight across the thighs, waist fine, could go 36"', line)
        self.assertFalse(fc.PENDING_DETAIL.exists(), "question closed")
        notes = json.load(open(fc.LIFECYCLE))[0]["notes"]
        self.assertTrue(any("fit detail (poor): tight across" in n["note"] for n in notes))
        self.assertIn("his words: tight across the thighs", json.load(open(fc.PENDING_FIT))["add"]["brand_sizing"][0]["notes"])
        with self.assertRaises(SystemExit):
            fd.record(pid, "again")

    def test_great_asks_nothing_more(self):
        pid, _, _ = sfp.build({"id": "purchase-abc", "retailer": "Holderness & Bourne", "item": "The Mills Jogger Navy L"})
        sfp.main(["--purchase-id", "purchase-abc"])
        text, _ = fc.record(pid, "great", run=lambda *a, **k: None)
        self.assertEqual(text, "👍 Great fit noted for The Mills Jogger Navy L. 🎩")
        self.assertFalse(fc.PENDING_DETAIL.exists())


class ClosetLooksTests(unittest.TestCase):
    def test_can_make_and_one_away(self):
        closet = [{"id": "a", "lifecycle_state": "active", "category": "polo", "color": "navy", "notes": ""},
                  {"id": "b", "lifecycle_state": "active", "category": "pants", "color": "stone / light greige", "notes": ""},
                  {"id": "c", "lifecycle_state": "retired", "category": "sneakers", "color": "white", "notes": ""}]
        pins = [{"pin_id": "1", "link": "l", "board": "fashion", "description": {"kind": "look", "summary": "navy polo + stone chinos", "pieces": [{"item": "polo", "colour": "navy"}, {"item": "chinos", "colour": "stone"}]}},
                {"pin_id": "2", "link": "l", "board": "fashion", "description": {"kind": "look", "summary": "with sneakers", "pieces": [{"item": "polo", "colour": "navy"}, {"item": "sneakers", "colour": "white"}]}},
                {"pin_id": "3", "link": "l", "board": "fashion", "description": {"kind": "piece", "pieces": [{"item": "polo", "colour": "navy"}]}}]
        res = cl.analyse(pins, closet)
        self.assertEqual((res["looks"], len(res["can_make"]), len(res["one_piece_away"])), (2, 1, 1))
        self.assertEqual(res["best_next_buys"], [{"piece": "white sneakers", "unlocks_looks": 1}], "retired items do not count")


class PlanProgressTests(unittest.TestCase):
    MD = """## 2. The finished wardrobe — target set (permanent layer)
| Category | Target | Have | Gap / action | Priority |
|---|---|---|---|---|
| Suits | 3 | 3 | — | done |
| Belts | 5 | 13 | cull 8 | P1 (cull) |
| Field jacket (waxed) | 1 | 0 | +1 | P2 |
### Rotation caps
## 2a. Wear capacity
| Category | Wear-days available/yr | ÷ min wears | **Ceiling** | Have | Read |
|---|---|---|---|---|---|
| Jeans | ~70 | 15 | ~4–5 | 3 | room |
## 3. Build sequence
"""

    def test_counts_come_from_the_plans_own_have_column(self):
        """2026-10-13: counting from closet categories alone called "layering sweaters: done"
        for his biggest gap, because a performance quarter-zip matched both rows. The
        stylist's own Have column decides what he is told; the computed count only flags drift."""
        closet = [{"category": "suit", "lifecycle_state": "active"}] * 3 + [{"category": "belt", "lifecycle_state": "active"}] * 6
        res = pp.progress(self.MD, closet)
        states = {r["category"]: r["state"] for r in res["rows"]}
        self.assertEqual(states, {"Suits": "done", "Belts": "over", "Field jacket (waxed)": "open"})
        belts = next(r for r in res["rows"] if r["category"] == "Belts")
        self.assertEqual((belts["plan_have"], belts["have"]), (13, 6), "plan says 13, closet shows 6")

    def test_the_message_never_nags_about_culling(self):
        """The ceiling gates buying, not culling (wardrobe-plan.md §2b). The user rejected the
        "over the cap" list on 2026-10-13, belts especially."""
        res = pp.progress(self.MD, [])
        msg = pp.message(res)
        self.assertNotIn("Over the cap", msg)
        self.assertNotIn("Belts", msg)
        self.assertNotIn("✂️", msg)
        self.assertIn("🎯 Next gaps", msg)
        self.assertIn("• Field jacket (waxed): 0 of 1 (P2)", msg)

    def test_ceilings_are_still_parsed(self):
        self.assertEqual(pp.parse_ceilings(self.MD), {"jeans": 4})

class CostPerWearTests(unittest.TestCase):
    def test_no_wear_data_is_said_plainly(self):
        res = cpw.report([{"id": "a", "brand": "X", "purchase_price": 50, "wear_count": 0}], [])
        self.assertTrue(res["no_wear_data"]); self.assertEqual(res["with_price"], 1)

    def test_cpw_from_history(self):
        closet = [{"id": "a", "brand": "X", "category": "polo", "color": "navy", "purchase_price": 50, "wear_count": 2, "lifecycle_state": "active"}]
        res = cpw.report(closet, [{"items": ["a"]}, {"items": ["a", "zzz"]}])
        self.assertEqual(res["best_value"][0]["cpw"], 12.5)   # $50 / (2 taps + 2 logged)
        self.assertEqual(res["brands"], [{"brand": "X", "avg_cpw": 12.5, "items": 1}])
        self.assertEqual(cpw.sell_candidates([{"id": "r", "lifecycle_state": "retired", "notes": "Sold 2026-09-10"}, {"id": "s", "lifecycle_state": "retired", "notes": "redundant"}])[0]["id"], "s")


if __name__ == "__main__":
    unittest.main()
