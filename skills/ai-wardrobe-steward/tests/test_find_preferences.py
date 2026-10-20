#!/usr/bin/env python3
"""The shopping feedback LOOP, end to end in a sandbox: send → tap → aggregate → guidance.

2026-10-13: the 👍/👎 buttons were wired to write a style signal that nothing read —
`learned_adjustments.py` filters to source "outfit-proposal-response" only — so taps
accumulated and no recommendation ever changed. This test exists to keep the loop closed.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

SANDBOX = tempfile.TemporaryDirectory()
os.environ["OPENCLAW_HOME"] = SANDBOX.name
os.environ["WARDROBE_NO_SEND"] = "1"
os.environ["TELEGRAM_SEND_DISABLED"] = "1"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
import send_find as sf  # noqa: E402
import find_feedback as ff  # noqa: E402
import find_preferences as fp  # noqa: E402

SB = Path(SANDBOX.name)
ff.DECISIONS = SB / "purchase_decisions.json"
ff.SIGNALS = fp.SIGNALS = SB / "style_signals.json"
ff.colour_family = lambda h: {"#1F2449": "blues", "#D2C7BB": "neutrals", "#B0303A": "reds"}.get((h or "").upper(), "")
for p in (sf.STORE, ff.STORE):
    assert str(p).startswith(SANDBOX.name), p
TODAY = date(2026, 10, 13)


class LoopTests(unittest.TestCase):
    def setUp(self):
        sf.STORE.parent.mkdir(parents=True, exist_ok=True)
        sf.STORE.write_text("{}")
        ff.DECISIONS.write_text(json.dumps([{"id": f"d{i}", "category": "sweater", "user_action": "unknown"} for i in range(6)]))
        ff.SIGNALS.write_text("[]")

    def tap(self, decision_id, label, answer, brand="", material="", hex_value=""):
        records, _ = sf.build([(decision_id, label, brand, material, hex_value)])
        store = json.load(open(sf.STORE))
        store.update({t: {**r, "source_tg_msg_id": 1} for t, r in records.items()})
        json.dump(store, open(sf.STORE, "w"))
        return ff.record(list(records)[0], answer)

    def test_a_find_carries_its_attributes_through_to_the_signal(self):
        self.tap("d0", "H&B cream crewneck", "like", "Holderness & Bourne", "merino", "#D2C7BB")
        sig = json.load(open(ff.SIGNALS))[0]
        self.assertEqual((sig["brand"], sig["material"], sig["colour_family"]),
                         ("Holderness & Bourne", "merino", "neutrals"))

    def test_one_tap_is_a_mood_not_a_preference(self):
        self.tap("d0", "H&B cream crewneck", "like", "Holderness & Bourne", "merino", "#D2C7BB")
        s = fp.summarise(json.load(open(ff.SIGNALS)), today=TODAY)
        self.assertEqual(s["taps"], 1)
        self.assertEqual(s["prefer"], {}, "a single tap must not become a rule")
        self.assertIn("none repeated enough", fp.sentence(s))

    def test_repeated_taps_become_guidance(self):
        for i, lbl in enumerate(["H&B cream crewneck", "H&B navy crewneck", "PM cream vest"]):
            self.tap(f"d{i}", lbl, "like", "Holderness & Bourne" if i < 2 else "Peter Millar", "merino", "#D2C7BB")
        for i, lbl in enumerate(["Loud red polo", "Bright red quarter zip"], start=3):
            self.tap(f"d{i}", lbl, "dislike", "Brand X", "polyester", "#B0303A")
        s = fp.summarise(json.load(open(ff.SIGNALS)), today=TODAY)
        self.assertEqual(s["taps"], 5)
        self.assertEqual([x["value"] for x in s["prefer"]["material"]], ["merino"])
        self.assertEqual([x["value"] for x in s["prefer"]["brand"]], ["Holderness & Bourne"])
        self.assertEqual([x["value"] for x in s["avoid"]["material"]], ["polyester"])
        self.assertEqual([x["value"] for x in s["avoid"]["colour_family"]], ["reds"])
        line = fp.sentence(s)
        self.assertIn("He has liked", line); self.assertIn("merino", line)
        self.assertIn("He has passed on", line); self.assertIn("polyester", line)

    def test_old_opinions_fade(self):
        old = date(2025, 10, 13).isoformat()
        signals = [{"source": fp.SOURCE, "action": "liked", "date": old, "material": "linen", "brand": "", "colour_family": "", "category": ""}
                   for _ in range(2)]
        s = fp.summarise(signals, today=TODAY)
        self.assertEqual(s["taps"], 2)
        self.assertEqual(s["prefer"], {}, "a year-old pair of taps is not current taste")
        recent = [{**x, "date": TODAY.isoformat()} for x in signals]
        self.assertEqual([x["value"] for x in fp.summarise(recent, today=TODAY)["prefer"]["material"]], ["linen"])

    def test_outfit_board_signals_are_ignored(self):
        signals = [{"source": "outfit-proposal-response", "action": "skip", "date": TODAY.isoformat(), "material": "merino"}] * 4
        self.assertEqual(fp.summarise(signals, today=TODAY)["taps"], 0)

    def test_empty_says_so_plainly(self):
        self.assertIn("nothing learned", fp.sentence(fp.summarise([], today=TODAY)))


if __name__ == "__main__":
    unittest.main()
