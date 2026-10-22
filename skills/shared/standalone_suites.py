#!/usr/bin/env python3
"""Find and run the test files `unittest discover` cannot see.

WHY (2026-10-08): a test file that defines no `unittest.TestCase` — several here use their
own `check()` harness and exit non-zero — is invisible to `python3 -m unittest discover`,
which is what the harness (test-fixtures/test-all.mjs) and the nightly job both run. The
finance skill had thirteen such files, roughly 367 assertions, that nothing executed; one
had been failing for days without anyone noticing, because its expectation went stale the
day an owner-reviewed merchant alias was added to config.

A gate per skill uses this: it discovers its own directory, runs each hidden suite as its
own process, and fails with that suite's output. A new custom-harness file is covered the
moment it is written, so nobody has to remember to register it.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

TIMEOUT = 300


def files(directory, exclude=()):
    """Sibling test files that `unittest discover` collects nothing from."""
    skip = {Path(name).name for name in exclude}
    found = []
    for path in sorted(Path(directory).glob("test_*.py")):
        if path.name in skip:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "unittest.TestCase" not in text:
            found.append(path)
    return found


SKILLS_ROOT = Path(os.path.expanduser("~/.openclaw/workspace/Skills")).expanduser()


def entry_point_offenders(root=SKILLS_ROOT):
    """Test files that define a class or test BELOW `if __name__ ...: unittest.main()`.

    WHY (2026-10-09): a third way a suite goes dark. Run as a script — which is what
    each skill's own docs and runners do — `unittest.main()` exits the process before
    Python ever defines a class written under it, so the file reports OK having skipped
    those tests. `unittest discover` imports the module without running `__main__`, so
    the nightly harness DID collect them and nobody saw a red line either way.
    Found in five files, sixteen tests: email-monitor's FinanceFeedsStaySilent (the
    2026-10-06 double-FYI guard) had never run once.

    Returns [(path, [offending lines])].
    """
    offenders = []
    for path in sorted(Path(root).glob("*/scripts/test_*.py")):
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        entry = [i for i, line in enumerate(lines) if line.startswith("if __name__")]
        if not entry:
            continue
        below = [line.strip() for line in lines[entry[0]:]
                 if line.startswith("class ") or line.strip().startswith("def test")]
        if below:
            offenders.append((path, below))
    return offenders


def check_entry_points(case, root=SKILLS_ROOT):
    """Fail `case` naming every suite with tests stranded below its entry point."""
    offenders = entry_point_offenders(root)
    if offenders:
        detail = "\n".join(
            f"  {path.relative_to(Path(root).parent)}: {len(found)} stranded — {found[0]}"
            for path, found in offenders)
        case.fail("tests defined below unittest.main() never run as a script:\n" + detail
                  + "\nMove the `if __name__` block to the end of the file.")


def check_all(case, directory, exclude=()):
    """Run each hidden suite; fail `case` with the first failure's own output."""
    found = files(directory, exclude)
    case.assertGreater(len(found), 0,
                       f"No custom-harness suites found in {Path(directory).name}; "
                       "either they all grew TestCase classes or the detection broke")
    for path in found:
        with case.subTest(suite=path.name):
            run = subprocess.run([sys.executable, str(path)], cwd=str(path.parent),
                                 capture_output=True, text=True, timeout=TIMEOUT)
            if run.returncode != 0:
                tail = (run.stdout or "")[-1500:] + (run.stderr or "")[-1500:]
                case.fail(f"{path.name} exited {run.returncode}:\n{tail}")
