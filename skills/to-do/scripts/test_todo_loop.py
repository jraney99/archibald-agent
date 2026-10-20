#!/usr/bin/env python3
"""Local regression tests for MEMORY.md and commitment-state consistency."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMITMENTS_PATH = Path(__file__).with_name("todo.py")
LOOP_PATH = ROOT / "loop-closer" / "scripts" / "loop_closer.py"

spec = importlib.util.spec_from_file_location("commitments_test_module", COMMITMENTS_PATH)
commitments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(commitments)

loop_spec = importlib.util.spec_from_file_location("loop_closer_test_module", LOOP_PATH)
loop = importlib.util.module_from_spec(loop_spec)
loop_spec.loader.exec_module(loop)


class CommitmentLoopConsistency(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.memory = self.tmp / "MEMORY.md"
        self.memory.write_text(
            "# Memory\n\n## Pending Actions\n"
            "- **🚨 CRITICAL:** Submit the signed form\n\n## Notes\n- Keep\n"
        )
        self.original = (
            commitments.DB_PATH,
            loop.MEMORY_PATH,
            loop.STATE_PATH,
            loop.commitments_store.DB_PATH,
            loop.edit_or_fallback,
        )
        db_path = self.tmp / "commitments.sqlite"
        commitments.DB_PATH = db_path
        loop.commitments_store.DB_PATH = db_path
        loop.MEMORY_PATH = self.memory
        loop.STATE_PATH = self.tmp / "loop.json"
        loop.edit_or_fallback = lambda _msg_id, text: text

    def tearDown(self):
        (commitments.DB_PATH, loop.MEMORY_PATH, loop.STATE_PATH,
         loop.commitments_store.DB_PATH, loop.edit_or_fallback) = self.original

    def test_close_updates_commitment_and_memory(self):
        loop.cmd_scan()
        state = json.loads(loop.STATE_PATH.read_text())
        item_id = next(iter(state["items"]))
        loop.cmd_close(item_id, "done", "uploaded receipt")

        source_key = commitments.memory_source_key(
            "**🚨 CRITICAL:** Submit the signed form")
        item = commitments.get_by_source_key(source_key)
        self.assertEqual(item["status"], "completed")
        self.assertIn("uploaded receipt", item["evidence"])
        self.assertNotIn("- **🚨 CRITICAL:** Submit", self.memory.read_text())


if __name__ == "__main__":
    unittest.main()
