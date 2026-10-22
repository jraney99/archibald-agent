#!/usr/bin/env python3
"""
Regression tests for daemon.py's Gmail L1 sender/subject filtering.

Run manually after touching daemon.py's classification logic (_layer1,
_passes_filters, ITINERARY_SUBJECT_RE, AUTOSEND_RE, TeleVisit handling):

    python3 test_daemon_classification.py

No pytest dependency — plain unittest, stdlib only. Imports daemon.py by
file path since it isn't packaged; importing it must stay side-effect-free
(no network/file I/O at module level) for this to keep working.

Background: LRN-20260714-001 (bad gog CLI flags) and the no-reply/itinerary
ordering bug (fixed same day — see feedback-noreply-appointment-skip-bug in
memory) were both silent production failures with zero test coverage. This
file exists so the next daemon.py edit can't silently reintroduce either
failure mode without a test going red first.
"""
import os
import datetime as _dt
import importlib.util
import json
import re
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_DAEMON_PATH = Path(__file__).parent / "daemon.py"
_spec = importlib.util.spec_from_file_location("daemon", _DAEMON_PATH)
daemon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daemon)


def hdrs(from_addr: str, **extra) -> dict:
    """Build a lowercase header dict the way the Gmail loop does."""
    h = {"from": from_addr}
    h.update(extra)
    return h


class TestInactiveSenders(unittest.TestCase):
    def test_inactive_provider_matches_exact_address(self):
        self.assertTrue(daemon._is_inactive_sender(
            "Example Sleep Clinic <info@sleep-clinic.example>"))

    def test_unrelated_sender_is_not_inactive(self):
        self.assertFalse(daemon._is_inactive_sender("person@example.com"))


class TestLayer1NoReplyOverride(unittest.TestCase):
    """Regression: a clinic portal's real 'Appointment Reminder' was
    dropped at L1 before the itinerary check ever ran. _passes_filters must
    let a no-reply sender through when the subject plainly reads as a
    booking/appointment notice."""

    def test_noreply_appointment_reminder_passes(self):
        passes, reason = daemon._passes_filters(
            hdrs("Example Health Clinic <noreply@clinic-portal.example>"),
            "Appointment Reminder",
        )
        self.assertTrue(passes, f"expected override to pass, got skip reason: {reason}")

    def test_noreply_upcoming_appointment_passes(self):
        passes, _ = daemon._passes_filters(
            hdrs("Some Clinic <notifications@example-booking.com>"),
            "Your upcoming appointment tomorrow",
        )
        self.assertTrue(passes)

    def test_televisit_style_subject_passes(self):
        # TELEVISIT_SUBJECT_RE and ITINERARY_SUBJECT_RE overlap on this
        # phrasing; confirm the generic L1 override independently covers it.
        passes, _ = daemon._passes_filters(
            hdrs("Coach Office <noreply@televisit.example>"),
            "Video appointment reminder",
        )
        self.assertTrue(passes)

    def test_noreply_non_itinerary_subject_still_skipped(self):
        # The override is narrow: a no-reply sender with a subject that does
        # NOT look like a booking/appointment must still be skipped.
        passes, reason = daemon._passes_filters(
            hdrs("Some List <noreply@marketing-blast.com>"),
            "Weekly newsletter roundup",
        )
        self.assertFalse(passes)
        self.assertTrue(reason.startswith("no-reply:"))

    def test_bulk_header_still_hard_skips_even_with_appointment_subject(self):
        # Stronger true-bulk-mail signals must never be overridden by subject
        # content, even if worded like an appointment.
        passes, reason = daemon._passes_filters(
            hdrs("noreply@example.com", **{"list-unsubscribe": "<mailto:x@example.com>"}),
            "Appointment Reminder",
        )
        self.assertFalse(passes)
        self.assertEqual(reason, "List-Unsubscribe")

    def test_precedence_bulk_still_hard_skips(self):
        passes, reason = daemon._passes_filters(
            hdrs("noreply@example.com", precedence="bulk"),
            "Appointment Reminder",
        )
        self.assertFalse(passes)
        self.assertEqual(reason, "Precedence:bulk")

    def test_auto_submitted_still_hard_skips(self):
        passes, reason = daemon._passes_filters(
            hdrs("noreply@example.com", **{"auto-submitted": "auto-replied"}),
            "Appointment Reminder",
        )
        self.assertFalse(passes)
        self.assertTrue(reason.startswith("Auto-Submitted:"))


class TestLayer1Baseline(unittest.TestCase):
    """Sanity checks that ordinary mail is unaffected by the override."""

    def test_normal_sender_normal_subject_passes(self):
        passes, reason = daemon._passes_filters(
            hdrs("Alex Example <alex@realdomain.example>"),
            "Let's meet at the beach",
        )
        self.assertTrue(passes)
        self.assertEqual(reason, "ok")

    def test_noreply_sender_no_subject_skipped(self):
        passes, reason = daemon._passes_filters(
            hdrs("noreply@somesite.com"),
            "(no subject)",
        )
        self.assertFalse(passes)
        self.assertTrue(reason.startswith("no-reply:"))


class TestItinerarySubjectRegex(unittest.TestCase):
    """Guard the specific phrasing that matters for the no-reply override."""

    def test_matches(self):
        for subj in [
            "Appointment Reminder",
            "Your upcoming appointment",
            "Video appointment reminder",
            "Appointment confirmation",
            "Flight itinerary",
            "You're going to Denver",
            "Booking confirmation (ABC123)",
        ]:
            with self.subTest(subj=subj):
                self.assertTrue(daemon.ITINERARY_SUBJECT_RE.search(subj), subj)

    def test_non_matches(self):
        for subj in [
            "Weekly newsletter roundup",
            "Your invoice is ready",
            "50% off summer sale",
        ]:
            with self.subTest(subj=subj):
                self.assertIsNone(daemon.ITINERARY_SUBJECT_RE.search(subj), subj)


class TestEventDurationFallback(unittest.TestCase):
    """Regression: a Dr. Example "Appointment Reminder" email (no end time) built a
    3-hour calendar event, duplicating the 30-min event the confirmation email
    had already made. A recurring-appointment event with no end time now
    defaults to 1h, not 3h."""

    def _end(self, extracted):
        p = daemon._build_cal_proposal(extracted)
        ev = p["gog_events"][0]
        from datetime import datetime
        return datetime.fromisoformat(ev["to"]) - datetime.fromisoformat(ev["from"])

    def test_recurring_appointment_no_end_defaults_1h(self):
        from datetime import timedelta
        d = self._end({
            "type": "event", "event_name": "Doctor appointment",
            "venue_name": "Dr. Example", "address": None,
            "datetime_iso": "2026-09-10T12:00:00-05:00", "end_datetime_iso": None,
            "timezone": "America/Chicago", "is_recurring_appointment": True,
        })
        self.assertEqual(d, timedelta(hours=1))

    def test_one_off_event_no_end_keeps_3h(self):
        from datetime import timedelta
        d = self._end({
            "type": "event", "event_name": "Concert", "venue_name": "The Venue",
            "address": "1 Music Row",
            "datetime_iso": "2026-09-10T20:00:00-05:00", "end_datetime_iso": None,
            "timezone": "America/Chicago", "is_recurring_appointment": False,
        })
        self.assertEqual(d, timedelta(hours=3))

    def test_explicit_end_time_wins(self):
        from datetime import timedelta
        d = self._end({
            "type": "event", "event_name": "Doctor appointment", "venue_name": "Dr. Example",
            "address": None, "datetime_iso": "2026-09-10T12:00:00-05:00",
            "end_datetime_iso": "2026-09-10T12:30:00-05:00",
            "timezone": "America/Chicago", "is_recurring_appointment": True,
        })
        self.assertEqual(d, timedelta(minutes=30))

    def test_event_ticket_displays_in_owners_current_timezone(self):
        original = daemon.get_tz
        daemon.get_tz = lambda: daemon.ZoneInfo("America/Chicago")
        try:
            proposal = daemon._build_cal_proposal({
                "type": "event",
                "event_name": "Planning and Strategy Webinar",
                "venue_name": "Zoom (Example Investment Group)",
                "address": "https://zoom.us/j/0000000000",
                "datetime_iso": "2026-09-15T13:30:00-06:00",
                "end_datetime_iso": "2026-09-15T14:15:00-06:00",
                "timezone": "America/Denver",
            })
        finally:
            daemon.get_tz = original
        self.assertIn("Sep 15 at 2:30 PM CDT", proposal["proposal_text"])


class TestFlightCompletenessGate(unittest.TestCase):
    def test_partial_southwest_reminder_is_rejected(self):
        proposal = daemon._build_cal_proposal({
            "type": "flight", "airline": None, "flight_number": None,
            "origin_code": None, "dest_code": "LGA", "origin_name": None,
            "dest_name": "LaGuardia Airport", "departure_iso": None,
            "arrival_iso": None, "confirmation": "XYZ789",
            "origin_tz": None, "dest_tz": "America/New_York",
        })
        self.assertIsNone(proposal)

    def test_complete_flight_is_accepted(self):
        proposal = daemon._build_cal_proposal({
            "type": "flight", "airline": "Southwest Airlines",
            "flight_number": "WN 123", "origin_code": "DAL",
            "dest_code": "LGA", "origin_name": "Dallas Love Field",
            "dest_name": "LaGuardia Airport",
            "departure_iso": "2026-10-01T08:00:00-05:00",
            "arrival_iso": "2026-10-01T12:00:00-04:00",
            "confirmation": "ABC123", "origin_tz": "America/Chicago",
            "dest_tz": "America/New_York",
        })
        self.assertEqual(proposal["gog_events"][0]["_flight_number"], "WN 123")


class TestItineraryCalendarDedup(unittest.TestCase):
    """A reminder must not propose an event already present under equivalent text."""

    def setUp(self):
        self.original_run = daemon.subprocess.run
        self.commands = []

    def tearDown(self):
        daemon.subprocess.run = self.original_run

    def _proposal(self):
        return {"gog_events": [{
            "summary": "Planning and Strategy Webinar <> the user @ Zoom (Example Investment Group)",
            "from": "2026-09-15T13:30:00-06:00",
        }]}

    def _calendar_returns(self, summaries):
        def fake_run(command, **kwargs):
            self.commands.append(command)
            return daemon.subprocess.CompletedProcess(
                command, 0, stdout=daemon.json.dumps({
                    "events": [{"summary": summary} for summary in summaries]
                }), stderr="")
        daemon.subprocess.run = fake_run

    def test_matches_ampersand_and_parenthetical_venue_drift(self):
        self._calendar_returns([
            "Planning & Strategy Webinar <> the user @ Zoom"
        ])
        self.assertTrue(daemon._proposal_already_on_calendar(self._proposal()))
        self.assertIn("--all", self.commands[0])

    def test_does_not_match_an_unrelated_zoom_event(self):
        self._calendar_returns(["Quarterly Team Meeting <> the user @ Zoom"])
        self.assertFalse(daemon._proposal_already_on_calendar(self._proposal()))

    def test_matches_when_practitioner_and_clinic_are_mirrored(self):
        """2026-09-23: the booked event carried the practitioner as its venue and
        the proposal carried her inside its event name, so neither field lined up
        and a duplicate Dr. Example appointment was proposed."""
        proposal = {"gog_events": [{
            "summary": ("Doctor appointment with Dr. Example Provider <> the user @ "
                        "Northside Wellness Clinic"),
            "from": "2026-09-24T11:00:00-05:00",
        }]}
        self._calendar_returns(["Phone call appointment <> the user @ Dr. Example Provider"])
        self.assertTrue(daemon._proposal_already_on_calendar(proposal))

    def test_matches_when_only_the_appointment_wording_differs(self):
        """2026-10-06: the booked event said "Medical appointment with Dr. Example Provider"
        and the reminder proposed "Doctor appointment with Dr. Example Provider" — same clinic,
        same 12:30 slot, one word apart. Neither name contained the other, so the substring
        rule missed it and the user was offered an event he already had."""
        proposal = {"gog_events": [{
            "summary": ("Doctor appointment with Dr. Example Provider <> the user @ "
                        "Northside Wellness Clinic"),
            "from": "2026-10-07T12:30:00-05:00",
        }]}
        self._calendar_returns(["Medical appointment with Dr. Example Provider <> the user @ "
                                "Northside Wellness Clinic"])
        self.assertTrue(daemon._proposal_already_on_calendar(proposal))

    def test_two_providers_at_one_clinic_do_not_collapse(self):
        """The guard on the above: dropping the appointment wording must not make every
        booking at a shared clinic look like the same visit."""
        venue = "Northside Wellness Clinic"
        self.assertFalse(daemon._same_calendar_event(
            f"Doctor appointment with Dr. Example Provider <> the user @ {venue}",
            f"Doctor appointment with Dr. Other Provider <> the user @ {venue}"))

    def test_the_same_practitioner_at_a_different_venue_is_a_different_event(self):
        self.assertFalse(daemon._same_calendar_event(
            "Doctor appointment with Dr. Example Provider <> the user @ Northside Wellness Clinic",
            "Medical appointment with Dr. Example Provider <> the user @ Southside General Hospital"))

    def test_shared_generic_phrase_does_not_collapse_two_providers(self):
        """"Phone call appointment" is not evidence on its own — two different
        practitioners an hour apart must both survive."""
        self.assertFalse(daemon._same_calendar_event(
            "Phone call appointment <> the user @ Dr. Example Provider",
            "Phone call appointment <> the user @ Dr. Someone Else"))

    def test_in_person_and_video_coaching_stay_separate(self):
        """Coach in-person at a park and the TeleVisit video session are
        genuinely different events and must not suppress each other."""
        self.assertFalse(daemon._same_calendar_event(
            "Coaching with Coach <> the user @ TeleVisit",
            "Coach <> the user @ City Park"))


class TestTeleVisitLinkRegex(unittest.TestCase):
    """Regression: TeleVisit moved their video link from a path
    (televisit.example/video/...) to a subdomain (video.televisit.example/...)
    on 2026-07-26; _SP_LINK_RE silently stopped matching and coaching calendar
    events were created with no join link in the description."""

    def test_new_subdomain_format(self):
        body = (
            "Your unique video appointment link: Join your Video Appointment \n"
            "( https://video.televisit.example/appt-0123456789abcdef0123456789abcdef?origin=client )\n"
        )
        m = daemon._SP_LINK_RE.search(body)
        self.assertIsNotNone(m)
        self.assertEqual(
            m.group(0),
            "https://video.televisit.example/appt-0123456789abcdef0123456789abcdef?origin=client",
        )

    def test_old_path_format_still_matches(self):
        body = "Join here: https://sub.televisit.example/video/abc123"
        m = daemon._SP_LINK_RE.search(body)
        self.assertIsNotNone(m)

    def test_zoom_subdomain_matches(self):
        body = "Join Zoom Meeting https://us02web.zoom.us/j/88888888?pwd=xyz"
        m = daemon._SP_LINK_RE.search(body)
        self.assertIsNotNone(m)

    def test_ignores_unrelated_televisit_links(self):
        body = (
            "iCloud ( https://coach-example.clientsecure.me/client-portal-api/x.ics )\n"
            "Unsubscribe ( https://secure.televisit.example/unsub/u/abc )\n"
        )
        m = daemon._SP_LINK_RE.search(body)
        self.assertIsNone(m)


class TestVipSenders(unittest.TestCase):
    """VIP senders (e.g. a family member) must bypass the L1 bulk gate and can never
    be silently dropped by an L2 'skip'. Regression: a short 1:1 note from
    family.member@example.com forwarding an article was L2-classified 'bulk' and
    never reached Telegram (2026-08-27)."""

    def test_family_personal_address_is_vip(self):
        self.assertTrue(daemon._is_vip("Family gmail <family.member@example.com>"))

    def test_second_seed_address_is_vip(self):
        self.assertTrue(daemon._is_vip('"Advisor, Example" <advisor@advisor.example>'))

    def test_case_insensitive(self):
        self.assertTrue(daemon._is_vip("<Family.Member@EXAMPLE.com>"))

    def test_stranger_is_not_vip(self):
        self.assertFalse(daemon._is_vip("Random Person <random@example.com>"))

    def test_domain_suffix_entry_matches(self):
        daemon.VIP_SENDERS.add("@familydomain.com")
        try:
            self.assertTrue(daemon._is_vip("Mom <mom@familydomain.com>"))
            self.assertFalse(daemon._is_vip("spammer@notfamilydomain.com"))
        finally:
            daemon.VIP_SENDERS.discard("@familydomain.com")

    def test_seed_survives_missing_json(self):
        # _load_vips must still return the hardcoded seed if the file is absent.
        self.assertIn("family.member@example.com", daemon._load_vips())


class TestOpenTableConf(unittest.TestCase):
    """The reservations catch-up scan must not re-propose a reservation that
    was cancelled inside its own 2-day window. Regression: two restaurant bookings
    made and cancelled during a restaurant-booking skill test (confs 10001 /
    10002) were re-proposed as live reservations after a daemon restart, because
    the scan only looked at confirmation emails and never at the matching
    cancellations (2026-08-28)."""

    def test_extracts_confirmation_number(self):
        for body in (
            "Your table is booked!\nConfirmation number 10001\nExample Bistro, Aug 28",
            "Confirmation #: 10002",
            "Reservation confirmation\nConfirmation code 100003 for 2 guests",
        ):
            self.assertRegex(daemon._opentable_conf(body) or "", r"^\d{4,7}$")

    def test_matches_confirmation_to_cancellation(self):
        confirm = "You're confirmed at Example Bistro\nConfirmation number 10001"
        cancel  = "Your reservation has been cancelled\nConfirmation number 10001"
        self.assertEqual(daemon._opentable_conf(confirm), daemon._opentable_conf(cancel))

    def test_returns_none_when_absent(self):
        self.assertIsNone(daemon._opentable_conf("Rate your recent visit to Example Bistro"))
        self.assertIsNone(daemon._opentable_conf(""))


class TestItineraryCancellationGuard(unittest.TestCase):
    """Regression: a hotel chain's 'Reservation Cancellation #40000001 for Example
    Suites' email carried the full property/dates/confirmation, so the travel-
    label catch-up scan tagged it is_itinerary=hotel and fired a '🏨 Hotel
    booking detected' proposal for a stay the user had just cancelled (2026-09-05).
    ITINERARY_CANCEL_RE must catch cancellation wording without tripping on the
    cancellation-policy boilerplate every live confirmation contains."""

    def test_cancel_subjects_match(self):
        for subj in [
            "Reservation Cancellation #40000001 for Example Suites",
            "Your reservation has been cancelled",
            "Cancellation Confirmation - Hotel Chain",
            "Flight cancelled: WN 100 DAL-DEN",
            "We've cancelled your booking",
            "Cancellation of your upcoming stay",
        ]:
            with self.subTest(subj=subj):
                self.assertTrue(daemon.ITINERARY_CANCEL_RE.search(subj), subj)

    def test_live_confirmations_do_not_match(self):
        for text in [
            "Reservation Confirmation #70000001 for Example Suites",
            "You're confirmed at Example Bistro",
            "To cancel your reservation, visit the link below",
            "Free cancellation until 24 hours before check-in",
            "Cancellation policy: a fee applies for changes",
        ]:
            with self.subTest(text=text):
                self.assertIsNone(daemon.ITINERARY_CANCEL_RE.search(text), text)

    def test_cancellation_confs_collects_body_and_extracted(self):
        confs = daemon._cancellation_confs(
            "Reservation Cancellation #40000001 for Example Suites",
            "This confirms the cancellation of confirmation number 70000001 at Example Suites.",
            {"type": "hotel", "confirmation": "70000001"},
        )
        self.assertIn("70000001", confs)
        self.assertIn("40000001", confs)

    def test_cancellation_confs_drops_short_and_alpha_noise(self):
        confs = daemon._cancellation_confs(
            "Your stay is cancelled",
            "Questions? Call us. Ref #12 and code CANCELLED apply.",
            {},
        )
        self.assertEqual(confs, set())


class TestItineraryFreshnessGuard(unittest.TestCase):
    """Regression: a hotel chain re-sent an Aug 1-2 hotel confirmation on
    Aug 30; the travel-label catch-up scan saw a new unprocessed itinerary
    email and proposed check-in/check-out calendar events a month in the past.
    _itinerary_latest_dt must report the last relevant instant so
    _handle_itinerary can skip a proposal for travel that already happened
    (2026-08-30)."""

    from datetime import datetime, timedelta, timezone

    def _rel(self, days):
        return (self.datetime.now(self.timezone.utc) + self.timedelta(days=days))

    def test_hotel_uses_checkout(self):
        past = self._rel(-20)
        fut  = self._rel(-19)
        it = {"type": "hotel", "checkin_date": past.strftime("%Y-%m-%d"),
              "checkout_date": fut.strftime("%Y-%m-%d"), "timezone": "America/Chicago"}
        latest = daemon._itinerary_latest_dt(it)
        self.assertEqual(latest.date().isoformat(), fut.strftime("%Y-%m-%d"))

    def test_past_hotel_is_stale(self):
        d = self._rel(-25).strftime("%Y-%m-%d")
        it = {"type": "hotel", "checkin_date": d, "checkout_date": d,
              "checkin_time": "15:00", "checkout_time": "11:00",
              "timezone": "America/Chicago"}
        latest = daemon._itinerary_latest_dt(it)
        self.assertLess(latest, self.datetime.now(latest.tzinfo) - self.timedelta(hours=12))

    def test_future_flight_is_fresh(self):
        dep = self._rel(3).replace(microsecond=0).isoformat()
        arr = self._rel(3).replace(microsecond=0).isoformat()
        latest = daemon._itinerary_latest_dt({"type": "flight", "departure_iso": dep,
                                              "arrival_iso": arr})
        self.assertGreater(latest, self.datetime.now(latest.tzinfo))

    def test_round_trip_uses_last_leg(self):
        legs = [
            {"type": "flight", "departure_iso": self._rel(2).replace(microsecond=0).isoformat(),
             "arrival_iso": self._rel(2).replace(microsecond=0).isoformat()},
            {"type": "flight", "departure_iso": self._rel(9).replace(microsecond=0).isoformat(),
             "arrival_iso": self._rel(9).replace(microsecond=0).isoformat()},
        ]
        latest = daemon._itinerary_latest_dt(legs)
        self.assertGreater(latest, self.datetime.now(latest.tzinfo) + self.timedelta(days=8))

    def test_naive_datetime_is_handled(self):
        # restaurant/event times can arrive without an offset
        latest = daemon._itinerary_latest_dt(
            {"type": "restaurant", "datetime_iso": "2026-08-01T19:00:00"})
        self.assertIsNotNone(latest)
        self.assertIsNotNone(latest.tzinfo)

    def test_unparseable_returns_none(self):
        self.assertIsNone(daemon._itinerary_latest_dt({"type": "hotel"}))
        self.assertIsNone(daemon._itinerary_latest_dt({"type": "flight", "departure_iso": ""}))


class TestReceivedDt(unittest.TestCase):
    """Regression: an investment-firm webinar confirmation (2026-08-31)
    printed 'Tuesday, September 15' with no year; Sonnet resolved it to
    2020-09-15 and _handle_itinerary's freshness guard silently dropped the
    calendar proposal. _extract_itinerary now anchors year inference to the
    email's receive time, which _received_dt derives from Gmail's internalDate."""

    from datetime import datetime, timezone

    def test_parses_internal_date_ms(self):
        # 2026-08-31 15:32 UTC in epoch ms
        ms = int(self.datetime(2026, 8, 31, 15, 32, tzinfo=self.timezone.utc).timestamp() * 1000)
        got = daemon._received_dt({"internalDate": str(ms)})
        self.assertEqual(got.year, 2026)
        self.assertEqual(got.month, 8)
        self.assertEqual(got.day, 31)
        self.assertIsNotNone(got.tzinfo)

    def test_missing_internal_date_falls_back_to_now(self):
        got = daemon._received_dt({})
        self.assertIsNotNone(got.tzinfo)
        self.assertLess(abs((got - daemon.datetime.now(daemon.CT)).total_seconds()), 5)

    def test_garbage_internal_date_falls_back(self):
        got = daemon._received_dt({"internalDate": "not-a-number"})
        self.assertIsNotNone(got.tzinfo)


class TestCalProposalStaleness(unittest.TestCase):
    """Regression: a hotel booking proposal the user ignored (then the booking was
    cancelled with an OCR-mangled confirmation number) wedged the single-slot
    calendar-proposal queue — an Coach coaching session sat behind it with
    no buttons and never got added. The queue now drains without a tap."""

    import json as _json
    import tempfile
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    def _prop(self, first_line, *, days_from_now=None, created_days_ago=0):
        ev = []
        if days_from_now is not None:
            when = (self.datetime.now(self.timezone.utc)
                    + self.timedelta(days=days_from_now)).replace(microsecond=0)
            ev = [{"summary": first_line, "from": when.isoformat(),
                   "to": (when + self.timedelta(minutes=50)).isoformat()}]
        return {
            "proposal_text": f"{first_line}\n\ndetail line",
            "gog_events": ev,
            "created_epoch": (self.datetime.now(self.timezone.utc)
                              - self.timedelta(days=created_days_ago)).timestamp(),
        }

    def setUp(self):
        self._tmp = self.tempfile.mkdtemp()
        self.cal = self.Path(self._tmp) / "pending_cal_event.json"
        self.q   = self.Path(self._tmp) / "pending_cal_queue.json"
        self._sent = []
        self._orig = (daemon._tg_send, daemon._tg_send_with_buttons)
        daemon._tg_send = lambda *a, **k: self._sent.append(("send", a, k)) or 1
        daemon._tg_send_with_buttons = lambda *a, **k: self._sent.append(("btn", a, k)) or 999

    def tearDown(self):
        daemon._tg_send, daemon._tg_send_with_buttons = self._orig

    def test_latest_dt_prefers_end(self):
        p = self._prop("x", days_from_now=3)
        self.assertGreater(daemon._cal_proposal_latest_dt(p),
                           self.datetime.now(self.timezone.utc))

    def test_past_event_is_stale(self):
        stale, why = daemon._cal_proposal_is_stale(self._prop("x", days_from_now=-1))
        self.assertTrue(stale)
        self.assertIn("passed", why)

    def test_future_event_is_fresh(self):
        stale, _ = daemon._cal_proposal_is_stale(self._prop("x", days_from_now=2))
        self.assertFalse(stale)

    def test_old_undated_proposal_is_stale(self):
        stale, why = daemon._cal_proposal_is_stale(self._prop("x", created_days_ago=20))
        self.assertTrue(stale)
        self.assertIn("unconfirmed", why)

    def test_a_proposal_with_no_date_is_unusable(self):
        """2026-09-24 (an exhibition ticket): the ticket email carried no date, so the
        proposal read '@ None / None' and its ✅ button could only crash."""
        unusable, why = daemon._cal_proposal_is_unusable({"gog_events": [
            {"summary": "Art Exhibition <> the user @ None", "from": None, "to": None}]})
        self.assertTrue(unusable)
        self.assertIn("from", why)

    def test_a_complete_proposal_is_usable(self):
        unusable, _ = daemon._cal_proposal_is_unusable({"gog_events": [
            {"summary": "Dinner <> the user @ Example Bistro", "from": "2026-10-01T18:00:00-05:00",
             "to": "2026-10-01T20:00:00-05:00"}]})
        self.assertFalse(unusable)

    def test_cancellations_and_invites_need_no_start_time(self):
        """Delete proposals carry {calendar_id, event_id, summary} and ICS
        invites are accepted rather than created — neither has from/to."""
        for proposal in ({"action": "delete", "gog_events": [
                             {"calendar_id": "primary", "event_id": "abc", "summary": "Flight"}]},
                         {"type": "ics_invite", "gog_events": [{"summary": "Standup"}]}):
            with self.subTest(proposal=proposal):
                self.assertFalse(daemon._cal_proposal_is_unusable(proposal)[0])

    def test_an_unusable_proposal_is_never_published(self):
        """It must not reach a Telegram button at all — that is what armed the
        crash. Silent by design: an extraction failure is not the user's to action."""
        sent = []
        original = daemon._tg_send_with_buttons
        daemon._tg_send_with_buttons = lambda *a, **k: sent.append(a) or 999
        logged = []
        try:
            daemon._publish_cal_proposal(
                {"proposal_text": "x", "gog_events": [{"summary": "s", "from": None, "to": None}]},
                logged.append)
        finally:
            daemon._tg_send_with_buttons = original
        self.assertEqual(sent, [])
        self.assertIn("no usable date", logged[0])

    def test_sweep_promotes_next_live_when_head_is_stale(self):
        self.cal.write_text(self._json.dumps(self._prop("stale hotel", days_from_now=-2)))
        self.q.write_text(self._json.dumps([self._prop("Coach coaching", days_from_now=1)]))
        n = daemon._sweep_stale_cal_proposals(cal_file=self.cal, queue_file=self.q)
        self.assertEqual(n, 1)
        promoted = self._json.loads(self.cal.read_text())
        self.assertIn("Coach", promoted["proposal_text"])
        self.assertFalse(self.q.exists())
        self.assertTrue(any(k == "btn" for k, *_ in self._sent))  # fresh buttons sent

    def test_sweep_noop_when_head_is_fresh(self):
        self.cal.write_text(self._json.dumps(self._prop("live hotel", days_from_now=5)))
        n = daemon._sweep_stale_cal_proposals(cal_file=self.cal, queue_file=self.q)
        self.assertEqual(n, 0)
        self.assertEqual(self._sent, [])

    def test_cancellation_matches_on_confirmation_number(self):
        p = self._prop("Hotel booking detected", days_from_now=3)
        p["proposal_text"] += "\nConfirmation: 70000001"
        self.assertTrue(daemon._cancellation_matches_proposal(p, {"70000001"}, {}))

    def test_cancellation_matches_hotel_on_name_and_window_despite_bad_conf(self):
        when = (self.datetime.now(self.timezone.utc) + self.timedelta(days=3)).replace(microsecond=0)
        p = {
            "proposal_text": "🏨 Hotel booking detected\n\nExample Suites Downtown\nConfirmation: 70000001",
            "gog_events": [{"summary": "Check-in <> the user @ Example Suites Downtown",
                            "from": when.isoformat(), "to": when.isoformat()}],
        }
        hotel = {"property_name": "Example Suites Downtown",
                 "checkin_date": when.strftime("%Y-%m-%d"),
                 "checkout_date": (when + self.timedelta(days=1)).strftime("%Y-%m-%d")}
        # cancellation carries a mangled number — conf set won't match, name+window must
        self.assertTrue(daemon._cancellation_matches_proposal(p, {"40000001"}, hotel))

    def test_cancellation_ignores_unrelated_proposal(self):
        p = self._prop("Coaching with Coach", days_from_now=3)
        self.assertFalse(daemon._cancellation_matches_proposal(p, {"40000001"}, {}))

    def test_clear_cancelled_unwedges_queue(self):
        when = (self.datetime.now(self.timezone.utc) + self.timedelta(days=3)).replace(microsecond=0)
        hotel_prop = {
            "proposal_text": "🏨 Hotel booking detected\n\nExample Suites Downtown",
            "gog_events": [{"summary": "Check-in <> the user @ Example Suites Downtown",
                            "from": when.isoformat(), "to": when.isoformat()}],
        }
        self.cal.write_text(self._json.dumps(hotel_prop))
        self.q.write_text(self._json.dumps([self._prop("Coaching with Coach", days_from_now=1)]))
        hotel = {"property_name": "Example Suites Downtown",
                 "checkin_date": when.strftime("%Y-%m-%d"),
                 "checkout_date": when.strftime("%Y-%m-%d")}
        n = daemon._clear_cancelled_cal_proposals({"40000001"}, hotel,
                                                  cal_file=self.cal, queue_file=self.q)
        self.assertEqual(n, 1)
        self.assertIn("Coach", self._json.loads(self.cal.read_text())["proposal_text"])


class TestLabeledOutBackstop(unittest.TestCase):
    """The generic backstop for skip-inbox mail (_labeled_out_backstop). A
    fraud/security-alert subject must be recognised so an L2 'skip' verdict on
    routed financial mail is floored up to 'fyi' rather than silently dropped.
    Senders that already have a dedicated catchup must be excluded."""

    def _sec(self, subj):
        return bool(daemon._SECURITY_SUBJECT_RE.search(subj))

    def test_fraud_alert_subjects_match(self):
        for subj in (
            "We noticed unusual sign-in activity on your account",
            "Did you make this purchase?",
            "Suspicious transaction on your card",
            "Your payment failed",
            "Was this you?",
            "Please verify this charge",
            "Your account was locked",
            "Unauthorized login attempt detected",
            "Card D Fraud Alert: Did you make a transaction for $412.00?",
            "We detected unusual activity on your card",
        ):
            self.assertTrue(self._sec(subj), f"expected security match: {subj!r}")

    def test_routine_subjects_do_not_match(self):
        # Routine financial confirmations remain quiet.
        for subj in (
            "Your September statement is ready",
            "Your monthly rewards summary",
            "Payment received — thank you",
            "Your receipt from Uber",
            "Statement available for account ending 1234",
            "You've successfully set up automatic payments",
            "Paperless confirmation: Notices and Letters",
        ):
            self.assertFalse(self._sec(subj), f"unexpected security match: {subj!r}")

    def test_account_access_changes_match(self):
        for subj in (
            "Card D security alert: You signed in with a new device",
            "Card D security alert: You changed your password",
            "A new device wants to sign in to your Exchange A account",
            "A new device is using your account",
            "Your MFA was disabled",
            "Account recovery requested",
        ):
            self.assertTrue(self._sec(subj), f"expected account-security match: {subj!r}")

    def test_daemon_uses_the_shared_policy_objects(self):
        # Drift guard: the audit's coverage model reads these same objects.
        import email_policy
        self.assertIs(daemon.ITINERARY_SUBJECT_RE, email_policy.ITINERARY_SUBJECT_RE)
        self.assertIs(daemon.ITINERARY_CANCEL_RE, email_policy.ITINERARY_CANCEL_RE)
        self.assertIs(daemon._TRAVEL_LABELS, email_policy.TRAVEL_LABELS)
        self.assertIs(daemon._SECURITY_SUBJECT_RE, email_policy.SECURITY_SUBJECT_RE)
        self.assertFalse(hasattr(daemon, "_BACKSTOP_SKIP_SENDER_RE"))

    def test_material_account_updates_match(self):
        for subj in (
            "Funds available for investing",
            "Your trade confirmation",
            "Your quarterly account statement is ready",
            "Dividend distribution received",
            "Your account has been linked",
        ):
            self.assertTrue(daemon._MATERIAL_ACCOUNT_SUBJECT_RE.search(subj), subj)

    def test_surveys_and_promotions_are_not_material_updates(self):
        for subj in (
            "Reminder: How was your experience?",
            "Get 50% off your next two rides",
            "Informed investing, now available",
        ):
            self.assertIsNone(daemon._MATERIAL_ACCOUNT_SUBJECT_RE.search(subj), subj)

    def test_known_finance_sender_detection(self):
        self.assertTrue(daemon._known_finance_sender(
            "Broker C <noreply@brokerc.example>", "Funds available for investing"))
        self.assertFalse(daemon._known_finance_sender(
            "Clinic <noreply@televisit.example>", "Appointment reminder"))

    def test_routine_receipt_is_not_a_material_update(self):
        """Exchange A's recurring-buy receipts reached the user's 6pm digest three times a day for
        two weeks: `routine_re` closed the finance-sender override on 2026-09-21,
        but "You bought BTC" matches MATERIAL_SUBJECT_RE and kept overriding as a
        material update instead. Both doors must honour routine_re."""
        exchange = "Exchange A <noreply@exchangea.example>"
        for coin in ("BTC", "ETH", "SOL"):
            subject = f"You bought {coin}"
            # it still LOOKS like money movement — that is why it got through
            self.assertEqual(daemon._policy.classify_subject(subject), "material")
            self.assertTrue(daemon._is_routine_subject(exchange, subject), subject)
            self.assertFalse(daemon._known_finance_sender(exchange, subject), subject)

    def test_bank_a_daily_balance_alert_is_routine(self):
        """The $1,000,000 threshold is deliberate: The user set the alert above his
        balance on 2026-09-29 so it fires every morning and
        the finance skill's balance watcher can parse his cash figure out of it. The
        mail must keep ARRIVING (the watcher queries Gmail by from+subject, not
        by folder) — he just should not be shown it daily."""
        wf = "Bank A <alerts@notify.banka.example>"
        # 2026-10-06: the daily account-update rundown lists scheduled activity he
        # already knows about, and the agent reads it for itself. Same deal as the
        # balance alert: keep arriving, keep being parsed, stop being shown.
        # A standing recurring payment mails him twice: a heads-up two days out and
        # a confirmation when it goes. He kept the confirmation and silenced the
        # heads-up, so the cadence word is wildcarded but "payment is coming up" is not.
        for subject in ("Your balance is below $1,000,000.00", "Your account update is here",
                        "Reminder: Your Every other week Zelle(R) payment is coming up",
                        "Reminder: Your Monthly Zelle(R) payment is coming up"):
            self.assertTrue(daemon._is_routine_subject(wf, subject), subject)
            self.assertFalse(daemon._known_finance_sender(wf, subject), subject)
        # A real event from the same sender is unaffected.
        for subject in ("You asked us to share your data with Plaid",
                        "Your Broker A statement is ready",
                        "You sent money with Zelle",
                        "You received money with Zelle",
                        "You've scheduled a Zelle(R) payment",
                        "We've got your mobile deposit",
                        "Your automatic payment is coming up"):
            self.assertFalse(daemon._is_routine_subject(wf, subject), subject)
            self.assertTrue(daemon._known_finance_sender(wf, subject), subject)

    def test_the_balance_watchers_query_still_matches_the_routine_subject(self):
        """routine_re silences the notification; it must not stop the watcher
        finding the mail. Both look at the same subject text."""
        import re
        subject = "Your balance is below $1,000,000.00"
        self.assertIn("Your balance is below", subject)        # the watcher's Gmail query
        self.assertTrue(re.search("^Your (available )?balance is below", subject))

    def test_security_mail_from_a_routine_sender_stays_loud(self):
        exchange = "Exchange A <noreply@exchangea.example>"
        for subject in ("Confirm your new withdrawal address",
                        "BTC withdrawal initiated"):
            self.assertFalse(daemon._is_routine_subject(exchange, subject), subject)
            self.assertEqual(daemon._policy.classify_subject(subject), "security")

    def test_non_routine_mail_from_the_same_sender_still_surfaces(self):
        exchange = "Exchange A <noreply@exchangea.example>"
        self.assertFalse(daemon._is_routine_subject(exchange, "Your 2026 tax documents are ready"))
        self.assertTrue(daemon._known_finance_sender(exchange, "Your 2026 tax documents are ready"))

    def test_routine_re_is_not_applied_to_senders_without_one(self):
        self.assertFalse(daemon._is_routine_subject(
            "Broker C <noreply@brokerc.example>", "You bought AAPL"))

    def test_classifier_requires_direct_relevance(self):
        self.assertIn("relationship with the sender is not enough", daemon.CLASSIFY_PROMPT)
        self.assertIn("surveys and feedback requests", daemon.CLASSIFY_PROMPT)


class TestBulkCleanupFallback(unittest.TestCase):
    def test_list_unsubscribe_uses_unsubscribe_mode(self):
        self.assertEqual(daemon._bulk_cleanup_mode(
            {"list-unsubscribe": "<https://example.com/u>"}, []), "unsubscribe")

    def test_promotions_without_header_uses_filter_mode(self):
        self.assertEqual(daemon._bulk_cleanup_mode(
            {}, ["INBOX", "CATEGORY_PROMOTIONS"]), "filter")

    def test_ordinary_mail_without_header_is_ignored(self):
        self.assertIsNone(daemon._bulk_cleanup_mode({}, ["INBOX"]))


class TestClassificationFailure(unittest.TestCase):
    def test_api_failure_is_retryable_error_not_skip(self):
        original = sys.modules.get("anthropic")

        class BrokenMessages:
            def create(self, **kwargs):
                raise RuntimeError("temporary outage")

        fake = types.SimpleNamespace(
            Anthropic=lambda **kwargs: types.SimpleNamespace(messages=BrokenMessages()))
        sys.modules["anthropic"] = fake
        try:
            result = daemon._classify("person@example.com", "Hello", "Body")
        finally:
            if original is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = original
        self.assertEqual(result["tier"], "error")
        self.assertIn("temporary outage", result["reason"])

    def test_inbox_recovery_window_is_at_least_seven_days(self):
        self.assertGreaterEqual(daemon.INBOX_LOOKBACK_S, 7 * 86400)


# ── End-to-end runs of the Gmail loops against a fake Gmail ────────────────────

class _Exec:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def execute(self):
        if self.error:
            raise self.error
        return self.value


class FakeGmail:
    LABELS = [{"id": "L_TRAVEL", "name": "Travel/Flights"},
              {"id": "L_FIN", "name": "Finance"},
              {"id": "L_THER", "name": "Health/Coaching"}]

    def __init__(self, messages, broken_ids=()):
        self.msgs = {m["id"]: m for m in messages}
        self.broken = set(broken_ids)
        self.queries = []

    def users(self):
        return self

    def messages(self):
        return self

    def labels(self):
        return _Labels(self.LABELS)

    def list(self, userId, q, maxResults=50, pageToken=None, **kw):
        """Honours the query terms the loops rely on: in:inbox, -in:inbox and
        -label:"Name" (the old backstop hid the Travel tree that way)."""
        self.queries.append(q)
        names = {lb["id"]: lb["name"] for lb in self.LABELS}
        excluded = set(re.findall(r'-label:"([^"]+)"', q))

        def matches(m):
            labels = set(m["labelIds"])
            if re.search(r"(?<!-)in:inbox", q) and "INBOX" not in labels:
                return False
            if "-in:inbox" in q and "INBOX" in labels:
                return False
            return not ({names.get(l, l) for l in labels} & excluded)

        return _Exec({"messages": [{"id": mid} for mid, m in self.msgs.items() if matches(m)]})

    def get(self, userId, id, format="full", **kw):
        if id in self.broken:
            return _Exec(error=RuntimeError("transient 500"))
        return _Exec(self.msgs[id])


class _Labels:
    def __init__(self, labels):
        self.labels = labels

    def list(self, userId):
        return _Exec({"labels": self.labels})


def gmsg(mid, sender, subject, labels=(), **headers):
    hs = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    hs += [{"name": k.replace("_", "-"), "value": v} for k, v in headers.items()]
    return {"id": mid, "threadId": mid, "labelIds": list(labels),
            "internalDate": "1789400000000",
            "payload": {"mimeType": "text/plain", "headers": hs,
                        "body": {"data": "SGVsbG8="}}}


class DaemonSandbox(unittest.TestCase):
    """Redirects every side effect of the Gmail loops to temp files / recorders."""

    L2_TIER = "skip"

    def setUp(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.sent, self.classified, self.popen = [], [], []
        self._saved = {name: getattr(daemon, name) for name in (
            "STATE_FILE", "PENDING_FILTER", "PENDING_UNSUB", "DIARY_FILE",
            "CAL_FILE", "CAL_QUEUE_FILE", "_handle_itinerary",
            "_gmail", "_classify", "_tg_send", "_tg_send_with_buttons", "_crm_log",
            "_get_signature", "_maybe_propose_sender_filter", "_maybe_propose_unsub",
            "_check_itinerary", "_write_draft")}
        self._sleep, self._popen = daemon.time.sleep, daemon.subprocess.Popen
        daemon.STATE_FILE = str(tmp / "state.json")
        daemon.PENDING_FILTER = tmp / "pending_filter.json"
        daemon.PENDING_UNSUB = tmp / "pending_unsub.json"
        daemon.DIARY_FILE = tmp / "diary.json"
        daemon.CAL_FILE = tmp / "pending_cal_event.json"
        daemon.CAL_QUEUE_FILE = tmp / "pending_cal_queue.json"
        if hasattr(daemon, "_MONITOR"):
            self._monitor = daemon._MONITOR
            daemon._MONITOR = type(daemon._MONITOR)(tmp / "monitoring.sqlite")
        daemon._classify = lambda s, subj, b: (self.classified.append(subj) or
            {"tier": self.L2_TIER, "meeting_request": None, "proposed_time": None,
             "summary": "s", "reason": "r"})
        daemon._tg_send = lambda text, reply_to=None: self.sent.append(text) or 1
        daemon._tg_send_with_buttons = lambda *a, **k: 2
        daemon._crm_log = lambda *a, **k: None
        daemon._get_signature = lambda: ""
        daemon._maybe_propose_sender_filter = lambda *a, **k: None
        daemon._maybe_propose_unsub = lambda *a, **k: None
        daemon._check_itinerary = lambda s, b: {"is_itinerary": False, "type": None}
        daemon._write_draft = lambda *a, **k: ""
        daemon.time.sleep = lambda s: None
        daemon.subprocess.Popen = lambda *a, **k: self.popen.append(a)
        if hasattr(daemon, "_label_cache"):
            daemon._label_cache.update({"names": {}, "fetched_at": 0.0})
        if hasattr(daemon, "_ledger"):
            self._ledger_file = daemon._ledger.LEDGER_FILE
            daemon._ledger.LEDGER_FILE = tmp / "sender_ledger.json"

    def ledger(self):
        return daemon._ledger.load()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(daemon, name, value)
        daemon.time.sleep, daemon.subprocess.Popen = self._sleep, self._popen
        if hasattr(daemon, "_ledger"):
            daemon._ledger.LEDGER_FILE = self._ledger_file
        if hasattr(self, "_monitor"):
            daemon._MONITOR = self._monitor

    def run_with(self, fn, messages, broken=()):
        svc = FakeGmail(messages, broken)
        daemon._gmail = lambda: svc
        fn()
        return svc, daemon._load()


class TestCardAStatementDelivery(DaemonSandbox):
    """Ownership is provisional until the exact notice has been handled."""

    SUBJECT = "Your statement is now available online"

    def setUp(self):
        super().setUp()
        self.statement_state = Path(daemon.STATE_FILE).parent / "statement_watch.json"
        patcher = patch.object(daemon._policy, "_statement_watch_state_path",
                               return_value=self.statement_state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_watch(self, mid, *, recent=True, enabled_providers=None, **notice):
        self.statement_state.write_text(json.dumps({
            "enabled": True,
            "enabled_providers": enabled_providers if enabled_providers is not None else ["card_a"],
            "successful_poll_at": (daemon.datetime.now(daemon.timezone.utc).isoformat()
                                   if recent else "2000-01-01T00:00:00Z"),
            "notices": {"cycle": {"message_ids": [mid], "status": "awaiting_upload",
                                  **notice}},
        }))

    def test_pending_inbox_notice_retries_and_falls_back_after_outage(self):
        mid = "pending-inbox"
        message = gmsg(mid, "Card A <alerts@info.carda.example>", self.SUBJECT, ["INBOX"])
        self.write_watch(mid)
        for _ in range(2):
            _, state = self.run_with(daemon.run_gmail_check, [message])
            self.assertNotIn(mid, state.get("processed_ids", []))
            self.assertEqual(self.sent, [])
        self.write_watch(mid, recent=False)
        _, state = self.run_with(daemon.run_gmail_check, [message])
        self.assertIn(mid, state["processed_ids"])
        self.assertEqual(len(self.sent), 1)

    def test_pending_archived_notice_holds_cursor_and_falls_back_after_outage(self):
        mid = "pending-archived"
        message = gmsg(mid, "Card A <alerts@info.carda.example>", self.SUBJECT, ["L_FIN"])
        self.write_watch(mid)
        for _ in range(2):
            _, state = self.run_with(daemon._labeled_out_backstop, [message])
            self.assertNotIn(mid, state.get("backstop_ids", []))
            self.assertNotIn("backstop_last_scan_epoch", state)
            self.assertEqual(self.sent, [])
        self.write_watch(mid, recent=False)
        _, state = self.run_with(daemon._labeled_out_backstop, [message])
        self.assertIn(mid, state["backstop_ids"])
        self.assertIn("backstop_last_scan_epoch", state)
        self.assertEqual(len(self.sent), 1)

    def test_acknowledged_notice_is_consumed_without_duplicate_during_outage(self):
        for index, extra in enumerate(({"notified_at": "2026-09-28T12:00:00Z"},
                                       {"status": "received"},
                                       {"status": "observed_before_activation"})):
            for fn, labels, seen_key in ((daemon.run_gmail_check, ["INBOX"], "processed_ids"),
                                         (daemon._labeled_out_backstop, ["L_FIN"], "backstop_ids")):
                mid = f"handled-{index}-{seen_key}"
                with self.subTest(mid=mid):
                    self.write_watch(mid, recent=False, **extra)
                    _, state = self.run_with(fn, [gmsg(
                        mid, "Card A <alerts@info.carda.example>", self.SUBJECT, labels)])
                    self.assertIn(mid, state[seen_key])
                    self.assertEqual(self.sent, [])

    def test_new_providers_defer_until_ack_and_fall_back_when_not_enabled(self):
        examples = (
            ("card_b", "Card B <no-reply@email.cardb.example>",
             "Your Card B Credit Card statement is ready!"),
            ("broker_a", "Broker A <alerts@brokera.example>",
             "Your Broker A statement is ready"),
            ("hsa_a", "HSA A <auto_reply@mailer.hsaa.example>",
             "Your HSA statement is now available"),
        )
        for provider, address, subject in examples:
            for fn, labels, seen_key in ((daemon.run_gmail_check, ["INBOX"], "processed_ids"),
                                         (daemon._labeled_out_backstop, ["L_FIN"], "backstop_ids")):
                mid = f"{provider}-{seen_key}"
                with self.subTest(mid=mid):
                    sent_before = len(self.sent)
                    self.write_watch(mid, enabled_providers=[provider], provider=provider)
                    message = gmsg(mid, address, subject, labels)
                    _, state = self.run_with(fn, [message])
                    self.assertNotIn(mid, state.get(seen_key, []))
                    self.assertEqual(len(self.sent), sent_before)
                    self.write_watch(mid, enabled_providers=["card_a"], provider=provider)
                    _, state = self.run_with(fn, [message])
                    self.assertIn(mid, state[seen_key])
                    self.assertEqual(len(self.sent), sent_before + 1)

    def test_new_provider_ack_does_not_duplicate_after_outage(self):
        mid = "handled-hsa-a"
        self.write_watch(mid, recent=False, enabled_providers=["card_a"],
                         provider="hsa_a", status="received")
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(
            mid, "HSA A <auto_reply@mailer.hsaa.example>",
            "Your HSA statement is now available", ["INBOX"])])
        self.assertIn(mid, state["processed_ids"])
        self.assertEqual(self.sent, [])


class TestBackstopEndToEnd(DaemonSandbox):
    def test_card_a_statement_defers_only_while_statement_watcher_is_healthy(self):
        subject = "Your statement is now available online"
        with patch.object(daemon._policy, "statement_watch_active", return_value=True):
            self.run_with(daemon._labeled_out_backstop, [gmsg(
                "card-a-owned", "Card A <alerts@info.carda.example>", subject, ["L_FIN"])])
        self.assertEqual((self.sent, self.classified), ([], []))
        with patch.object(daemon._policy, "statement_watch_active", return_value=False):
            self.run_with(daemon._labeled_out_backstop, [gmsg(
                "card-a-fallback", "Card A <alerts@info.carda.example>", subject, ["L_FIN"])])
        self.assertEqual(len(self.sent), 1)

    def test_security_in_travel_label_pings_in_real_time(self):
        # Regression: the backstop excluded the whole Travel tree, and the
        # travel catch-up only handles itineraries — airline account alerts
        # were never surfaced.
        _, state = self.run_with(daemon._labeled_out_backstop, [gmsg(
            "t1", "Southwest <southwestairlines@ifly.southwest.com>",
            "Security alert: new sign-in to your Rapid Rewards account", ["L_TRAVEL"])])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("🔒", self.sent[0])
        self.assertIn("t1", state["backstop_ids"])

    def test_dedicated_sender_security_surfaces_but_owned_mail_does_not(self):
        # Regression: televisit.example / @opentable.com were skipped wholesale.
        _, state = self.run_with(daemon._labeled_out_backstop, [
            gmsg("s1", "TeleVisit <notifications@televisit.example>",
                 "Your password was changed", ["L_THER"]),
            gmsg("s2", "TeleVisit <notifications@televisit.example>",
                 "Video appointment reminder for Tuesday, September 15th", ["L_THER"]),
        ])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("password", self.sent[0])
        self.assertNotIn("Video appointment reminder", self.classified)
        self.assertIn("s2", state["backstop_ids"])

    def test_finance_watcher_mail_is_not_announced_twice(self):
        self.run_with(daemon._labeled_out_backstop, [gmsg(
            "v1", "P2P App <payments@p2p.example>", "A Friend paid you $75.00", ["L_FIN"],
            List_Unsubscribe="<https://p2p.example/u>")])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.classified, [])

    def test_unowned_record_goes_to_digest_not_a_ping(self):
        _, state = self.run_with(daemon._labeled_out_backstop, [gmsg(
            "sb", "TeleVisit <notifications@televisit.example>",
            "Your superbill(s) for 8/1/26 to 8/31/26", ["L_THER"],
            List_Unsubscribe="<https://sp/u>")])
        self.assertEqual(self.sent, [])
        self.assertEqual([e["mid"] for e in state["fyi_queue"]], ["sb"])

    def test_bulk_finance_marketing_never_reaches_l2(self):
        self.run_with(daemon._labeled_out_backstop, [gmsg(
            "g1", "Card B <no-reply@em.cardb.example>",
            "Your August Card B Credit Card rewards recap is here", ["L_FIN"],
            List_Unsubscribe="<https://cardb/u>")])
        self.assertEqual((self.sent, self.classified), ([], []))

    def test_fetch_failure_is_retried_not_recorded(self):
        # Regression: a transient fetch error left the id in backstop_ids, so the
        # message was never looked at again.
        _, state = self.run_with(daemon._labeled_out_backstop, [gmsg(
            "x1", "Google <no-reply@accounts.google.com>", "Security alert")],
            broken={"x1"})
        self.assertNotIn("x1", state.get("backstop_ids", []))
        self.assertNotIn("backstop_last_scan_epoch", state)

    def test_scan_cursor_advances_after_a_clean_pass(self):
        _, state = self.run_with(daemon._labeled_out_backstop, [])
        self.assertAlmostEqual(state["backstop_last_scan_epoch"], time.time(), delta=5)


class TestBackstopWindow(unittest.TestCase):
    """Regression: a fixed 2h window meant an outage >2h silently skipped every
    security alert a filter had archived in the meantime."""

    def test_first_run_uses_lookback(self):
        self.assertEqual(daemon._backstop_since({}, now=100_000),
                         100_000 - daemon.LOOKBACK_S)

    def test_resumes_from_last_scan_after_an_outage(self):
        now = 1_000_000
        last = now - 3 * 86400
        self.assertEqual(daemon._backstop_since({"backstop_last_scan_epoch": last}, now=now),
                         last - daemon.BACKSTOP_CURSOR_OVERLAP_S)

    def test_window_is_bounded_by_inbox_recovery(self):
        now = 10_000_000
        since = daemon._backstop_since({"backstop_last_scan_epoch": now - 30 * 86400}, now=now)
        self.assertEqual(since, now - daemon.INBOX_LOOKBACK_S)


class TestInboxLoopEndToEnd(DaemonSandbox):
    L2_TIER = "fyi"

    def setUp(self):
        super().setUp()
        # 2026-10-06: without this these tests read the LIVE statement_watch.json, which
        # the finance watchers rewrite every few minutes. The HSA-provider assertion below passed
        # or failed depending on what the watcher had done in the last hour — it passed
        # at 21:00 and failed at 22:00 with no code change in between.
        # TestCardAStatementDelivery already isolates this; this class did not.
        self.statement_state = Path(daemon.STATE_FILE).parent / "statement_watch.json"
        patcher = patch.object(daemon._policy, "_statement_watch_state_path",
                               return_value=self.statement_state)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A healthy watcher owning the providers these tests name. Stated here rather
        # than inherited from the live file: "the HSA provider is owned by the statement watcher" is
        # the premise of the assertion below, so the test has to establish it.
        self.statement_state.write_text(json.dumps({
            "enabled": True,
            "enabled_providers": ["card_a", "hsa_a"],
            "successful_poll_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "notices": {},
        }))

    def test_card_a_statement_ownership_does_not_hide_security_or_fallback(self):
        subject = "Your statement is now available online"
        with patch.object(daemon._policy, "statement_watch_active", return_value=True):
            self.run_with(daemon.run_gmail_check, [
                gmsg("card-a-owned", "Card A <alerts@info.carda.example>", subject, ["INBOX"]),
                gmsg("card-a-security", "Card A <alerts@info.carda.example>",
                     "Security alert: " + subject, ["INBOX"]),
            ])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.classified, ["Security alert: " + subject])
        with patch.object(daemon._policy, "statement_watch_active", return_value=False):
            self.run_with(daemon.run_gmail_check, [gmsg(
                "card-a-fallback", "Card A <alerts@info.carda.example>", subject, ["INBOX"])])
        self.assertEqual(len(self.sent), 2)

    def test_watcher_owned_purchase_in_inbox_is_left_to_the_watcher(self):
        self.run_with(daemon.run_gmail_check, [gmsg(
            "a1", "Card C <alerts@welcome.cardc.example>",
            "Large Purchase Approved", ["INBOX"])])
        self.assertEqual((self.sent, self.classified), ([], []))

    def test_bulk_marketing_from_a_bank_skips_l2(self):
        # Regression: the known-finance L1 override handed bulk promotions to L2,
        # which could promote them to an FYI (policy 6).
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(
            "c1", "Card D <marketing@mail.cardd.example>", "Get 50% off your next 2 rides",
            ["INBOX", "CATEGORY_PROMOTIONS"], List_Unsubscribe="<https://cardd/u>")])
        self.assertEqual(self.classified, [])
        self.assertIn("c1", state["processed_ids"])

    def test_human_subject_that_sounds_like_marketing_still_goes_to_l2(self):
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(
            "h1", "A Colleague <colleague@startup.example>", "Feedback on the deck", ["INBOX"])])
        self.assertEqual(self.classified, ["Feedback on the deck"])
        self.assertEqual([e["mid"] for e in state["fyi_queue"]], ["h1"])

    def test_invalid_model_key_is_health_failure_not_one_fyi_per_email(self):
        daemon._classify = lambda *a: {
            "tier": "error", "meeting_request": None, "proposed_time": None,
            "summary": "", "reason": "classify error: 401 sk-ant-fixture-secret",
        }
        messages = [gmsg(f"bad-{i}", "Sender <sender@example.com>", f"Question {i}", ["INBOX"])
                    for i in range(3)]
        with self.assertRaisesRegex(daemon.ClassificationUnavailable,
                                    "Anthropic classification unavailable"):
            self.run_with(daemon.run_gmail_check, messages)
        state = daemon._load()
        self.assertEqual(self.sent, [])
        self.assertEqual(state.get("processed_ids", []), [])

    def test_classification_outage_alerts_once_at_persistent_threshold(self):
        sent = []
        sleeps = []
        old_running = daemon._running
        daemon._running = True
        def stop_after_threshold(_seconds):
            sleeps.append(1)
            if len(sleeps) >= daemon.GMAIL_FAIL_ALERT_THRESHOLD:
                daemon._running = False
        try:
            with patch.object(daemon, "_run_poll_cycle",
                              side_effect=daemon.ClassificationUnavailable(
                                  "Anthropic classification unavailable")), \
                    patch.object(daemon, "_tg_send", side_effect=lambda text, **k: sent.append(text) or 1), \
                    patch.object(daemon.time, "sleep", side_effect=stop_after_threshold):
                daemon.gmail_loop()
        finally:
            daemon._running = old_running
        self.assertEqual(len(sent), 1)
        self.assertIn("consecutive Gmail check failures", sent[0])
        self.assertNotIn("sk-ant-", sent[0])

    def test_realtime_only_for_security_and_the_realtime_material_set(self):
        _, state = self.run_with(daemon.run_gmail_check, [
            gmsg("r1", "Apple <no_reply@email.apple.com>", "Your receipt from Apple.",
                 ["INBOX"], List_Unsubscribe="<https://apple/u>"),
            gmsg("r2", "HSA A <auto_reply@mailer.hsaa.example>",
                 "Your HSA statement is now available", ["INBOX"],
                 List_Unsubscribe="<https://hsaa/u>"),
            gmsg("r3", "Google <no-reply@accounts.google.com>",
                 "Your Google Account was recovered successfully", ["INBOX"]),
        ])
        self.assertEqual([e["mid"] for e in state["fyi_queue"]], ["r1"])
        # The HSA provider is owned by the healthy statement watcher; only the security
        # alert is real-time here.  It must not also become a daemon FYI.
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.popen, [])   # no diary proposal / subprocess side effects


class TestNoDraftsToRobots(DaemonSandbox):
    """Regression (2026-09-16): a restored photo-app "New Device" alert and an X
    verification code were both classified "action", so the daemon wrote chatty
    reply drafts addressed to noreply@ senders and offered a ✅ Send button.
    The security/material override makes automated mail visible, never
    repliable."""

    L2_TIER = "action"

    def setUp(self):
        super().setUp()
        self.drafted = []
        daemon._write_draft = lambda *a, **k: self.drafted.append(a[0]) or "Dear robot,"

    def test_security_alert_from_a_noreply_sender_gets_no_draft(self):
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(
            "vs", "PhotoApp <noreply@photoapp.example>", "Your PhotoApp Account - New Device", ["INBOX"])])
        self.assertEqual(self.drafted, [])
        self.assertEqual(state.get("gmail_pending", {}), {})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("FYI", self.sent[0])
        self.assertNotIn("Action Needed", self.sent[0])

    def test_verification_code_gets_no_draft(self):
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(
            "xc", "X <info@x.com>", "291930 is your X verification code", ["INBOX"])])
        self.assertEqual(self.drafted, [])
        self.assertEqual(state.get("gmail_pending", {}), {})

    def test_a_real_person_still_gets_a_draft(self):
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(
            "p1", "Alex Example <alex@realdomain.example>",
            "Can you send the deck today?", ["INBOX"])])
        self.assertEqual(self.drafted, ["Alex Example <alex@realdomain.example>"])
        self.assertEqual(list(state["gmail_pending"]), ["p1"])

    def test_vip_on_a_bulk_looking_message_still_gets_a_draft(self):
        self.run_with(daemon.run_gmail_check, [gmsg(
            "d1", "Family Member <family.member@example.com>", "Read this when you get a sec",
            ["INBOX"], List_Unsubscribe="<https://x/u>")])
        self.assertEqual(len(self.drafted), 1)


class TestDeliveryReceipts(DaemonSandbox):
    """Audit D3/D4 (2026-09-16): a notification that never reached Telegram was
    still recorded as handled, so a security alert could vanish for good."""

    L2_TIER = "fyi"
    ALERT = ("g1", "Google <no-reply@accounts.google.com>", "Security alert", ["INBOX"])

    def test_undelivered_inbox_alert_is_retried(self):
        daemon._tg_send = lambda *a, **k: None
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(*self.ALERT)])
        self.assertNotIn("g1", state.get("processed_ids", []))
        daemon._tg_send = lambda text, reply_to=None: self.sent.append(text) or 1
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(*self.ALERT)])
        self.assertIn("g1", state["processed_ids"])
        self.assertEqual(len(self.sent), 1)

    def test_undelivered_backstop_alert_is_retried_and_holds_the_cursor(self):
        daemon._tg_send = lambda *a, **k: None
        _, state = self.run_with(daemon._labeled_out_backstop, [gmsg(
            "b1", "Google <no-reply@accounts.google.com>", "Security alert", ["L_FIN"])])
        self.assertNotIn("b1", state.get("backstop_ids", []))
        self.assertNotIn("backstop_last_scan_epoch", state)

    def test_undelivered_calendar_proposal_is_not_stored(self):
        daemon._tg_send_with_buttons = lambda *a, **k: None
        with self.assertRaises(daemon.DeliveryFailed):
            daemon._publish_cal_proposal(
                {"proposal_text": "Dinner", "gog_events": []}, lambda m: None)
        # Storing it would wedge the single slot behind buttons the user never saw.
        self.assertFalse(daemon.CAL_FILE.exists())

    def test_inbox_itinerary_whose_proposal_failed_is_retried(self):
        def failing(*a, **k):
            raise daemon.DeliveryFailed("buttons not delivered")
        daemon._handle_itinerary = failing
        daemon._check_itinerary = lambda s, b: {"is_itinerary": True, "type": "restaurant"}
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(
            "r1", "Example Bistro <hello@bistro.example>", "Your reservation confirmation", ["INBOX"])])
        self.assertNotIn("r1", state.get("processed_ids", []))

    def test_failed_opentable_fetch_is_retried(self):
        # Audit D5: a message that failed to FETCH was saved as processed.
        daemon._check_itinerary = lambda s, b: {"is_itinerary": False, "type": None}
        _, state = self.run_with(daemon._reservations_label_catchup, [
            gmsg("o1", "OpenTable <no-reply@opentable.com>", "Your reservation confirmation for Example Bistro"),
            gmsg("o2", "OpenTable <no-reply@opentable.com>", "How was Example Bistro?")], broken={"o1"})
        self.assertNotIn("o1", state.get("processed_ids", []))
        self.assertIn("o2", state["processed_ids"])


class TestTelegramSendResult(unittest.TestCase):
    """_tg_send must never raise, and must not claim success for a partial send."""

    class Resp:
        def __init__(self, code):
            self.status_code, self.text = code, "err"

        def json(self):
            return {"result": {"message_id": 42}}

    def run_send(self, text, responses):
        calls = iter(responses)

        def post(*a, **k):
            r = next(calls)
            if isinstance(r, Exception):
                raise r
            return r
        original = sys.modules.get("requests")
        sys.modules["requests"] = types.SimpleNamespace(post=post)
        sleep, daemon.time.sleep = daemon.time.sleep, (lambda s: None)
        # requests.post is replaced above, so nothing can leave the machine and the
        # Telegram URL may safely be built. The token fence (runtime_secrets,
        # 2026-10-06) refuses to interpolate while TELEGRAM_SEND_DISABLED=1, and
        # _tg_send swallows every exception by design — so without this opt-out the
        # fence would show up as a silent None rather than as a failure.
        was = os.environ.get("TELEGRAM_SEND_DISABLED")
        os.environ["TELEGRAM_SEND_DISABLED"] = "0"
        try:
            return daemon._tg_send(text)
        finally:
            if was is None:
                os.environ.pop("TELEGRAM_SEND_DISABLED", None)
            else:
                os.environ["TELEGRAM_SEND_DISABLED"] = was
            daemon.time.sleep = sleep
            if original is None:
                sys.modules.pop("requests", None)
            else:
                sys.modules["requests"] = original

    def test_success(self):
        self.assertEqual(self.run_send("hi", [self.Resp(200)]), 42)

    def test_http_error_is_a_failure(self):
        self.assertIsNone(self.run_send("hi", [self.Resp(502)]))

    def test_network_error_is_a_failure_not_a_crash(self):
        self.assertIsNone(self.run_send("hi", [ConnectionError("no route")]))

    def test_partly_delivered_message_is_a_failure(self):
        long_text = ("x" * 3000 + "\n\n") * 2
        self.assertIsNone(self.run_send(long_text, [self.Resp(502), self.Resp(200)]))


class TestPollCycleIsolation(DaemonSandbox):
    """One catch-up failing must never skip the security backstop."""

    def test_backstop_runs_even_when_an_earlier_step_fails_and_the_error_still_counts(self):
        ran = []
        saved = {n: getattr(daemon, n) for n in (
            "run_gmail_check", "_sender_proposal_catchup", "_televisit_catchup",
            "_travel_label_catchup", "_reservations_label_catchup",
            "_labeled_out_backstop", "_sweep_stale_cal_proposals")}

        def boom():
            ran.append("televisit")
            raise RuntimeError("TeleVisit API 500")
        try:
            daemon.run_gmail_check = lambda: ran.append("inbox")
            daemon._sender_proposal_catchup = lambda svc: ran.append("proposals")
            daemon._televisit_catchup = boom
            daemon._travel_label_catchup = lambda: ran.append("travel")
            daemon._reservations_label_catchup = lambda: ran.append("reservations")
            daemon._labeled_out_backstop = lambda: ran.append("backstop")
            daemon._sweep_stale_cal_proposals = lambda: ran.append("sweep")
            daemon._gmail = lambda: object()
            with self.assertRaises(RuntimeError) as err:
                daemon._run_poll_cycle()
        finally:
            for n, v in saved.items():
                setattr(daemon, n, v)
        self.assertIn("backstop", ran)
        self.assertEqual(ran[-1], "sweep")
        self.assertIn("televisit", str(err.exception))


class TestTravelOwnershipIsEarned(DaemonSandbox):
    """Audit D6: a Travel-labelled message the itinerary model ignored had no
    second owner. Only messages the travel catch-up HANDLED are skipped."""

    L2_TIER = "skip"
    GATE = ("t9", "Airline <alerts@airline.example>", "Your flight is delayed", ["L_TRAVEL"])

    def seed(self, processed=(), handled=()):
        daemon._save({"processed_ids": list(processed), "travel_handled_ids": list(handled)})

    def test_unhandled_travel_update_falls_to_the_backstop(self):
        self.seed(processed=["t9"])
        _, state = self.run_with(daemon._labeled_out_backstop, [gmsg(*self.GATE)])
        self.assertIn("t9", [e["mid"] for e in state.get("fyi_queue", [])])

    def test_travel_message_the_catchup_handled_is_left_alone(self):
        self.seed(processed=["t9"], handled=["t9"])
        _, state = self.run_with(daemon._labeled_out_backstop, [gmsg(*self.GATE)])
        self.assertEqual(state.get("fyi_queue", []), [])
        self.assertIn("t9", state["backstop_ids"])

    def test_travel_message_not_yet_seen_by_the_catchup_is_deferred(self):
        self.seed()
        _, state = self.run_with(daemon._labeled_out_backstop, [gmsg(*self.GATE)])
        self.assertNotIn("t9", state.get("backstop_ids", []))
        self.assertNotIn("backstop_last_scan_epoch", state)

    def test_travel_catchup_records_what_it_handled(self):
        daemon._check_itinerary = lambda s, b: (
            {"is_itinerary": True, "type": "flight"} if "WN" in s else {"is_itinerary": False, "type": None})
        daemon._handle_itinerary = lambda *a, **k: None
        _, state = self.run_with(daemon._travel_label_catchup, [
            gmsg("i1", "SW <a@southwest.com>", "You're going to Denver (WN 123)", ["L_TRAVEL"]),
            gmsg("i2", "SW <a@southwest.com>", "Your flight is delayed", ["L_TRAVEL"])])
        self.assertEqual(state["travel_handled_ids"], ["i1"])
        self.assertEqual(set(state["processed_ids"]), {"i1", "i2"})


class TestSecurityAuthWarning(DaemonSandbox):
    """A security-looking message that fails sender authentication is flagged,
    never suppressed — a parsing slip must not be able to hide a real alert."""

    L2_TIER = "fyi"

    def alert(self, results):
        return gmsg("a1", "Bank <alerts@bank.example>", "Security alert: new sign-in", ["INBOX"],
                    Authentication_Results=results)

    def test_failed_dmarc_is_flagged(self):
        self.run_with(daemon.run_gmail_check, [self.alert("mx.google.com; dmarc=fail header.from=bank.example")])
        self.assertIn("failed email authentication", self.sent[0])

    def test_authenticated_alert_is_not_flagged(self):
        self.run_with(daemon.run_gmail_check, [self.alert(
            "mx.google.com; dkim=pass; spf=pass; dmarc=pass header.from=bank.example")])
        self.assertNotIn("failed email authentication", self.sent[0])

    def test_missing_header_is_no_verdict(self):
        self.assertFalse(daemon._auth_failed({}))


class TestSenderLedgerIntegration(DaemonSandbox):
    """An unknown recurring sender can earn a filing proposal (sender_ledger)."""

    L2_TIER = "fyi"
    DAY = 86400

    def test_inbox_mail_is_tallied_against_its_sender(self):
        self.run_with(daemon.run_gmail_check, [gmsg(
            "n1", "New Vendor <orders@newvendor.com>", "Your order #1 confirmed", ["INBOX"])])
        entry = self.ledger()["orders@newvendor.com"]
        self.assertEqual(entry["count"], 1)
        self.assertEqual(entry["kinds"]["material"], 1)

    def test_the_ledger_stays_out_of_the_hot_state_file(self):
        # It is rewritten on every state save; a growing dataset does not belong.
        _, state = self.run_with(daemon.run_gmail_check, [gmsg(
            "n2", "New Vendor <orders@newvendor.com>", "Your order #2 confirmed", ["INBOX"])])
        self.assertNotIn("sender_ledger", state)
        self.assertIn("orders@newvendor.com", self.ledger())

    def test_vip_and_self_are_never_tallied(self):
        self.run_with(daemon.run_gmail_check, [
            gmsg("v1", "Family Member <family.member@example.com>", "Call me", ["INBOX"]),
            gmsg("m1", f"The user <{daemon.MY_EMAIL}>", "Note to self", ["INBOX"])])
        self.assertEqual(self.ledger(), {})


class TestExpiredProposalRetiresItsButtons(unittest.TestCase):
    """A proposal that times out must have its Telegram buttons retired BEFORE its
    pending file is deleted. The file holds the only record of which message the
    buttons belong to, so deleting it first stranded a live-looking ✅/❌ prompt in
    the chat forever; tapping it later found no pending file and could only answer
    "No pending filter proposal." as a second message underneath (2026-09-26)."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = {n: getattr(daemon, n) for n in ("PENDING_FILTER", "_tg_edit_message")}
        daemon.PENDING_FILTER = self.tmp / "pending_filter.json"
        self.edits = []
        daemon._tg_edit_message = lambda mid, text: self.edits.append((mid, text))

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(daemon, n, v)

    def write(self, age_s, **extra):
        body = {"address": "statements@carda.example", "label": "Finance",
                "created_at": time.time() - age_s, "source_tg_msg_id": 4242}
        body.update(extra)
        daemon.PENDING_FILTER.write_text(json.dumps(body))

    def test_fresh_proposal_is_left_alone(self):
        self.write(60)
        self.assertEqual(daemon._read_fresh_proposal(daemon.PENDING_FILTER, "filter offer"),
                         "statements@carda.example")
        self.assertEqual(self.edits, [])
        self.assertTrue(daemon.PENDING_FILTER.exists())

    def test_expired_proposal_edits_its_own_message_then_deletes_the_file(self):
        self.write(daemon._ff.PROPOSAL_TTL_S + 1)
        self.assertEqual(daemon._read_fresh_proposal(daemon.PENDING_FILTER, "filter offer"), "")
        self.assertEqual(len(self.edits), 1, "the stale prompt was not retired")
        mid, text = self.edits[0]
        self.assertEqual(mid, 4242)
        self.assertIn("filter offer", text)
        self.assertIn("statements@carda.example", text)
        self.assertIn("nothing was changed", text)
        self.assertFalse(daemon.PENDING_FILTER.exists())

    def test_the_kind_word_comes_from_the_caller(self):
        self.write(daemon._ff.PROPOSAL_TTL_S + 1)
        daemon._read_fresh_proposal(daemon.PENDING_FILTER, "unsubscribe offer")
        self.assertIn("unsubscribe offer", self.edits[0][1])

    def test_expired_without_a_message_id_still_deletes_the_file(self):
        self.write(daemon._ff.PROPOSAL_TTL_S + 1, source_tg_msg_id=None)
        self.assertEqual(daemon._read_fresh_proposal(daemon.PENDING_FILTER, "filter offer"), "")
        self.assertEqual(self.edits, [])
        self.assertFalse(daemon.PENDING_FILTER.exists())

    def test_malformed_file_is_deleted_and_never_raises(self):
        daemon.PENDING_FILTER.write_text("{not json")
        self.assertEqual(daemon._read_fresh_proposal(daemon.PENDING_FILTER, "filter offer"), "")
        self.assertFalse(daemon.PENDING_FILTER.exists())

    def test_missing_file_is_a_noop(self):
        self.assertEqual(daemon._read_fresh_proposal(daemon.PENDING_FILTER, "filter offer"), "")
        self.assertEqual(self.edits, [])


class TestLearnedSenderProposal(unittest.TestCase):
    def setUp(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self._saved = {n: getattr(daemon, n) for n in (
            "PENDING_FILTER", "STATE_FILE", "_sender_history", "_tg_send_with_buttons")}
        self._cached = daemon._ff.cached_filter_addresses
        self._ledger_file = daemon._ledger.LEDGER_FILE
        daemon._ledger.LEDGER_FILE = tmp / "sender_ledger.json"
        daemon.PENDING_FILTER = tmp / "pending_filter.json"
        daemon.STATE_FILE = str(tmp / "state.json")
        daemon._ff.cached_filter_addresses = lambda state: set()
        daemon._sender_history = lambda address, svc=None: {"total": 4, "material": 4}
        self.proposals = []
        daemon._tg_send_with_buttons = lambda text, *a, **k: self.proposals.append(text) or 5

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(daemon, n, v)
        daemon._ff.cached_filter_addresses = self._cached
        daemon._ledger.LEDGER_FILE = self._ledger_file

    def seed(self, subjects, step):
        book = {}
        for i, s in enumerate(subjects):
            daemon._ledger.observe(book, "orders@newvendor.com", s, ["INBOX"],
                                   now=time.time() - (len(subjects) - i) * step)
        daemon._ledger.save(book)
        daemon._save({})

    def propose(self):
        daemon._maybe_propose_sender_filter(
            "New Vendor <orders@newvendor.com>", "Your package is out for delivery",
            svc=object())

    def test_recurring_records_earn_a_routed_proposal(self):
        self.seed(["A shipment from order #1 has been delivered",
                   "Your package is out for delivery",
                   "Tracking number for your shipment"], step=8 * 86400)
        self.propose()
        self.assertEqual(len(self.proposals), 1)
        self.assertIn("Records/Packages", self.proposals[0])
        self.assertIn("learned from 3 messages", self.proposals[0])
        pending = json.loads(daemon.PENDING_FILTER.read_text())
        self.assertEqual((pending["source"], pending["domain"], pending["area"]),
                         ("learned", "newvendor.com", "packages"))

    def test_a_single_burst_earns_nothing(self):
        self.seed(["Order #1 confirmed", "Your order shipped", "Package delivered"], step=3600)
        self.propose()
        self.assertEqual(self.proposals, [])

    def test_a_learned_domain_does_not_decide_another_addresss_purpose(self):
        # Audit D7: learning booking@ → Travel/Hotels used to file every other
        # address at that domain as a hotel too, regardless of what it sends.
        original = daemon._sender_cfg
        learned = {"senders": [{"name": "newvendor.com", "area": "travel",
                                "domains": ["newvendor.com"], "label": "Travel/Hotels",
                                "learned_at": "2026-09-16"}]}
        daemon._sender_cfg = lambda: {"senders": learned["senders"] + original()["senders"]}
        try:
            self.seed(["A shipment from order #1 has been delivered",
                       "Your package is out for delivery",
                       "Tracking number for your shipment"], step=8 * 86400)
            self.propose()
        finally:
            daemon._sender_cfg = original
        self.assertEqual(len(self.proposals), 1)
        self.assertIn("Records/Packages", self.proposals[0])
        self.assertNotIn("Travel/Hotels", self.proposals[0])

    def test_unavailable_filter_list_proposes_nothing_and_caches_nothing(self):
        # Audit D8: an API error used to become "no filters exist" for an hour.
        daemon._ff.cached_filter_addresses = lambda state: None
        listing = daemon._ff.list_filter_coverage
        daemon._ff.list_filter_coverage = lambda svc: (_ for _ in ()).throw(RuntimeError("503"))
        try:
            self.seed(["A shipment from order #1 has been delivered",
                       "Your package is out for delivery",
                       "Tracking number for your shipment"], step=8 * 86400)
            self.propose()
        finally:
            daemon._ff.list_filter_coverage = listing
        self.assertEqual(self.proposals, [])
        self.assertNotIn("filter_cache", daemon._load().get("finance_filter", {}))

    def test_unknown_sender_with_no_ledger_history_earns_nothing(self):
        daemon._save({})
        self.propose()
        self.assertEqual(self.proposals, [])


class TestUnsubProposalEvidence(unittest.TestCase):
    """Regression: an unknown bulk sender with insufficient history (policy
    "unknown") slipped past the withhold check and got a Trash proposal."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = {n: getattr(daemon, n) for n in (
            "PENDING_UNSUB", "STATE_FILE", "_sender_history", "_tg_send_with_buttons")}
        self._ff = (daemon._ff.cached_filter_addresses, daemon._ff.inbox_count_from)
        daemon.PENDING_UNSUB = self.tmp / "pending_unsub.json"
        daemon.STATE_FILE = str(self.tmp / "state.json")
        daemon._ff.cached_filter_addresses = lambda state: set()
        daemon._ff.inbox_count_from = lambda svc, a, cap=5: 5
        self.proposals = []
        daemon._tg_send_with_buttons = lambda text, *a, **k: self.proposals.append(text) or 7

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(daemon, n, v)
        daemon._ff.cached_filter_addresses, daemon._ff.inbox_count_from = self._ff

    def propose(self, profile):
        daemon._sender_history = lambda address, svc=None: profile
        daemon._maybe_propose_unsub("Promo <promo@example.com>", "Hello",
                                    {"list-unsubscribe": "<https://x/u>"},
                                    ["INBOX", "CATEGORY_PROMOTIONS"], svc=object())

    def test_single_message_history_is_withheld(self):
        self.propose({"total": 1, "marketing": 1})
        self.assertEqual(self.proposals, [])

    def test_unknown_subjects_are_withheld(self):
        self.propose({"total": 4, "marketing": 3, "unknown": 1})
        self.assertEqual(self.proposals, [])

    def test_consistent_marketing_is_proposed(self):
        self.propose({"total": 3, "marketing": 3})
        self.assertEqual(len(self.proposals), 1)


class _SettingsReq:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeForwardingGmail:
    """Just the two read-only Gmail settings calls the forwarding alarm makes."""

    def __init__(self, addresses=(), auto=None, error=None):
        self.addresses, self.auto, self.error, self.reads = list(addresses), auto or {}, error, 0

    def users(self):
        return self

    def settings(self):
        return self

    def forwardingAddresses(self):
        return self

    def list(self, userId):
        return _SettingsReq(self._list)

    def getAutoForwarding(self, userId):
        return _SettingsReq(lambda: self.auto)

    def _list(self):
        self.reads += 1
        if self.error:
            raise RuntimeError(self.error)
        return {"forwardingAddresses": self.addresses}


class TestForwardingAlarm(DaemonSandbox):
    THIEF = [{"forwardingEmail": "thief@evil.example", "verificationStatus": "accepted"}]

    def watch(self):
        return daemon._load().get(daemon._fwd.STATE_KEY, {})

    def make_due(self):
        state = daemon._load()
        state.setdefault(daemon._fwd.STATE_KEY, {})["last_check"] = 0
        daemon._save(state)

    def run_alarm(self, svc):
        daemon._gmail = lambda: svc
        daemon._forwarding_watch()

    def test_it_runs_every_poll_cycle(self):
        import inspect
        self.assertIn("_forwarding_watch", inspect.getsource(daemon._run_poll_cycle))

    def test_clean_settings_are_quiet_and_recorded_healthy(self):
        self.run_alarm(FakeForwardingGmail())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.watch()["signature"], "")
        self.assertEqual(daemon._MONITOR.source(daemon._fwd.SOURCE)["status"], "complete")

    def test_forwarding_alerts_once_and_is_not_rechecked_until_due(self):
        svc = FakeForwardingGmail(self.THIEF, {"enabled": True, "emailAddress": "thief@evil.example"})
        self.run_alarm(svc)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("thief@evil.example", self.sent[0])
        self.run_alarm(svc)  # a minute later: not due, no API call, no repeat
        self.assertEqual((svc.reads, len(self.sent)), (1, 1))
        self.make_due()
        self.run_alarm(svc)  # due again, same state: no repeat within a day
        self.assertEqual((svc.reads, len(self.sent)), (2, 1))

    def test_an_undelivered_alert_is_retried_next_cycle(self):
        svc = FakeForwardingGmail(self.THIEF)
        daemon._tg_send = lambda *a, **k: None
        with self.assertRaises(daemon.DeliveryFailed):
            self.run_alarm(svc)
        self.assertNotIn(daemon._fwd.STATE_KEY, daemon._load())
        daemon._tg_send = lambda text, reply_to=None: self.sent.append(text) or 1
        self.run_alarm(svc)
        self.assertEqual(len(self.sent), 1)
        self.assertNotEqual(self.watch()["signature"], "")

    def test_repeated_read_failures_alert_instead_of_going_silent(self):
        svc = FakeForwardingGmail(error="403 insufficient permissions")
        for _ in range(3):
            self.make_due()
            with self.assertRaises(RuntimeError):
                self.run_alarm(svc)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("isn't protecting you", self.sent[0])
        self.assertEqual(daemon._MONITOR.source(daemon._fwd.SOURCE)["status"], "unavailable")
        svc.error = None
        self.make_due()
        self.run_alarm(svc)
        self.assertEqual(self.watch()["failures"], 0)

    def test_an_undelivered_failure_alert_is_retried(self):
        svc = FakeForwardingGmail(error="boom")
        daemon._tg_send = lambda *a, **k: None
        for _ in range(3):
            self.make_due()
            with self.assertRaises(RuntimeError):
                self.run_alarm(svc)
        self.assertNotIn("failure_alerted_at", self.watch())
        daemon._tg_send = lambda text, reply_to=None: self.sent.append(text) or 1
        self.make_due()
        with self.assertRaises(RuntimeError):
            self.run_alarm(svc)
        self.assertEqual(len(self.sent), 1)


class ArrivalGmail:
    """Messages, threads and labels — enough of Gmail for E1's arrival filing."""

    def __init__(self, messages, labels=("Records/Receipts",)):
        self.msgs = {m["id"]: m for m in messages}
        self.label_list = [{"id": f"L{i}", "name": n} for i, n in enumerate(labels)]
        self.modified, self.created = [], []

    def users(self):
        return self

    def labels(self):
        svc = self

        class L:
            def list(self, userId):
                return _SettingsReq(lambda: {"labels": list(svc.label_list)})

            def create(self, userId, body):
                def run():
                    new = {"id": f"L{len(svc.label_list)}", "name": body["name"]}
                    svc.label_list.append(new); svc.created.append(body["name"])
                    return new
                return _SettingsReq(run)
        return L()

    def _meta(self, m):
        return {"id": m["id"], "threadId": m.get("thread", m["id"]), "labelIds": list(m["labels"]),
                "snippet": m.get("snippet", ""), "internalDate": str(int(m.get("ts", 0) * 1000)),
                "payload": {"headers": [{"name": k, "value": v} for k, v in m.get("headers", {}).items()]}}

    def messages(self):
        svc = self

        class M:
            def list(self, userId, q, maxResults=50, pageToken=None):
                if q.startswith("from:me to:"):
                    addr = q.split("to:")[1]
                    hit = [m for m in svc.msgs.values() if "SENT" in m["labels"] and addr in m.get("headers", {}).get("To", "")]
                    return _SettingsReq(lambda: {"messages": [{"id": m["id"]} for m in hit]})
                return _SettingsReq(lambda: {"messages": [{"id": i} for i, m in svc.msgs.items() if "INBOX" in m["labels"]]})

            def get(self, userId, id, format="metadata", metadataHeaders=None):
                return _SettingsReq(lambda: svc._meta(svc.msgs[id]))

            def modify(self, userId, id, body):
                def run():
                    svc.modified.append((id, body))
                    m = svc.msgs[id]
                    m["labels"] = [l for l in m["labels"] if l not in body["removeLabelIds"]] + body["addLabelIds"]
                return _SettingsReq(run)
        return M()

    def threads(self):
        svc = self

        class T:
            def get(self, userId, id, format="minimal", metadataHeaders=None):
                members = sorted((m for m in svc.msgs.values() if m.get("thread", m["id"]) == id), key=lambda m: m.get("ts", 0))
                return _SettingsReq(lambda: {"messages": [svc._meta(m) for m in members]})

            def list(self, userId, q, maxResults=100, pageToken=None):
                tids = {m.get("thread", m["id"]) for m in svc.msgs.values() if "INBOX" in m["labels"]}
                return _SettingsReq(lambda: {"threads": [{"id": t} for t in sorted(tids)]})
        return T()


def amsg(mid, frm, subject, labels=("INBOX", "CATEGORY_UPDATES"), **kw):
    return {"id": mid, "labels": list(labels), "headers": {"From": frm, "Subject": subject, **kw.pop("headers", {})}, **kw}


class TestArrivalFiling(DaemonSandbox):
    CARD_D = ("c1", "Card D Card Services <marketing@mail.cardd.example>", "Remember, the user, make the most of the Card D Mobile app")

    def setUp(self):
        super().setUp()
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self._arrive = (daemon._arrive.CONFIG, daemon._arrive.LOG, daemon._classify_type)
        daemon._arrive.CONFIG, daemon._arrive.LOG = tmp / "arrival_filing.json", tmp / "arrival.jsonl"
        self.asked = []
        daemon._classify_type = lambda m: self.asked.append(m["addr"]) or "account_notice"

    def tearDown(self):
        daemon._arrive.CONFIG, daemon._arrive.LOG, daemon._classify_type = self._arrive
        super().tearDown()

    def go(self, messages, processed, live=False, **state):
        daemon._arrive.CONFIG.write_text(json.dumps({"mode": "live" if live else "watch"}))
        daemon._save({"processed_ids": list(processed), **state})
        svc = ArrivalGmail(messages)
        daemon._gmail = lambda: svc
        daemon._arrival_filing()
        log = daemon._arrive.LOG
        return svc, [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    def test_watch_mode_decides_and_logs_but_changes_nothing(self):
        svc, log = self.go([amsg(*self.CARD_D)], processed=["c1"])
        self.assertEqual(svc.modified, [])
        self.assertEqual((log[0]["mode"], log[0]["decision"]["remove"], log[0]["decision"]["why"]),
                         ("watch", ["INBOX"], "rule fin-card-d-noise"))
        self.assertEqual(daemon._load()["arrival_seen"], ["c1"])

    def test_live_mode_files_and_creates_a_missing_folder(self):
        svc, _ = self.go([amsg("r1", "PayApp <receipts@payapp.example>", "Your PayApp receipt"),
                          amsg("n1", "Example Auto Spa <hello@autospa.example>", "Unlimited Plan has been discontinued.",
                               labels=("INBOX", "CATEGORY_PERSONAL"))], processed=["r1", "n1"], live=True)
        done = dict(svc.modified)
        self.assertEqual(done["r1"], {"addLabelIds": ["L0"], "removeLabelIds": ["INBOX"]})
        self.assertIn("Records/Notifications/Accounts", svc.created)   # the AI said account_notice
        self.assertEqual(self.asked, ["hello@autospa.example"])  # a rule-matched receipt never costs an AI call

    def test_nothing_is_filed_before_the_inbox_loop_has_finished_with_it(self):
        svc, log = self.go([amsg(*self.CARD_D)], processed=[], live=True)
        self.assertEqual((svc.modified, log), ([], []))

    def test_a_message_with_a_pending_reply_draft_is_left_alone(self):
        svc, _ = self.go([amsg(*self.CARD_D)], processed=["c1"], live=True, gmail_pending={"c1": {}})
        self.assertEqual(svc.modified, [])

    def test_people_owner_corresponds_with_are_never_filed(self):
        now = time.time()   # a live conversation: he replied yesterday
        thread = [amsg("p1", "A Contact <contact2@example.org>", "Re: receipt for breakfast", labels=("INBOX", "CATEGORY_PERSONAL"), thread="T1", ts=now - 86400),
                  amsg("p0", "The user <owner@example.com>", "Breakfast", labels=("SENT",), thread="T1", ts=now - 2 * 86400, headers={"To": "contact2@example.org"})]
        svc, log = self.go(thread, processed=["p1"], live=True)
        self.assertEqual((svc.modified, [e for e in log if "id" in e], self.asked), ([], [], []))

    def test_a_vacation_auto_reply_does_not_shield_a_robot_from_filing(self):
        # The live bug: a bank's alert address stayed in the inbox for hours.
        wf = amsg("w1", "Bank A <alerts@notify.banka.example>", "Your account update is here",
                  labels=("INBOX", "CATEGORY_UPDATES", "L0"))
        auto = amsg("v1", "The user <owner@example.com>", "Currently Unavailable Re: Your statement", labels=("SENT",),
                    headers={"To": "alerts@notify.banka.example", "Auto-Submitted": "auto-replied", "X-Autoreply": "yes"})
        same_thread = amsg("v2", "The user <owner@example.com>", "Currently Unavailable Re: Your account update is here",
                           labels=("SENT",), thread="w1", headers={"To": "alerts@notify.banka.example", "Auto-Submitted": "auto-replied"})
        svc, _ = self.go([wf, auto, same_thread], processed=["w1"], live=True)
        self.assertEqual([i for i, _ in svc.modified], ["w1"])
        self.assertNotIn("INBOX", svc.msgs["w1"]["labels"])

    def test_enabled_finance_rule_files_exact_mail_without_ai(self):
        old = daemon._finance_mail.ACTIVE
        try:
            # 2026-10-09: these read the spent finance_mail_rules.proposed.json, which was
            # deleted once the rules went live on 2026-10-03. The rules under test are the
            # ones actually filing the user's mail, so copy the LIVE file.
            live = json.loads(daemon._finance_mail.ACTIVE.read_text())
            daemon._finance_mail.ACTIVE = daemon._arrive.CONFIG.parent / "finance_mail_rules.json"
            daemon._finance_mail.ACTIVE.write_text(json.dumps({**live, "enabled": True}))
            svc, log = self.go([
                amsg("k1", "Exchange A <noreply@exchangea.example>", "You bought BTC")
            ], processed=["k1"], live=True)
            self.assertEqual(self.asked, [])
            self.assertNotIn("INBOX", svc.msgs["k1"]["labels"])
            self.assertEqual(log[0]["decision"]["why"],
                             "finance rule finance-exchange-a-buy-fill")
        finally:
            daemon._finance_mail.ACTIVE = old

    def test_enabled_finance_rule_never_files_security_mail(self):
        old = daemon._finance_mail.ACTIVE
        try:
            # 2026-10-09: these read the spent finance_mail_rules.proposed.json, which was
            # deleted once the rules went live on 2026-10-03. The rules under test are the
            # ones actually filing the user's mail, so copy the LIVE file.
            live = json.loads(daemon._finance_mail.ACTIVE.read_text())
            daemon._finance_mail.ACTIVE = daemon._arrive.CONFIG.parent / "finance_mail_rules.json"
            daemon._finance_mail.ACTIVE.write_text(json.dumps({**live, "enabled": True}))
            svc, log = self.go([
                amsg("k1", "Exchange A <noreply@exchangea.example>", "Security alert: new login")
            ], processed=["k1"], live=True)
            self.assertEqual((svc.modified, log), ([], []))
            self.assertIn("INBOX", svc.msgs["k1"]["labels"])
        finally:
            daemon._finance_mail.ACTIVE = old

    def test_a_real_reply_to_a_support_ticket_keeps_it_in_the_inbox(self):
        now = time.time()
        ticket = amsg("t1", "Device Support <support@devicemaker.zendesk.example>", "Re: Rangefinder | RMA", thread="TT", ts=now - 3600,
                      labels=("INBOX", "CATEGORY_UPDATES"), headers={"List-Unsubscribe": "<x>"})
        mine = amsg("t0", "The user <owner@example.com>", "Rangefinder | Ranging value incorrect", labels=("SENT",), thread="TT", ts=now - 7200,
                    headers={"To": "support@devicemaker.zendesk.example"})
        svc, _ = self.go([ticket, mine], processed=["t1"], live=True)
        self.assertEqual(svc.modified, [])

    def test_a_rulebook_change_re_decides_mail_already_in_the_inbox(self):
        self.go([amsg(*self.CARD_D)], processed=["c1"], live=True)
        seen_under = daemon._load()["arrival_mode"]
        self.assertIn(f":{daemon.ARRIVAL_LOGIC_VERSION}:", seen_under)
        svc, _ = self.go([amsg(*self.CARD_D)], processed=["c1"], live=True, arrival_seen=["c1"], arrival_mode="live:1:0")
        self.assertEqual(len(svc.modified), 1)          # old logic version: decided again

    def test_switching_mode_re_evaluates_what_watch_mode_only_logged(self):
        self.go([amsg(*self.CARD_D)], processed=["c1"])
        svc, _ = self.go([amsg(*self.CARD_D)], processed=["c1"], live=True, arrival_seen=["c1"], arrival_mode="watch")
        self.assertEqual(len(svc.modified), 1)

    def test_a_finished_conversation_leaves_and_an_unanswered_ask_never_does(self):
        now = time.time()
        msgs = [amsg("a1", "Dr. Example Office <clinic@example.org>", "Appt notes", labels=("INBOX", "CATEGORY_PERSONAL"), thread="TA", ts=now - 31 * 86400),
                amsg("q0", "The user <owner@example.com>", "Catching up", labels=("SENT", "INBOX"), thread="TQ", ts=now - 40 * 86400,
                     snippet="Would love to catch up — are you free next week?", headers={"To": "contact3@firm.example"}),
                amsg("q1", "A Contact <contact3@firm.example>", "Re: Catching up", labels=("INBOX", "CATEGORY_PERSONAL"), thread="TQ", ts=now - 60 * 86400)]
        msgs[1]["ts"], msgs[2]["ts"] = now - 40 * 86400, now - 60 * 86400
        svc, log = self.go(msgs, processed=[], live=True)
        self.assertEqual([i for i, _ in svc.modified], ["a1"])                       # only the finished thread moved
        self.assertIn("Health/Dr. Example", svc.created)
        nudges = [e for e in log if e.get("decision", {}).get("action") == "nudge"]
        self.assertEqual(len(nudges), 1)
        self.assertIn("INBOX", svc.msgs["q0"]["labels"])


class FinanceFeedsStaySilent(unittest.TestCase):
    """2026-10-06: a broker's crypto statement reached the user as an FYI, twice. The
    subject matched the realtime-statement set, and the labelled-out backstop
    announced it again because it dedups on its own id list."""

    def test_a_filed_feed_mail_is_never_announced(self):
        self.assertTrue(daemon._silently_ingested(
            "Broker C <noreply@brokerc.example>", "Your crypto monthly statement is available"))
        self.assertTrue(daemon._silently_ingested(
            "noreply@brokerc.example", "Your order has been executed"))

    def test_every_pure_feed_the_log_shows_announced_is_now_silent(self):
        """The daemon log shows these were announced repeatedly, by both paths."""
        for addr, subject in [
                ("noreply@exchangea.example", "You bought BTC"),
                ("noreply@secure.exchangea.example", "You bought ETH"),
                ("noreply@brokerc.example", "Your crypto monthly statement is available"),
                ("noreply@brokerc.example", "Your order has been executed"),
                ("alerts@info.carda.example",
                 "A $11.68 transaction was made on your Card A account"),
                ("alerts@notify.banka.example", "Your balance is below $1,000,000.00"),
                ("alerts@notify.banka.example", "Your account update is here")]:
            self.assertTrue(daemon._silently_ingested(addr, subject), subject)

    def test_both_announcing_paths_apply_the_same_rules(self):
        import inspect
        inbox = inspect.getsource(daemon)
        backstop = inspect.getsource(daemon._labeled_out_backstop)
        self.assertIn("_silently_ingested(sender, subject)", backstop)
        self.assertEqual(inbox.count("_remember_announced(mid)"), 4)

    def test_security_and_marketing_are_not_silenced(self):
        self.assertFalse(daemon._silently_ingested(
            "noreply@brokerc.example", "Unusual activity on your account"))
        self.assertFalse(daemon._silently_ingested(
            "noreply@brokerc.example", "Looking for your next investment?"))
        self.assertFalse(daemon._silently_ingested(
            "someone@example.com", "Your crypto monthly statement is available"))

    def test_the_backstop_never_re_announces_what_was_already_announced(self):
        import inspect
        source = inspect.getsource(daemon._labeled_out_backstop)
        self.assertIn('state.get("notified_ids"', source)
        self.assertIn("if mid in already_announced", source)
        # and the inbox loop records both the ping and the digest entry
        loop = inspect.getsource(daemon)
        self.assertEqual(loop.count("_remember_announced(mid)"), 4)
        # processed_ids must NOT be the gate: the travel catch-up hands work over
        # through that list on purpose.
        self.assertNotIn("if mid in travel_seen:", source)


class TestDraftSignature(unittest.TestCase):
    """One sign-off per reply.

    2026-10-09: a reply draft to a golf venue reached Telegram signed twice —
    the model's own "The user" and then the Gmail signature's "Best, / the user"
    under it. Both the prompts and the join were at fault; this covers the join,
    which is the half that can be tested.
    """
    SIG = "Best,\n\nthe user\n555.010.0000\nLinkedIn"

    def test_the_models_own_closing_is_replaced_by_the_signature(self):
        draft = ("Sam,\n\nHappy to come see the venue.\n\n"
                 "Looking forward to it.\n\nOwner")
        out = daemon._with_signature(draft, self.SIG)
        self.assertTrue(out.endswith(self.SIG))
        self.assertEqual(out.count("the user"), 1)
        # the sentence above the sign-off is body text and must survive
        self.assertIn("Looking forward to it.", out)
        # "Best," appears once — the signature's — and "the user" only inside it
        self.assertEqual(out.count("Best,"), 1)
        self.assertNotIn("Looking forward to it.\n\nOwner\n", out)

    def test_a_valediction_above_the_name_also_goes(self):
        for closing in ("Best,\n\nOwner", "Thanks,\nthe user", "Cheers,\n\n-the user",
                        "Talk soon,\nOwner", "All the best,\n\nOwner R."):
            out = daemon._with_signature(f"Sam,\n\nSounds good.\n\n{closing}", self.SIG)
            self.assertEqual(out, f"Sam,\n\nSounds good.\n\n{self.SIG}", closing)

    def test_body_text_that_merely_starts_with_thanks_is_kept(self):
        draft = "Sam,\n\nThanks for sending the Trackman numbers — they help."
        out = daemon._with_signature(draft, self.SIG)
        self.assertIn("Thanks for sending the Trackman numbers", out)
        self.assertEqual(out, f"{draft}\n\n{self.SIG}")

    def test_an_unsigned_draft_just_gets_the_signature(self):
        draft = "Sam,\n\nMonday at 9 works."
        self.assertEqual(daemon._with_signature(draft, self.SIG),
                         f"{draft}\n\n{self.SIG}")

    def test_no_signature_leaves_the_models_closing_alone(self):
        """A signature lookup that failed must not strip the only sign-off there is."""
        draft = "Sam,\n\nMonday at 9 works.\n\nOwner"
        self.assertEqual(daemon._with_signature(draft, ""), draft)

    def test_a_draft_that_is_only_a_sign_off_is_not_emptied(self):
        self.assertEqual(daemon._strip_closing("Best,\n\nOwner"), "Best,\n\nOwner")

    def test_both_send_paths_join_through_the_one_helper(self):
        import inspect
        source = inspect.getsource(daemon)
        self.assertEqual(source.count("draft = _with_signature(draft"), 2)
        # the old hand-rolled append must not come back
        self.assertNotIn('draft.rstrip() + f"\\n\\n{sig}"', source)
        self.assertNotIn('draft.rstrip() + (f"\\n\\n{sig}" if sig else "")', source)

    def test_the_prompts_tell_the_model_not_to_sign(self):
        for prompt in (daemon.DRAFT_PROMPT, daemon.AVAIL_PROMPT):
            self.assertIn("Never add a closing line or sign your name", prompt)

    def test_the_draft_prompt_bans_filler_openers(self):
        """The user, Telegram, 2026-10-09: "please do not put lines like this in emails
        moving forwad. Its not necessary: [Thanks] for the personal touch—I appreciate
        it." The durable principle is baked in; his verbatim rule arrives via
        _standing_email_rules so a later instruction lands without a code change."""
        flat = " ".join(daemon.DRAFT_PROMPT.split())
        self.assertIn("No filler or acknowledgement sentence", flat)
        # ...without losing the greeting: the first redraft dropped "Sam," entirely
        self.assertIn("Keep the greeting line", flat)


class TestStandingEmailRules(unittest.TestCase):
    """His [email]-tagged standing instructions must reach the drafting prompt.

    2026-10-09: the rule was filed in standing-instructions.md — read by Archibald
    before planning, and by the email daemon not at all — so the writer that
    actually composes his mail never saw it.
    """

    def setUp(self):
        daemon._standing_cache.update({"text": "", "mtime": None})
        self.addCleanup(daemon._standing_cache.update, {"text": "", "mtime": None})

    def _write(self, body: str) -> Path:
        import tempfile
        path = Path(tempfile.mkdtemp()) / "standing-instructions.md"
        path.write_text(body)
        return path

    def test_email_tagged_lines_reach_the_prompt_verbatim(self):
        path = self._write(
            "# Standing Instructions\n\n"
            "- 2026-10-09 [email] Keep emails clean and direct.\n")
        with patch.object(daemon, "STANDING_FILE", path):
            out = daemon._standing_email_rules()
        self.assertIn("Keep emails clean and direct.", out)
        self.assertIn("in his own words", out)

    def test_other_topics_are_left_out(self):
        path = self._write(
            "- 2026-09-20 [calendar] Example calendar rule.\n"
            "- 2026-10-07 [finance] Example finance rule.\n")
        with patch.object(daemon, "STANDING_FILE", path):
            out = daemon._standing_email_rules()
        self.assertEqual(out, "")

    def test_no_email_rules_adds_nothing_to_the_prompt(self):
        path = self._write("# Standing Instructions\n")
        with patch.object(daemon, "STANDING_FILE", path):
            self.assertEqual(daemon._standing_email_rules(), "")

    def test_a_missing_file_is_not_an_error(self):
        with patch.object(daemon, "STANDING_FILE", Path("/nonexistent/standing.md")):
            self.assertEqual(daemon._standing_email_rules(), "")

    def test_a_new_rule_applies_without_a_restart(self):
        path = self._write("- 2026-10-09 [email] First rule.\n")
        with patch.object(daemon, "STANDING_FILE", path):
            self.assertIn("First rule.", daemon._standing_email_rules())
            import os, time
            path.write_text("- 2026-10-09 [email] First rule.\n"
                            "- 2026-10-10 [email] Second rule.\n")
            os.utime(path, (time.time() + 5, time.time() + 5))
            out = daemon._standing_email_rules()
        self.assertIn("Second rule.", out)

    def test_the_injected_block_is_capped(self):
        long_rule = "x" * 400
        path = self._write("".join(
            f"- 2026-10-09 [email] {long_rule}{i}\n" for i in range(10)))
        with patch.object(daemon, "STANDING_FILE", path):
            out = daemon._standing_email_rules()
        self.assertLessEqual(len(out), daemon.STANDING_MAX_CHARS + 120)

    def test_both_drafting_prompts_get_them(self):
        import inspect
        source = inspect.getsource(daemon)
        self.assertIn("DRAFT_PROMPT + _standing_email_rules()", source)
        self.assertIn("AVAIL_PROMPT + _standing_email_rules()", source)

    def test_email_is_a_real_remember_topic(self):
        """remember.py has to accept --topic email or nothing can be tagged."""
        text = Path(os.path.expanduser("~/.openclaw/workspace/Skills/shared/remember.py")).expanduser().read_text()
        self.assertIn('"email"', text.split("TOPICS = ")[1].split(")")[0])


class TestSuiteWiring(unittest.TestCase):
    """unittest.main() has to stay the last thing in this file.

    FinanceFeedsStaySilent was appended below it on 2026-10-06 and never ran
    once — main() exits the process before Python reaches the class, so five
    regression tests for the double-FYI bug sat dead until 2026-10-09. A reader
    cannot tell a dead class from a live one; this can.
    """

    def test_nothing_is_defined_below_the_entry_point(self):
        lines = Path(__file__).read_text().splitlines()
        entry = [i for i, line in enumerate(lines) if line.startswith("if __name__")]
        self.assertEqual(len(entry), 1, "expected exactly one entry point")
        below = [line for line in lines[entry[0]:]
                 if line.startswith("class ") or line.strip().startswith("def test")]
        self.assertEqual(below, [], f"never runs — below unittest.main(): {below}")



class TestFreeSlotFormatting(unittest.TestCase):
    """An opening is a window, and its length has to survive into the draft.

    2026-10-09: _fmt_slots printed only the start, so a 30-minute gap and a
    five-hour morning rendered identically and the reply offered a bare "9:00 AM".
    """

    def slots(self, *spans):
        from datetime import datetime, timedelta
        out = []
        for hour, minutes in spans:
            start = datetime(2026, 10, 12, hour, 0, tzinfo=daemon.CT)
            out.append((start, start + timedelta(minutes=minutes)))
        return out

    def test_a_window_shows_both_ends(self):
        line = daemon._fmt_slots(self.slots((9, 300)))
        self.assertEqual(line, "• Monday Oct 12, 9:00 AM–2:00 PM CT")

    def test_a_short_gap_is_visibly_short(self):
        line = daemon._fmt_slots(self.slots((15, 30)))
        self.assertIn("3:00 PM–3:30 PM", line)
        # the old format would have been indistinguishable from a five-hour window
        self.assertNotEqual(line, daemon._fmt_slots(self.slots((15, 300))))

    def test_every_slot_gets_its_own_line(self):
        self.assertEqual(len(daemon._fmt_slots(self.slots((9, 120), (15, 180))).splitlines()), 2)

    def test_the_avail_prompt_asks_for_windows_not_start_times(self):
        flat = " ".join(daemon.AVAIL_PROMPT.split())
        self.assertIn("never as a single start time", flat)


class TestEmailApprovalTTL(unittest.TestCase):
    """The ✅ must outlive the card it sits under, not die 15 minutes in.

    2026-10-09: a reply-draft approval expired at 11:21 while its card sat in
    the chat for 48h. The user opened it at ~11:47 and the button was already inert.
    """

    def test_email_approvals_last_as_long_as_the_pending_entry(self):
        import inspect
        source = inspect.getsource(daemon._tg_send_with_buttons)
        self.assertIn('{"ttl": PENDING_TTL_S}', source)
        self.assertNotIn('if action_type == "cal" else {}', source)

    def test_the_two_lifetimes_are_the_same_number(self):
        """One source of truth: the card's life IS the button's life."""
        self.assertEqual(daemon.PENDING_TTL_S, 172800)


if __name__ == "__main__":
    unittest.main(verbosity=2)
