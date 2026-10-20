#!/usr/bin/env python3
"""The board as the user reads it: photos-only image, a caption that names the pieces, four buttons.

2026-10-13: "I dont really like the way it has looked in the last ones we have created."
The board was a tall scroll with letterboxed photos and truncated style codes; the caption
never named a piece but carried a leaked triage line, a 50-word paragraph and a repeated
temperature; the keyboard was ten flat buttons. He chose option B: image = photos only.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ["WARDROBE_NO_SEND"] = "1"
os.environ["TELEGRAM_SEND_DISABLED"] = "1"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import outfit_board as ob  # noqa: E402
import proposal_caption as pc  # noqa: E402

CLOSET = [
    {"id": "polo", "brand": "Holderness & Bourne", "color": "light blue with navy micro-print", "category": "polo"},
    {"id": "pants", "brand": "johnnie-O", "color": "stone / light greige", "category": "pants"},
    {"id": "shoes", "brand": "G/FORE", "model": "Gallivanter (style GMF000055, 'Snow')", "color": "white", "category": "specialty_footwear"},
    {"id": "belt", "brand": "Holderness & Bourne", "color": "navy with whiskey-brown dogleg stripe", "category": "belt"},
]
PROPOSAL = {"id": "abc123def456", "event_title": "Golf <> the user @ Example Hills", "event_start": "2026-10-17T08:10:00-05:00",
            "items": ["shoes", "belt", "polo", "pants"], "reason": "Cool start, long sleeves not needed by the turn.",
            "meta": {"why": "a real round of golf", "kind": "golf-round", "forecast": {"feels_like_f": 72.4, "wind_mph": 6, "rain_chance_pct": 5, "place": "Dallas, TX"}}}


class CaptionTests(unittest.TestCase):
    def test_names_every_piece_in_dressing_order(self):
        text = pc.caption(PROPOSAL, closet=CLOSET)
        self.assertEqual(text.splitlines(), [
            "👔 Golf · Sat 8:10am · 72°F",
            "• H&B blue polo",
            "• johnnie-O stone pants",
            "• G/FORE white golf shoes",
            "• H&B navy belt",
            "Cool start, long sleeves not needed by the turn."])

    def test_no_leaked_triage_line_and_one_temperature(self):
        text = pc.caption(PROPOSAL, closet=CLOSET)
        self.assertNotIn("Why:", text)
        self.assertNotIn("Forecast:", text)
        self.assertEqual(text.count("72"), 1)
        self.assertNotIn("wind", text.lower())

    def test_a_long_stylist_line_is_cut_on_a_word(self):
        p = dict(PROPOSAL, reason="The heather grey body with navy contrast collar reads clean and deliberate for an indoor simulator club, tonal grey layers without competing.")
        last = pc.caption(p, closet=CLOSET).splitlines()[-1]
        self.assertTrue(last.endswith("…")); self.assertLessEqual(len(last), 121)
        self.assertFalse(last[:-1].endswith(" "))

    def test_status_still_shows_after_a_tap(self):
        self.assertIn("✅ Worn, logged.", pc.caption(dict(PROPOSAL, status="worn"), closet=CLOSET))


class BoardTests(unittest.TestCase):
    def pairs(self):
        return [(i, None) for i in CLOSET]

    def test_bare_board_carries_no_names_or_title(self):
        """Option B: the image is the photos; the caption does the naming, once.
        The only words left are the 'no photo yet' placeholder on a piece without one."""
        svg = ob.build_svg(self.pairs(), "Golf · Sat 8:10am", bare=True)
        for word in ("Golf", "Sat", "johnnie-O", "G/FORE", "Bourne", "polo", "belt"):
            self.assertNotIn(word, svg, word)
        self.assertEqual(svg.count("<text"), len(CLOSET), "one placeholder per photoless piece, nothing else")

    def test_labelled_board_drops_style_codes_and_fits(self):
        self.assertEqual(ob.item_label(CLOSET[2]), "G/FORE")
        self.assertEqual(ob.item_sublabel(CLOSET[2]), "white golf shoes")
        self.assertEqual(ob.item_sublabel(CLOSET[3]), "navy belt")

    def test_pieces_are_laid_out_in_dressing_order(self):
        svg = ob.build_svg([(CLOSET[3], None), (CLOSET[2], None), (CLOSET[0], None), (CLOSET[1], None)], "t")
        order = [svg.index(x) for x in ("Holderness &amp; Bourne", "johnnie-O", "G/FORE")]
        self.assertEqual(order, sorted(order), "top, then bottoms, then shoes, then belt")

    def test_one_row_for_four_pieces(self):
        svg = ob.build_svg(self.pairs(), "t", bare=True)
        import re
        w, h = (int(re.search(rf'{a}="(\d+)"', svg).group(1)) for a in ("width", "height"))
        self.assertGreater(w, h, "landscape, so the whole outfit fits a phone screen")


class ButtonTests(unittest.TestCase):
    def markup(self, menu=None):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sop", SCRIPTS / "send_outfit_proposal.py")
        return None  # the module needs a bot token at import; shape is asserted below instead

    def test_four_buttons_then_submenus(self):
        src = (SCRIPTS / "send_outfit_proposal.py").read_text()
        main = src[src.index('return {"inline_keyboard": [\n        [{"text": "✅ Wore it"'):]
        self.assertIn('"🔁 Swap"', main); self.assertIn('"✏️ Not right"', main)
        self.assertIn('outfit:menu-{pid}-swap', main); self.assertIn('outfit:menu-{pid}-wrong', main)
        self.assertIn('◀ Back', src)
        for old in ("Plan this", "Another"):
            self.assertNotIn(old, src)

    def test_menu_action_records_nothing(self):
        src = (SCRIPTS / "outfit_callback.py").read_text()
        block = src[src.index('if args.action.startswith("menu-"):'):src.index('if args.action.startswith("swap-"):')]
        self.assertNotIn("record(", block, "opening a menu is not an opinion about the outfit")


if __name__ == "__main__":
    unittest.main()
