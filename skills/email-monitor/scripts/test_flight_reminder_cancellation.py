#!/usr/bin/env python3
"""
Regression tests for daemon.py's _cancel_stale_flight_reminders().

Background: a Southwest schedule change rebooked WN 200 (Jul 17) as WN 100
(Jul 21) under the same confirmation number (ABC123). The calendar event was
correctly renamed/rescheduled and new checkin-WN100-.../dayof-WN100-...
cron jobs were created, but the original checkin-WN200-.../dayof-WN200-...
jobs were never cancelled — they fired on their stale original schedule and
sent the user a check-in prompt + "leave by" travel reminder for a flight that
no longer departed at that time (see feedback-stale-flight-reminder-cron in
memory).

All `openclaw cron` subprocess calls are mocked — no live cron reads/writes.
Run manually after touching _cancel_stale_flight_reminders or
_schedule_flight_reminders:

    python3 test_flight_reminder_cancellation.py
"""
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

_DAEMON_PATH = Path(__file__).parent / "daemon.py"
_spec = importlib.util.spec_from_file_location("daemon", _DAEMON_PATH)
daemon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daemon)


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _jobs_payload(*jobs) -> str:
    return json.dumps({"jobs": list(jobs)})


STALE_CHECKIN = {
    "id": "old-checkin-id",
    "name": "checkin-WN200-20260717",
    "payload": {"message": "Check-in is open for WN 200 DEN → DAL · Fri Jul 17 10:45 AM.\nCheck in: https://www.southwest.com/air/check-in/index.html?confirmationNumber=ABC123"},
}
STALE_DAYOF = {
    "id": "old-dayof-id",
    "name": "dayof-WN200-20260717",
    # command payload (argv) — confirmation is an argv element, not in a message
    "payload": {"kind": "command", "argv": [
        "python3", "travel_reminder.py", "--flight", "WN 200",
        "--confirmation", "ABC123", "--origin", "DEN",
    ]},
}
STALE_FLIGHTLOG = {
    "id": "old-flightlog-id",
    "name": "flightlog-WN200-20260717",
    "payload": {"kind": "command", "argv": [
        "python3", "prompt_flight_log.py", "--flight", "WN 200",
        "--confirmation", "ABC123",
    ]},
}
CURRENT_CHECKIN = {
    "id": "new-checkin-id",
    "name": "checkin-WN100-20260721",
    "payload": {"message": "Check-in is open for WN 100 ... ABC123"},
}
UNRELATED_JOB = {
    "id": "unrelated-id",
    "name": "checkin-WN9999-20260801",
    "payload": {"message": "Check-in is open for WN 9999 ... DIFFERENTCONF"},
}
NON_REMINDER_JOB = {
    "id": "morning-briefing-id",
    "name": "morning-briefing-daily",
    "payload": {"message": "contains ABC123 by coincidence, should never be touched"},
}


class TestCancelStaleFlightReminders(unittest.TestCase):
    def test_no_confirmation_number_does_nothing(self):
        with patch.object(daemon.subprocess, "run") as mock_run:
            daemon._cancel_stale_flight_reminders("", keep_names={"checkin-WN100-20260721"})
        mock_run.assert_not_called()

    def test_cron_list_failure_does_not_raise(self):
        with patch.object(daemon.subprocess, "run", return_value=_completed(1, stderr="boom")):
            daemon._cancel_stale_flight_reminders("ABC123", keep_names=set())
        # no exception == pass

    def test_cancels_stale_checkin_and_dayof_matching_confirmation(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:3] == ["/opt/homebrew/bin/openclaw", "cron", "list"]:
                return _completed(0, stdout=_jobs_payload(STALE_CHECKIN, STALE_DAYOF, STALE_FLIGHTLOG, CURRENT_CHECKIN))
            if args[:3] == ["/opt/homebrew/bin/openclaw", "cron", "rm"]:
                return _completed(0)
            self.fail(f"unexpected command: {args}")

        with patch.object(daemon.subprocess, "run", side_effect=fake_run):
            daemon._cancel_stale_flight_reminders(
                "ABC123", keep_names={"checkin-WN100-20260721", "dayof-WN100-20260721"},
            )

        rm_ids = [c[3] for c in calls if c[:3] == ["/opt/homebrew/bin/openclaw", "cron", "rm"]]
        self.assertEqual(sorted(rm_ids), ["old-checkin-id", "old-dayof-id", "old-flightlog-id"])

    def test_never_cancels_jobs_in_keep_names(self):
        # Regression guard: the job(s) just created for the current booking
        # must never be immediately cancelled by their own cleanup pass.
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:3] == ["/opt/homebrew/bin/openclaw", "cron", "list"]:
                return _completed(0, stdout=_jobs_payload(CURRENT_CHECKIN))
            self.fail(f"rm should never be called for a kept job: {args}")

        with patch.object(daemon.subprocess, "run", side_effect=fake_run):
            daemon._cancel_stale_flight_reminders(
                "ABC123", keep_names={"checkin-WN100-20260721"},
            )

    def test_never_cancels_jobs_with_different_confirmation(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:3] == ["/opt/homebrew/bin/openclaw", "cron", "list"]:
                return _completed(0, stdout=_jobs_payload(UNRELATED_JOB))
            self.fail(f"rm should never be called for a different confirmation: {args}")

        with patch.object(daemon.subprocess, "run", side_effect=fake_run):
            daemon._cancel_stale_flight_reminders("ABC123", keep_names=set())

    def test_never_touches_non_reminder_jobs_even_on_confirmation_match(self):
        # Safety guard: only checkin-*/dayof-* named jobs are ever candidates,
        # regardless of what text happens to appear in an unrelated job's message.
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:3] == ["/opt/homebrew/bin/openclaw", "cron", "list"]:
                return _completed(0, stdout=_jobs_payload(NON_REMINDER_JOB))
            self.fail(f"rm should never be called for a non-reminder job: {args}")

        with patch.object(daemon.subprocess, "run", side_effect=fake_run):
            daemon._cancel_stale_flight_reminders("ABC123", keep_names=set())

    def test_rm_failure_for_one_job_does_not_block_others_or_raise(self):
        rm_calls = []

        def fake_run(args, **kwargs):
            if args[:3] == ["/opt/homebrew/bin/openclaw", "cron", "list"]:
                return _completed(0, stdout=_jobs_payload(STALE_CHECKIN, STALE_DAYOF))
            if args[:3] == ["/opt/homebrew/bin/openclaw", "cron", "rm"]:
                rm_calls.append(args[3])
                if args[3] == "old-checkin-id":
                    return _completed(1, stderr="not found")
                return _completed(0)
            self.fail(f"unexpected command: {args}")

        with patch.object(daemon.subprocess, "run", side_effect=fake_run):
            daemon._cancel_stale_flight_reminders("ABC123", keep_names=set())

        self.assertEqual(sorted(rm_calls), ["old-checkin-id", "old-dayof-id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
