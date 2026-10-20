#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from _state_lock import locked_state


class StateLock(unittest.TestCase):
    def test_corrupt_state_is_preserved_and_rejected(self):
        path = Path(tempfile.mkdtemp()) / "state.json"
        path.write_text("{broken")
        with self.assertRaises(ValueError):
            with locked_state(path, default={}):
                pass
        self.assertEqual(path.read_text(), "{broken")


if __name__ == "__main__":
    unittest.main()
