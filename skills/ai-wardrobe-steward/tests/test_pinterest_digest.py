#!/usr/bin/env python3
"""pinterest_describe / pinterest_digest / pinterest_digest_callback with a fake model; sandboxed."""
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
import pinterest_describe as pdsc  # noqa: E402
import pinterest_digest as pdg  # noqa: E402
import pinterest_digest_callback as pcb  # noqa: E402

for p in (pdsc.PINS_PATH, pdg.DIGEST_PATH, pdg.BOARDS_PATH):
    assert str(p).startswith(SANDBOX.name), p
pcb.PENDING_STYLE = Path(SANDBOX.name) / "pending_style_update.json"   # never the live learning layer


class FakeResp:
    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


class FakeClient:
    """Just enough of anthropic.Anthropic: client.messages.create(**kw) -> response."""
    def __init__(self, answers):
        self.answers = list(answers); self.calls = 0
        outer = self

        class Messages:
            def create(self, **kw):
                outer.calls += 1
                return FakeResp(outer.answers.pop(0))
        self.messages = Messages()


DESC = json.dumps({"kind": "look", "pieces": [{"item": "knit polo", "colour": "charcoal"}, {"item": "chinos", "colour": "stone"}], "summary": "Charcoal knit polo, stone chinos, relaxed"})
SUMMARY = json.dumps({"pieces": ["Charcoal ribbed knit polo"], "looks": [{"text": "Knit polo + stone chinos + loafers", "count": 4}], "taste": ["Knit polos, charcoal/navy, worn as a shirt", "Relaxed light chinos"]})


class DescribeTests(unittest.TestCase):
    def test_describe_pending_uses_fake_reader_and_dedupes(self):
        pins = [{"pin_id": "1", "file": "x.jpg", "description": None},
                {"pin_id": "2", "file": "x.jpg", "description": None, "duplicate_of": "1"},
                {"pin_id": "3", "file": None, "description": None}]
        client = FakeClient([DESC])
        rep = pdsc.describe_pending(pins, client, reader=lambda p: b"jpeg")
        self.assertEqual((rep["described"], rep["skipped_duplicates"], rep["errors"]), (1, 1, []))
        self.assertEqual(pins[0]["description"]["kind"], "look"); self.assertEqual(pins[1]["description"], pins[0]["description"])
        self.assertIsNone(pins[2]["description"]); self.assertEqual(client.calls, 1)

    def test_bad_model_output_is_an_error_not_a_crash(self):
        pins = [{"pin_id": "1", "file": "x.jpg", "description": None}]
        rep = pdsc.describe_pending(pins, FakeClient(["not json"]), reader=lambda p: b"jpeg")
        self.assertEqual(len(rep["errors"]), 1); self.assertIsNone(pins[0]["description"])


class DigestTests(unittest.TestCase):
    def test_message_matches_approved_template(self):
        piece = {"description": {"kind": "piece", "pieces": [{"item": "knit polo", "colour": "charcoal"}], "summary": "Charcoal knit polo"}}
        s = pdg.summarise([{"description": json.loads(DESC)}] * 2 + [piece], FakeClient([SUMMARY]))
        only_looks = pdg.summarise([{"description": json.loads(DESC)}] * 3, FakeClient([SUMMARY]))
        self.assertEqual(only_looks["pieces"], [], "no Pieces section when every pin is an outfit")
        text = pdg.build_message("Fashion Ideas", 25, s)
        self.assertTrue(text.startswith("📌 Pinterest · Fashion Ideas: 25 new pins\n\n👕 Pieces\n• Charcoal ribbed knit polo\n\n🧥 Looks\n• Knit polo + stone chinos + loafers ×4\n\n🎯 Your taste\n• Knit polos"), text)
        self.assertTrue(text.endswith("Add to your style profile?"))
        with_closet = pdg.build_message("Fashion Ideas", 25, s, (), "👔 You can already make 6 of these looks.")
        self.assertIn("\n👔 You can already make 6 of these looks.\n\nAdd to your style profile?", with_closet)
        self.assertNotIn("*", text); self.assertNotIn("#", text)
        kb = pdg.buttons("abcdef012345")["inline_keyboard"][0]
        self.assertEqual([b["callback_data"] for b in kb], ["confirm:pindigest-abcdef012345", "skip:pindigest-abcdef012345"])

    def test_pending_pins_excludes_digested_and_undescribed(self):
        pins = [{"description": {"kind": "piece"}}, {"description": {"kind": "piece"}, "digest_id": "x"}, {"description": None}]
        self.assertEqual(len(pdg.pending_pins(pins)), 1)


class WatchTests(unittest.TestCase):
    STORES = [{"slug": "holderness-bourne", "name": "Holderness & Bourne", "domain": "holdernessandbourne.com"}]

    def batch(self):
        return [{"pin_id": "1", "link": "https://www.pinterest.com/pin/1/", "source_url": "https://holdernessandbourne.com/products/x",
                 "description": {"kind": "piece", "pieces": [{"item": "knit polo", "colour": "charcoal"}]}},
                {"pin_id": "2", "link": "https://www.pinterest.com/pin/2/", "source_url": "https://golftoday.co.uk/article",
                 "description": {"kind": "piece", "pieces": [{"item": "leather brogues", "colour": "white"}]}},
                {"pin_id": "3", "link": "https://www.pinterest.com/pin/3/", "description": {"kind": "piece", "pieces": [{"item": "thingamajig", "colour": "red"}]}},
                {"pin_id": "4", "link": "https://www.pinterest.com/pin/4/", "description": {"kind": "look", "pieces": [{"item": "polo", "colour": "navy"}]}}]

    def test_watch_items_map_category_and_store(self):
        w = pdg.watch_items(self.batch(), self.STORES)
        self.assertEqual([(x["label"], x["category"], x["store"]) for x in w],
                         [("charcoal knit polo", "polo", "holderness-bourne"), ("white leather brogues", "dress_shoes", None)],
                         "unknown category and look pins get no button")
        text = pdg.build_message("Fashion Ideas", 4, {"pieces": ["ignored"], "looks": [], "taste": ["x"]}, w)
        self.assertIn("👕 Pieces\n• charcoal knit polo (Holderness & Bourne)\n• white leather brogues\n", text)
        kb = pdg.buttons("abcdef012345", w)["inline_keyboard"]
        self.assertEqual([b["callback_data"] for b in kb[1]], ["confirm:pinwatch-abcdef012345-0", "confirm:pinwatch-abcdef012345-1"])
        self.assertTrue(kb[1][0]["text"].startswith("🔍 Watch: knit polo"))
        w[0]["status"] = "watching"
        self.assertEqual(pdg.buttons("abcdef012345", w)["inline_keyboard"][1][0]["text"], "👀 Watching")

    def test_source_link_parses_pin_page(self):
        page = b'{"domain":"holdernessandbourne.com","link":"https:\\/\\/holdernessandbourne.com\\/products\\/x"}'
        self.assertEqual(pdsc.source_link("https://www.pinterest.com/pin/1/", fetch=lambda u: page), "https://holdernessandbourne.com/products/x")
        self.assertEqual(pdsc.source_link("https://www.pinterest.com/pin/1/", fetch=lambda u: (_ for _ in ()).throw(RuntimeError("x"))), "")


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.row = {"status": "asked", "board": "fashion", "pin_ids": ["1", "2", "3"], "source_tg_msg_id": 42,
                    "summary": json.loads(SUMMARY)}
        pdg.DIGEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"d1": self.row}, open(pdg.DIGEST_PATH, "w"))
        pcb.PENDING_STYLE.unlink(missing_ok=True)

    def test_skip_marks_answered_and_writes_no_profile(self):
        text, mid = pcb.answer("d1", "skip")
        self.assertTrue(text.startswith("⏭")); self.assertEqual(mid, 42)
        self.assertEqual(json.load(open(pdg.DIGEST_PATH))["d1"]["status"], "skip")
        self.assertFalse(pcb.PENDING_STYLE.exists())
        self.assertTrue(pcb.answer("d1", "skip")[0].startswith("No open digest"), "second tap is a no-op")

    def test_add_refuses_when_another_update_is_pending(self):
        pcb.PENDING_STYLE.write_text(json.dumps({"review_summary": "something else"}))
        text, _ = pcb.answer("d1", "add")
        self.assertTrue(text.startswith("⚠️"))
        self.assertEqual(json.load(open(pdg.DIGEST_PATH))["d1"]["status"], "asked", "still open after a refusal")

    def test_watch_adds_want_and_flips_only_that_button(self):
        row = dict(self.row); row["watch"] = pdg.watch_items(WatchTests().batch(), WatchTests.STORES)
        json.dump({"d1": row}, open(pdg.DIGEST_PATH, "w"))
        calls = []
        pcb.subprocess.run = lambda *a, **k: (calls.append(a[0]), type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())[1]
        text, mid = pcb.answer("d1", "watch", 0)
        self.assertEqual((text, mid), ("👀 Watching: charcoal knit polo at Holderness & Bourne. 🎩", 42))
        self.assertIn("--category", calls[0]); self.assertEqual(calls[0][calls[0].index("--category") + 1], "polo")
        self.assertEqual(calls[0][calls[0].index("--brands") + 1], "Holderness & Bourne")
        saved = json.load(open(pdg.DIGEST_PATH))["d1"]
        self.assertEqual([w["status"] for w in saved["watch"]], ["watching", "offered"])
        self.assertEqual(saved["status"], "asked", "Add/Skip still open after a watch tap")
        self.assertTrue(pcb.answer("d1", "watch", 0)[0].startswith("Already watching"))
        self.assertTrue(pcb.answer("d1", "watch", 7)[0].startswith("No such item"))

    def test_add_writes_inspirations_pending(self):
        calls = []
        pcb.subprocess.run = lambda *a, **k: (calls.append(a), type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())[1]
        text, _ = pcb.answer("d1", "add")
        self.assertTrue(text.startswith("✅ Added 2 lines"), text)
        pending = json.load(open(pcb.PENDING_STYLE))
        self.assertEqual([i["value"] for i in pending["add"]["inspirations"]], ["Pinterest: Knit polos, charcoal/navy, worn as a shirt", "Pinterest: Relaxed light chinos"])
        self.assertTrue(str(calls[0][0][1]).endswith("confirm_style_update.py"))


if __name__ == "__main__":
    unittest.main()
