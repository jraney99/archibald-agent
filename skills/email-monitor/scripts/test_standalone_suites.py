#!/usr/bin/env python3
"""Gate for email-monitor test files that `unittest discover` cannot see."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "shared"))
import standalone_suites  # noqa: E402


class StandaloneSuites(unittest.TestCase):
    def test_every_hidden_suite_passes(self):
        standalone_suites.check_all(self, HERE, exclude=[Path(__file__).name])


if __name__ == "__main__":
    unittest.main(verbosity=2)
