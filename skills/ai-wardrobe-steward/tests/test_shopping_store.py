#!/usr/bin/env python3
"""Tests for scripts/_store.py and scripts/repair_json.py.

Runs entirely inside a temp directory: never reads or writes the live profile/.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from _store import load_json, save_json  # noqa: E402
from repair_json import repair  # noqa: E402

# The exact shape found live on 2026-10-05 and 2026-10-08: a valid document
# followed by the tail of an older, longer version.
CORRUPT = json.dumps([{"id": "a", "history": [1]}], indent=2) + '5:00",\n    "price": 224.0\n  }\n]'


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "profile" / "x.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_returns_default(self):
        self.assertEqual(load_json(self.path, []), [])
        self.assertEqual(load_json(self.path, {}), {})
        self.assertFalse(self.path.exists())

    def test_round_trip_and_no_tmp_left(self):
        save_json(self.path, {"k": [1, 2]})
        self.assertEqual(load_json(self.path, {}), {"k": [1, 2]})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["x.json"])

    def test_corrupt_file_fails_loudly_and_is_untouched(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(CORRUPT)
        with self.assertRaises(SystemExit) as cm:
            load_json(self.path, [])
        self.assertIn("repair_json.py", str(cm.exception))
        self.assertEqual(self.path.read_text(), CORRUPT)

    def test_wrong_shape_fails(self):
        save_json(self.path, [])
        with self.assertRaises(SystemExit):
            load_json(self.path, {})

    def test_repair_recovers_prefix_and_backs_up(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(CORRUPT)
        backup_dir = self.dir / "state" / "corrupt"
        dry = repair(self.path, dry_run=True, backup_dir=backup_dir)
        self.assertEqual(dry["status"], "would-repair")
        self.assertEqual(self.path.read_text(), CORRUPT)
        res = repair(self.path, backup_dir=backup_dir)
        self.assertEqual(res["status"], "repaired")
        self.assertEqual(res["recovered_entries"], 1)
        self.assertEqual(json.loads(self.path.read_text()), [{"id": "a", "history": [1]}])
        self.assertEqual(Path(res["backup"]).read_text(), CORRUPT)
        self.assertEqual(repair(self.path, backup_dir=backup_dir)["status"], "already-valid")

    def test_concurrent_writers_never_leave_invalid_json(self):
        """The live failure: parallel truncate-and-write interleaving. Atomic replace
        means every observed state is one complete document."""
        bad = []
        stop = threading.Event()

        def writer(n):
            for i in range(60):
                save_json(self.path, {"writer": n, "i": i, "pad": "x" * (200 * (n + 1))})

        def reader():
            while not stop.is_set():
                if self.path.exists():
                    try:
                        json.loads(self.path.read_text())
                    except json.JSONDecodeError as exc:
                        bad.append(str(exc))
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        r = threading.Thread(target=reader)
        r.start()
        [t.start() for t in threads]
        [t.join() for t in threads]
        stop.set(); r.join()
        self.assertEqual(bad, [])
        json.loads(self.path.read_text())
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["x.json"])

    def test_cli_repair_dry_run(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(CORRUPT)
        out = subprocess.run([sys.executable, str(SCRIPTS / "repair_json.py"), str(self.path), "--dry-run"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["status"], "would-repair")




class SignalSourceTests(unittest.TestCase):
    """Every consumer of style_signals.json must filter by source.

    2026-10-13 audit: `style_learning.py` read EVERY signal and counted each one as a
    rejection, so a 👍 on a shopping find (source "shopping-find-feedback", action "liked")
    would have taught the engine to avoid what he liked. Three other consumers already
    filtered correctly; this keeps the fourth honest and catches the next new source.
    """

    CONSUMERS = ("style_learning.py", "learned_adjustments.py", "rule_proposals.py", "weekly_summary.py")

    def test_every_consumer_filters_by_source(self):
        for name in self.CONSUMERS:
            src = (SCRIPTS / name).read_text()
            self.assertIn("outfit-proposal-response", src, f"{name} must say which signals it means")
            self.assertRegex(src, r'source"?\)?\s*[!=]=\s*"outfit-proposal-response"',
                             f"{name} reads style_signals without filtering by source")


class SourceHealthIsReadBackTests(unittest.TestCase):
    """source_health.json existed to record which stores keep blocking us and NOTHING read it
    (2026-10-13 audit) — Peter Millar had 403'd on every run and the scan kept trying."""

    def test_blocked_lists_only_persistent_failures(self):
        sys.path.insert(0, str(SCRIPTS))
        import source_health as sh
        data = {"peter-millar": {"failures": 3, "last_status": "blocked", "note": "403"},
                "usga-shop": {"failures": 1, "last_status": "blocked", "note": "403"},
                "holderness-bourne": {"failures": 0, "last_status": "ok", "note": ""}}
        self.assertEqual([r["source"] for r in sh.blocked(data)], ["peter-millar"])
        self.assertEqual(sh.blocked(data, threshold=1), sorted(sh.blocked(data, threshold=1), key=lambda r: -r["failures"]))
        self.assertEqual(len(sh.blocked(data, threshold=1)), 2)
        self.assertIn("peter-millar", sh.report(data))
        self.assertIn("No source is persistently blocked", sh.report({"x": {"failures": 0}}))

if __name__ == "__main__":
    unittest.main()
