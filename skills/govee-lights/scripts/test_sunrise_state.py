#!/usr/bin/env python3
""""Will my light come on tomorrow?" must be answered from real state.

2026-10-14: The user asked to turn off his sunrise for the SIXTH time in six weeks
and still could not tell what state it was in. The behaviour was fine — the ramp
runs with --follow-hatch and skips itself when the alarm is off. What failed
every time was the REPORT: "Bedroom sunrise — off" described an action attempted,
not what would happen. Two independent things decide it (the cron, and the Hatch
alarm) and no message had ever combined them.

    python3 test_sunrise_state.py
"""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "shared"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sr = _load("sunrise", _HERE / "sunrise.py")


def world(job_enabled=True, alarm_enabled=True, readable=True):
    job = {"id": "x", "name": sr.JOB_NAME, "enabled": job_enabled,
           "schedule": {"expr": "22 8 * * *"}}
    alarm = ({"readable": True, "enabled": alarm_enabled, "wake": "08:30",
              "ramp_start": "08:22"} if readable
             else {"readable": False, "why": "Hatch login failed"})
    return mock.patch.object(sr, "_job", return_value=job), \
        mock.patch.object(sr, "_alarm", return_value=alarm)


class BothThingsDecideIt(unittest.TestCase):
    def test_scheduled_and_alarm_on_means_it_runs(self):
        a, b = world()
        with a, b:
            s = sr.state()
        self.assertIs(s["will_run_tomorrow"], True)
        self.assertIn("08:22", sr.sentence(s))

    def test_alarm_off_means_no_sunrise_even_though_the_job_is_on(self):
        """The exact 2026-10-14 case. The job was enabled and I twice told the user his
        light would wake him; sunrise_ramp.py skips on --follow-hatch."""
        a, b = world(job_enabled=True, alarm_enabled=False)
        with a, b:
            s = sr.state()
        self.assertIs(s["will_run_tomorrow"], False)
        self.assertIn("alarm is off", s["because"])

    def test_job_off_means_no_sunrise_even_with_an_alarm(self):
        a, b = world(job_enabled=False, alarm_enabled=True)
        with a, b:
            s = sr.state()
        self.assertIs(s["will_run_tomorrow"], False)
        self.assertIn("job is switched off", s["because"])

    def test_an_unreadable_hatch_says_it_cannot_tell(self):
        """Never guess. A confident wrong answer is what started this."""
        a, b = world(readable=False)
        with a, b:
            s = sr.state()
        self.assertIsNone(s["will_run_tomorrow"])
        self.assertIn("can't say", sr.sentence(s))


class TheSentenceTellsHimWhatHappensNext(unittest.TestCase):
    def test_alarm_off_says_it_comes_back_by_itself(self):
        a, b = world(alarm_enabled=False)
        with a, b:
            said = sr.sentence(sr.state())
        self.assertIn("returns by itself", said)

    def test_job_disabled_says_it_stays_off_until_asked(self):
        """The trap: nothing re-enables it for him, so this must be said out loud."""
        a, b = world(job_enabled=False, alarm_enabled=False)
        with a, b:
            said = sr.sentence(sr.state())
        self.assertIn("stays off until you ask", said)

    def test_no_sentence_claims_an_action(self):
        for kw in (dict(), dict(alarm_enabled=False), dict(job_enabled=False)):
            a, b = world(**kw)
            with a, b:
                said = sr.sentence(sr.state()).lower()
            for claim in ("disabled", "turned off", "done"):
                self.assertNotIn(claim, said, f"{said!r} reports an action, not a state")


class OffAlwaysReadsBack(unittest.TestCase):
    def test_off_reports_state_not_the_attempt(self):
        a, b = world(alarm_enabled=False)
        import io, contextlib, json
        buf = io.StringIO()
        with a, b, mock.patch.object(sr.subprocess, "run",
                                     return_value=mock.Mock(returncode=0, stdout="", stderr="")), \
                contextlib.redirect_stdout(buf):
            sr.main(["off"])
        out = json.loads(buf.getvalue())
        self.assertIn("will_run_tomorrow", out)
        self.assertIn("said", out)
        self.assertIs(out["will_run_tomorrow"], False)

    def test_plain_off_does_not_disable_the_job(self):
        """Disabling would need a manual re-enable later; the alarm already
        suppresses it. Only --tomorrow-too touches the job."""
        a, b = world()
        calls = []
        import io, contextlib
        with a, b, mock.patch.object(sr.subprocess, "run",
                                     side_effect=lambda cmd, **k: calls.append(cmd) or
                                     mock.Mock(returncode=0, stdout="", stderr="")), \
                contextlib.redirect_stdout(io.StringIO()):
            sr.main(["off"])
        self.assertFalse(any("disable" in " ".join(map(str, c)) for c in calls))

    def test_tomorrow_too_does_disable_the_job(self):
        a, b = world()
        calls = []
        import io, contextlib
        with a, b, mock.patch.object(sr.subprocess, "run",
                                     side_effect=lambda cmd, **k: calls.append(cmd) or
                                     mock.Mock(returncode=0, stdout="", stderr="")), \
                contextlib.redirect_stdout(io.StringIO()):
            sr.main(["off", "--tomorrow-too"])
        self.assertTrue(any("disable" in " ".join(map(str, c)) for c in calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
