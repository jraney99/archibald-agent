#!/usr/bin/env python3
"""nightly_guard_check: silent on pass, one alert per distinct failure, never nags. Sends nothing."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "nightly_guard_check.py"
state = Path(tempfile.mkdtemp()) / "state.json"


def check(suite_py, *extra):
    cmd = [sys.executable, str(SCRIPT), "--state", str(state), "--suite-cmd", json.dumps([sys.executable, "-c", suite_py]), *extra]
    done = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "GUARD_CHECK_NO_SEND": "1"})
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


PASS = "print('All 8 guard/hook suites passed')"
DEAD = ("import sys; print('DEAD BUTTON  ai-wardrobe-steward/scripts/send_outfit_proposal.py:59  outfit:formal-{args.id}');"
        "print('DEAD BUTTON  ai-wardrobe-steward/scripts/send_outfit_proposal.py:60  outfit:casual-{args.id}'); sys.exit(1)")
OTHER = "import sys; sys.stderr.write('AssertionError: Runtime boundary changed: dist/x.js; review and refresh fixtures'); sys.exit(1)"

# The real shape of a unittest failure (2026-09-28): the actionable half sits on the
# lines AFTER the assertion repr, and it said the OPPOSITE of what the user was told — a
# script had been FIXED and the baseline needed re-snapshotting, not a new violation.
UNITTEST_OUTPUT = "\n".join([
    'FAIL: test_baseline_only_shrinks (__main__.Layout)',
    'Traceback (most recent call last):',
    '  File "workspace/Skills/shared/test_data_layout.py", line 96, in test_baseline_only_shrinks',
    'AssertionError: {\'workspace/Skills/shared/add_note.py\': [\'journal_notes.json\']} != {}',
    '- {\'workspace/Skills/shared/add_note.py\': [\'journal_notes.json\']}',
    '+ {} :',
    'Fixed - now take these out of test-fixtures/data-layout-baseline.json (python3 test_data_layout.py --write-baseline):',
    '{',
    '  "workspace/Skills/shared/add_note.py": [',
])
UNITTEST = 'import sys; print(%r); sys.exit(1)' % UNITTEST_OUTPUT

assert check(PASS) == {"status": "pass", "alert": None}                                  # passing: silent
first = check(DEAD)
assert first["alert"] == ("⚠️ Daily self-check failed: a button sent by ai-wardrobe-steward/scripts/send_outfit_proposal.py "
                          "never reaches its handler: outfit:formal-{args.id} (+1 more)\n"
                          "Nothing was changed. Ask a Claude Code session to run the guard suite. 🎩"), first
assert check(DEAD)["alert"] is None and check(DEAD)["alert"] is None                     # same failure next mornings: no nagging
changed = check(OTHER)
assert changed["alert"].startswith("⚠️ Daily self-check failed: Runtime boundary changed: dist/x.js")   # a different failure: tell him
assert check(PASS)["alert"] is None
assert check(DEAD)["alert"] is not None                                                  # fixed, then broke again: tell him again
before = state.read_text()
preview = check(OTHER, "--dry-run")
assert preview["dry_run"] is True and preview["alert"] and state.read_text() == before    # dry run remembers nothing
unit = check(UNITTEST)                                                                   # names the test AND its own message
assert "test_baseline_only_shrinks" in unit["summary"], unit
assert "take these out of test-fixtures/data-layout-baseline.json" in unit["summary"], unit
assert "!= {}" not in unit["summary"], unit                                              # never the bare repr again
assert check(PASS)["alert"] is None

# 2026-10-12: he got "callback: Ran 31 tests in 7.060s" — two unrelated failures
# welded together, explaining neither. The name came from a HARNESS line truncated at
# its hyphen, and the "reason" was mined from a different suite's AssertionError and
# landed on a test counter. The fix sentence was on the matched line and was dropped.
DRIFT = ("import sys; print('PASS: claim-guard \u2014 loaded content stamp matches disk');"
         "print('FAIL: callback-dispatch \u2014 loaded content stamp differs from disk; restart the gateway');"
         "print('Ran 31 tests in 7.060s'); sys.exit(1)")
drift = check(DRIFT)
assert "callback-dispatch" in drift["summary"], drift                                    # whole name, not "callback"
assert "restart the gateway" in drift["summary"], drift                                  # the actionable half survives
assert "Ran 31 tests" not in drift["summary"], drift                                     # a counter is never a reason
assert check(PASS)["alert"] is None

# Both shapes present: the summary must describe the FIRST failure and not mix them.
WELDED = ("import sys; print('FAIL: callback-dispatch \u2014 loaded content stamp differs from disk; restart the gateway');"
          "print('=' * 70); print('FAIL: test_status_report (__main__.TestSnapshots)');"
          "print('-' * 70); print('AssertionError: Regex did not match');"
          "print('the report format moved'); print('Ran 31 tests in 7.060s'); sys.exit(1)")
welded = check(WELDED)
assert "callback-dispatch" in welded["summary"], welded
assert "restart the gateway" in welded["summary"], welded
assert "Regex" not in welded["summary"] and "report format" not in welded["summary"], welded  # no welding
assert check(PASS)["alert"] is None

# A unittest failure must not borrow a LATER failure's message either.
TWO_UNITS = ("import sys; print('=' * 70); print('FAIL: test_alpha (__main__.A)'); print('-' * 70);"
             "print('AssertionError: alpha broke'); print('alpha has its own message');"
             "print('=' * 70); print('FAIL: test_beta (__main__.B)'); print('-' * 70);"
             "print('AssertionError: beta broke'); print('beta has a different message');"
             "print('Ran 2 tests in 0.1s'); sys.exit(1)")
two = check(TWO_UNITS)
assert "test_alpha" in two["summary"], two
assert "alpha has its own message" in two["summary"], two
assert "beta" not in two["summary"], two
assert check(PASS)["alert"] is None

hang = check("import time; time.sleep(0)")                                               # exits 0 quickly: a pass
assert hang["status"] == "pass"
print("nightly_guard_check: all assertions passed")
