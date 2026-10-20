#!/usr/bin/env python3
"""pinterest_board_watch.py against a fixture feed; sandboxed, no network, no live files."""
import json
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
import pinterest_board_watch as pw  # noqa: E402
pw.FEED_PAUSE_S = 0

pw.INSPIRATION_DIR = Path(SANDBOX.name) / "inspiration"   # never the live photo folder
for p in (pw.BOARDS_PATH, pw.STATE_PATH, pw.PINS_PATH):
    assert str(p).startswith(SANDBOX.name), p

FEED = b"""<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>Fashion Ideas</title>
<item><title>example.creator</title><link>https://www.pinterest.com/pin/111/</link><description>&lt;a href="x"&gt;&lt;img src="https://i.pinimg.com/236x/aa/bb/cc.jpg"&gt;&lt;/a&gt;</description><pubDate>Mon, 12 Oct 2026 06:08:50 GMT</pubDate></item>
<item><title></title><link>https://www.pinterest.com/pin/222/</link><description>&lt;img src="https://i.pinimg.com/236x/dd/ee/ff.jpg"&gt;</description><pubDate>Mon, 12 Oct 2026 06:06:56 GMT</pubDate></item>
<item><title>share</title><link>https://www.pinterest.com/pin/8rSnAwWm/</link><description></description><pubDate>Invalid Date</pubDate></item>
<item><title>dup</title><link>https://www.pinterest.com/pin/333/</link><description>&lt;img src="https://i.pinimg.com/236x/aa/bb/cc.jpg"&gt;</description><pubDate>Mon, 12 Oct 2026 06:00:00 GMT</pubDate></item>
</channel></rss>"""
IMAGES = {"https://i.pinimg.com/originals/aa/bb/cc.jpg": b"JPEG-A", "https://i.pinimg.com/originals/dd/ee/ff.jpg": b"JPEG-B"}


def fake_fetch(url):
    if url.endswith(".rss"):
        return FEED
    return IMAGES[url]


class PinterestTests(unittest.TestCase):
    def setUp(self):
        self.boards = [{"id": "fashion", "url": "https://www.pinterest.com/example-user/fashion/", "enabled": True}]

    def test_parse_feed_upgrades_image_to_original(self):
        items = pw.parse_feed(FEED)
        self.assertEqual([i["pin_id"] for i in items], ["111", "222", "333"], "the share link is not a pin")
        self.assertEqual(items[0]["image"], "https://i.pinimg.com/originals/aa/bb/cc.jpg")
        self.assertEqual(pw.feed_url(self.boards[0]["url"]), "https://www.pinterest.com/example-user/fashion.rss")

    def test_dry_run_writes_nothing(self):
        state, pins = {}, []
        rep = pw.import_boards(self.boards, state, pins, fetch=fake_fetch, dry_run=True)
        self.assertEqual(rep["boards"], [{"board": "fashion", "in_feed": 3, "new": 3}])
        self.assertEqual((state, pins), ({}, []))
        self.assertFalse(pw.INSPIRATION_DIR.exists())

    def test_import_saves_images_dedupes_and_is_idempotent(self):
        state, pins = {}, []
        rep = pw.import_boards(self.boards, state, pins, fetch=fake_fetch)
        self.assertEqual(len(pins), 3)
        files = sorted(p.name for p in pw.INSPIRATION_DIR.iterdir())
        self.assertEqual(files, ["pinterest-111.jpg", "pinterest-222.jpg"], "the duplicate image is not saved twice")
        self.assertEqual(pins[2]["duplicate_of"], "111"); self.assertEqual(pins[2]["file"], pins[0]["file"])
        self.assertEqual(state["seen"], ["111", "222", "333"])
        rep2 = pw.import_boards(self.boards, state, pins, fetch=fake_fetch)
        self.assertEqual(rep2["boards"][0]["new"], 0); self.assertEqual(len(pins), 3)

    def test_fetches_are_merged_so_rotating_slices_all_land(self):
        slices = [FEED, FEED.replace(b"/pin/111/", b"/pin/444/"), FEED, FEED]
        calls = []
        def rotating(url):
            if url.endswith(".rss"):
                calls.append(url); return slices[len(calls) - 1]
            return IMAGES.get(url, b"JPEG-X")
        rep = pw.import_boards(self.boards, {}, [], fetch=rotating, dry_run=True)
        self.assertEqual(len(calls), pw.FEED_FETCHES)
        self.assertEqual(rep["boards"][0]["in_feed"], 4, "111, 222, 333 and the 444 only one slice showed")

    def test_feed_failure_leaves_record_untouched(self):
        def broken(url):
            raise RuntimeError("HTTP 403")
        state, pins = {"seen": ["111"]}, [{"pin_id": "111", "image_sha256": None}]
        rep = pw.import_boards(self.boards, state, pins, fetch=broken)
        self.assertEqual(rep["errors"][0]["board"], "fashion")
        self.assertEqual(state["seen"], ["111"]); self.assertEqual(len(pins), 1)


if __name__ == "__main__":
    unittest.main()
