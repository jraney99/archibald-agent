#!/usr/bin/env python3
"""A confirmation for something already on the calendar is not a question.

2026-10-13 (twice): The user booked a simulator bay through the golf skill, which put
"Golf <> the user @ Example Golf" on his calendar. The venue then emailed a
confirmation, the itinerary path read it as an event ticket, and he got
"🎟 Event ticket detected" for an event he had just booked — once at 23:29 and
again the next evening. Anything booked through his own skills arrives this way.

    python3 test_cal_duplicate_guard.py
"""
import importlib.util
import json
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


d = _load("daemon", _HERE / "daemon.py")

PROPOSED = [{
    "summary": "Bay 1 Golf Bay (Free Hour) <> the user @ Example Golf Club | Anytown",
    "from": "2026-10-14T11:30:00-05:00", "to": "2026-10-14T12:30:00-05:00",
    "location": "100 Example St, Anytown, ST 00000, USA",
}]


def _cal(events):
    """Patch gog so the check sees exactly these existing events."""
    out = json.dumps({"events": events})
    return mock.patch.object(d.subprocess, "run",
                             return_value=mock.Mock(returncode=0, stdout=out, stderr=""))


def _event(summary, start, location=""):
    return {"summary": summary, "location": location,
            "start": {"dateTime": start}, "end": {"dateTime": start}}


class DuplicateDetection(unittest.TestCase):
    def test_the_booking_he_already_made_is_recognised(self):
        with _cal([_event("Golf <> the user @ Example Golf", "2026-10-14T11:30:00-05:00",
                          "100 Example St, Anytown, ST 00000, USA")]):
            self.assertEqual(d.already_on_calendar(PROPOSED), "Golf <> the user @ Example Golf")

    def test_the_venue_may_come_from_the_summary_alone(self):
        with _cal([_event("Golf <> the user @ 100 Example St", "2026-10-14T11:30:00-05:00")]):
            self.assertTrue(d.already_on_calendar(PROPOSED))

    def test_an_empty_day_is_not_a_duplicate(self):
        with _cal([]):
            self.assertEqual(d.already_on_calendar(PROPOSED), "")

    def test_same_hour_different_place_still_reaches_owner(self):
        with _cal([_event("Dentist <> the user @ Midtown", "2026-10-14T11:30:00-05:00",
                          "999 Elsewhere Ave, Anytown, ST")]):
            self.assertEqual(d.already_on_calendar(PROPOSED), "")

    def test_same_place_different_hour_still_reaches_owner(self):
        with _cal([_event("Golf <> the user @ Example Golf", "2026-10-14T17:00:00-05:00",
                          "100 Example St, Anytown, ST 00000, USA")]):
            self.assertEqual(d.already_on_calendar(PROPOSED), "")

    def test_a_calendar_it_cannot_read_proposes_as_before(self):
        # Failing closed would silently swallow real tickets.
        with mock.patch.object(d.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="", stderr="boom")):
            self.assertEqual(d.already_on_calendar(PROPOSED), "")

    def test_a_crash_in_the_lookup_proposes_as_before(self):
        with mock.patch.object(d.subprocess, "run", side_effect=OSError("no gog")):
            self.assertEqual(d.already_on_calendar(PROPOSED), "")

    def test_an_event_with_no_start_is_skipped(self):
        with _cal([_event("Golf <> the user @ Example Golf", "2026-10-14T11:30:00-05:00",
                          "100 Example St")]):
            self.assertEqual(d.already_on_calendar([{"summary": "x", "location": "y"}]), "")


class ConfirmationNumberMatching(unittest.TestCase):
    """The only identity both sides agree on. Time and place is the fallback for
    the first booking at a venue; once a conf number is on the event, a vendor
    stating a different time can no longer slip past."""

    WITH_CONF = [dict(PROPOSED[0], description="Order: RES0000EXAMPLE")]

    def test_a_matching_conf_wins_even_at_a_different_time(self):
        with _cal([_event("Golf <> the user @ Example Golf", "2026-10-14T09:00:00-05:00",
                          "somewhere else entirely")
                   | {"description": "Confirmation: RES0000EXAMPLE"}]):
            self.assertEqual(d.already_on_calendar(self.WITH_CONF),
                             "Golf <> the user @ Example Golf")

    def test_a_different_conf_is_not_a_match(self):
        with _cal([_event("Golf <> the user @ Elsewhere", "2026-10-14T09:00:00-05:00",
                          "elsewhere") | {"description": "Confirmation: RESZZZZZZZZ"}]):
            self.assertEqual(d.already_on_calendar(self.WITH_CONF), "")

    def test_without_a_conf_it_falls_back_to_time_and_place(self):
        with _cal([_event("Golf <> the user @ Example Golf", "2026-10-14T11:30:00-05:00",
                          "100 Example St, Anytown, ST 00000, USA")]):
            self.assertTrue(d.already_on_calendar(PROPOSED))


class ThePublisherHonoursIt(unittest.TestCase):
    def test_a_duplicate_is_logged_and_never_proposed(self):
        said = []
        with mock.patch.object(d, "already_on_calendar", return_value="Golf <> the user @ Example Golf"), \
             mock.patch.object(d, "_tg_send_with_buttons") as send, \
             mock.patch.object(d, "CAL_FILE") as cal_file:
            d._publish_cal_proposal({"proposal_text": "🎟 Event ticket detected",
                                     "gog_events": PROPOSED}, said.append)
        send.assert_not_called()
        cal_file.write_text.assert_not_called()
        self.assertIn("already on the calendar", said[0])

    def test_a_genuine_ticket_still_gets_through(self):
        with mock.patch.object(d, "already_on_calendar", return_value=""), \
             mock.patch.object(d, "_cal_proposal_is_unusable", return_value=(True, "no date")):
            said = []
            d._publish_cal_proposal({"proposal_text": "t", "gog_events": PROPOSED}, said.append)
            # Reached the normal path rather than being swallowed by the new guard.
            self.assertIn("no date", said[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
