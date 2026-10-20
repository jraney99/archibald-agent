#!/usr/bin/env python3
"""
Confirm a pending calendar event proposal.
Called by Archibald when user taps ✅ (confirm:cal) on a daemon proposal.
"""
import json, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "~/.openclaw/workspace/Skills/email-monitor/scripts")
sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")
from approval import require_cli, finish

CAL_FILE       = Path("~/.openclaw/pending_cal_event.json")
CAL_QUEUE_FILE = Path("~/.openclaw/pending_cal_queue.json")
GOG      = "/opt/homebrew/bin/gog"
EMAIL    = "owner@example.com"

# User-facing result lines, collected during the run and delivered once at the
# end by _finish() — as an in-place edit of the original proposal message when
# we know its id (one message that updates, no second reply under dead buttons).
OUT: list = []


def out(line: str) -> None:
    OUT.append(line)


def _finish(proposal: dict, remove_card: bool = False) -> None:
    """Report the outcome on the card the user tapped.

    `remove_card` deletes it instead, for the paths that END with the event on his calendar:
    "when I approve a calendar card and it's on my calendar, can we delete the message? It's
    becoming clutter" (2026-10-08). The event is the record, so the card has nothing to say. It
    is archived first, and anything other than a clean delete (Telegram refuses messages over
    48h old) falls through to the edit below, so an outcome is never silently dropped.
    """
    body = "\n\n".join(s for s in OUT if s).strip() or "Done."
    src = proposal.get("source_tg_msg_id")
    orig = (proposal.get("proposal_text") or "").strip()
    if src and remove_card:
        try:
            from chat_cleanup import remove_now
            if remove_now(src, f"{orig}\n\n✅ {body}", skill="calendar",
                          reason="approved and verified on the calendar") == "removed":
                print("NO_REPLY")
                return
        except Exception:
            pass
    if src and orig:
        try:
            from send_telegram import edit_message_text
            if edit_message_text(src, f"{orig}\n\n✅ {body}"):
                print("NO_REPLY")
                return
        except Exception:
            pass
    print(body)


def _promote_next_queued() -> None:
    """
    CAL_FILE is a single-slot pending proposal; a second proposal arriving
    before the first was confirmed queues in CAL_QUEUE_FILE instead of
    clobbering it (see daemon.py's _publish_cal_proposal). Once the active
    proposal is resolved, move the next queued one into the slot so it isn't
    silently lost.
    """
    if not CAL_QUEUE_FILE.exists():
        return
    try:
        queue = json.loads(CAL_QUEUE_FILE.read_text())
    except Exception:
        queue = []
    import daemon as _d
    # Drop any queued proposals that have gone stale (event already passed /
    # sat unconfirmed too long) rather than promoting a dead one into the slot.
    while queue:
        stale, why = _d._cal_proposal_should_drop(queue[0])
        if not stale:
            break
        _d._log("Cal", f"skipped promoting stale queued proposal ({why}): "
                       f"{_d._first_line(queue[0])}")
        queue.pop(0)
    if not queue:
        CAL_QUEUE_FILE.unlink(missing_ok=True)
        return
    nxt, rest = queue[0], queue[1:]
    # Send this proposal's real buttons now, on promotion — it was queued
    # with a plain no-button notice (see daemon.py's _publish_cal_proposal)
    # specifically so nothing was tappable while a different proposal still
    # occupied CAL_FILE.
    msg_id = _d._tg_send_with_buttons(nxt["proposal_text"], "cal", proposal=nxt)
    if msg_id:
        nxt["source_tg_msg_id"] = msg_id
    CAL_FILE.write_text(json.dumps(nxt))
    if rest:
        CAL_QUEUE_FILE.write_text(json.dumps(rest))
    else:
        CAL_QUEUE_FILE.unlink(missing_ok=True)


def _delete_google_duplicates(spec: dict) -> list[str]:
    """
    After creating our formatted event, find and delete any Google auto-imported
    duplicates at the same start time.

    Google auto-imports have IDs starting with '_' and never use our '<> the user @' format.
    We scan a 10-minute window around the event start and delete matching events —
    but ONLY ones that reference the same venue, since an unscoped underscore-ID
    check can also match unrelated events that happen to share the window (e.g.
    a subscribed holiday/sports calendar entry), which are not duplicates at all.
    """
    start_iso = spec.get("from", "")
    if not start_iso:
        return []

    venue = spec.get("summary", "").split("@ ", 1)[-1].strip().lower()
    if not venue:
        return []

    try:
        start_dt  = datetime.fromisoformat(start_iso)
        window_from = (start_dt - timedelta(minutes=5)).isoformat()
        window_to   = (start_dt + timedelta(minutes=10)).isoformat()
    except Exception:
        return []

    try:
        r = subprocess.run(
            [GOG, "cal", "ls", "--account", EMAIL,
             "--from", window_from, "--to", window_to, "-j", "--max", "50"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return []
        events = json.loads(r.stdout).get("events", [])
    except Exception:
        return []

    deleted = []
    for ev in events:
        ev_id       = ev.get("id", "")
        ev_summary  = ev.get("summary", "")
        ev_location = ev.get("location", "")
        # Google auto-imported events: '_'-prefixed ID, no '<> the user @' in title,
        # and must actually reference our venue to be considered a duplicate.
        is_same_venue = venue in ev_summary.lower() or venue in ev_location.lower()
        if ev_id.startswith("_") and "<> the user @" not in ev_summary and is_same_venue:
            d = subprocess.run(
                [GOG, "cal", "delete", "--account", EMAIL, "primary", ev_id, "--force"],
                capture_output=True, text=True, timeout=30,
            )
            if d.returncode == 0:
                deleted.append(ev_summary)
            else:
                print(f"Warning: could not delete duplicate '{ev_summary}': {d.stderr.strip()}", file=sys.stderr)

    return deleted


def _handle_ics_invite(proposal: dict) -> None:
    """
    Accept a calendar invite and rename our copy to the '<> the user @' format.
    Used when the proposal type is 'ics_invite' (a meeting invite from a person).
    The organiser sees 'accepted'; our calendar shows the correct title.
    """
    import daemon as _d

    ics_uid     = proposal.get("ics_uid", "")
    new_summary = proposal.get("new_summary", "")

    if not ics_uid or not new_summary:
        print("Missing ics_uid or new_summary in proposal.", file=sys.stderr)
        sys.exit(2)

    ok = _d._accept_and_rename_invite(ics_uid, new_summary)
    if not ok:
        print(f"Failed to accept invite UID {ics_uid[:40]}", file=sys.stderr)
        sys.exit(2)

    out(f"Accepted and saved as: {new_summary}")


def _verify_event_exists(event_id: str) -> bool:
    """Confirm an event we just created is actually retrievable from the calendar.

    gog has returned exit 0 on a create that didn't stick before (transient API
    issue never surfaced in stderr), so a clean exit code alone isn't proof.
    """
    try:
        r = subprocess.run(
            [GOG, "cal", "event", "--account", EMAIL, "primary", event_id, "-j"],
            capture_output=True, text=True, timeout=30,
        )
        event = json.loads(r.stdout).get("event", {}) if r.returncode == 0 else {}
        return event.get("id") == event_id and event.get("status") != "cancelled"
    except Exception:
        return False


def _event_is_gone(calendar_id: str, event_id: str) -> bool:
    """True when an event we just deleted is no longer live — either gog can't
    fetch it at all, or it comes back with status 'cancelled'. gog has returned
    exit 0 on a delete that didn't stick, so verify rather than trust the code."""
    try:
        r = subprocess.run(
            [GOG, "cal", "event", "--account", EMAIL, calendar_id or "primary", event_id, "-j"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            error = (r.stderr + r.stdout).lower()
            return any(code in error for code in ("404", "410", "not found", "has been deleted"))
        return json.loads(r.stdout).get("event", {}).get("status") == "cancelled"
    except Exception:
        return False


def _handle_delete(proposal: dict) -> None:
    """A cancellation-driven proposal (daemon._handle_itinerary_cancellation):
    remove the calendar events a now-cancelled booking left behind."""
    events = proposal.get("delete_events", [])
    if not events:
        out("Nothing to remove.")
        return
    removed, failed = [], []
    for ev in events:
        calendar_id = ev.get("calendar_id") or "primary"
        event_id    = ev.get("event_id", "")
        summary     = ev.get("summary", "event")
        if not event_id:
            continue
        d = subprocess.run(
            [GOG, "cal", "delete", "--account", EMAIL, calendar_id, event_id, "--force"],
            capture_output=True, text=True, timeout=30,
        )
        if _event_is_gone(calendar_id, event_id):
            removed.append(summary)
        else:
            failed.append(summary)
            print(f"Warning: could not remove '{summary}': {d.stderr.strip()}", file=sys.stderr)

    if removed:
        out("Removed from your calendar:\n" + "\n".join(f"• {s}" for s in removed))
    if failed:
        print("Could not remove:\n" + "\n".join(f"• {s}" for s in failed))
        sys.exit(2)


def _maybe_upgrade_flight_reminders(gog_events: list) -> None:
    """If this proposal included a hotel check-in/check-out pair, see whether
    any already-scheduled flight (booked before this hotel was confirmed, and
    still running on the tier-based estimate) departs during this stay —
    and if so, retroactively reschedule its leave-for-airport reminder using
    the hotel's real address instead of the generic estimate.

    No-ops silently on anything that isn't a hotel proposal or has nothing to
    upgrade — this is a pure enhancement, never required for correctness."""
    checkin = next((s for s in gog_events if "Check-in" in s.get("summary", "")), None)
    checkout = next((s for s in gog_events if "Check-out" in s.get("summary", "")), None)
    if not checkin or not checkin.get("location"):
        return

    try:
        ci_dt = datetime.fromisoformat(checkin["from"])
        co_dt = datetime.fromisoformat(checkout["from"]) if checkout else (ci_dt + timedelta(days=3650))
    except Exception:
        return

    import daemon as _d
    state = _d._load()
    scheduled = state.get("scheduled_flights", {})
    matches = []
    for conf, entry in scheduled.items():
        if entry.get("home_source") != "tier":
            continue
        try:
            dep_dt = datetime.fromisoformat(entry["dep_iso"])
        except Exception:
            continue
        if ci_dt <= dep_dt <= co_dt:
            matches.append((conf, entry))

    if not matches:
        return

    sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")
    try:
        from drive_time import geocode
        coords = geocode(checkin["location"])
    except Exception as e:
        print(f"Warning: could not geocode hotel for reminder upgrade: {e}", file=sys.stderr)
        return
    if not coords:
        return

    label = checkin["summary"].split(" Check-in")[0].strip() or "Hotel"
    new_home = {"lat": coords[0], "lng": coords[1], "address": checkin["location"], "label": label}

    for conf, entry in matches:
        try:
            _d._cancel_stale_flight_reminders(conf, keep_names=set())
            leave_id = entry.get("leave_event_id")
            if leave_id:
                subprocess.run(
                    [GOG, "cal", "delete", "--account", EMAIL, "primary", leave_id, "--force"],
                    capture_output=True, text=True, timeout=30,
                )
            _d._schedule_flight_reminders(entry["spec"], override_home=new_home)
            out(f"Upgraded flight reminder for confirmation {conf} using {label}.")
        except Exception as e:
            print(f"Warning: could not upgrade flight reminder for {conf}: {e}", file=sys.stderr)


# gog needs all three to create anything; the daemon stores None when it could
# not parse one out of the email. 2026-09-24: a ticket with no date reached the
# ✅ button, subprocess got None in its argv and raised TypeError *after* the
# approval was claimed — which left it stuck in 'executing' and blocked every
# later calendar proposal. Checked before the claim, so a dud can never wedge.
REQUIRED_EVENT_FIELDS = ("summary", "from", "to")


def _unusable_events(proposal: dict) -> list:
    """Human-readable reasons this proposal cannot become a calendar event."""
    problems = []
    for index, spec in enumerate(proposal.get("gog_events") or [], start=1):
        missing = [field for field in REQUIRED_EVENT_FIELDS if not spec.get(field)]
        if missing:
            label = spec.get("summary") or f"event {index}"
            problems.append(f"{label} — no {', '.join(missing)}")
    return problems


def _refuse_unusable(proposal: dict) -> None:
    """Drop an unfillable proposal and say so, without claiming its approval."""
    problems = _unusable_events(proposal)
    if not problems:
        return
    print("Nothing was added, Master Wayne — this proposal has no usable date, so it "
          "was never something a calendar could hold: " + "; ".join(problems) +
          ". The email it came from did not carry one. I have cleared it.",
          file=sys.stderr)
    CAL_FILE.unlink(missing_ok=True)
    _promote_next_queued()
    sys.exit(2)


def main():
    if not CAL_FILE.exists():
        print("No pending calendar event.")
        sys.exit(1)

    proposal = json.loads(CAL_FILE.read_text())
    ordinary = proposal.get("type") != "ics_invite" and proposal.get("action") != "delete"
    if ordinary and not proposal.get("gog_events"):
        print("Nothing was added, Master Wayne — that proposal carried no event at all. "
              "I have cleared it; ask me to build it again.", file=sys.stderr)
        CAL_FILE.unlink(missing_ok=True)
        _promote_next_queued()
        sys.exit(2)
    if ordinary:
        _refuse_unusable(proposal)
    approval_id = require_cli("cal", "pending_cal_event", proposal)

    # ── ICS invite (meeting from a person) — accept + rename, don't create ──
    if proposal.get("type") == "ics_invite":
        _handle_ics_invite(proposal)
        finish(approval_id, "invite processed")
        CAL_FILE.unlink(missing_ok=True)
        _promote_next_queued()
        _finish(proposal, remove_card=True)
        return

    # ── Cancellation cleanup — remove events a cancelled booking left behind ──
    if proposal.get("action") == "delete":
        _handle_delete(proposal)
        finish(approval_id, "cancellation processed")
        CAL_FILE.unlink(missing_ok=True)
        _promote_next_queued()
        _finish(proposal)
        return

    # ── All other types — create new '<> the user @' event ──
    gog_events = proposal.get("gog_events", [])
    if not gog_events:
        print("No events in proposal.")
        CAL_FILE.unlink(missing_ok=True)
        _promote_next_queued()
        sys.exit(1)

    created  = []
    cleaned  = []
    for spec in gog_events:
        # `or`, not a .get default: the daemon stores an explicit None when no
        # drive-time reminder applies, which would otherwise become "popup:Nonem".
        reminder_min = spec.get("reminder_minutes") or 15
        all_day = bool(spec.get("all_day"))
        cmd = [
            GOG, "cal", "create", EMAIL,
            "--account",        EMAIL,
            "--summary",        spec["summary"],
            "--from",           spec["from"],
            "--to",             spec["to"],
            # gog refuses a timezone alongside date-only bounds, so an all-day
            # proposal that passed them would fail at the moment the user taps Approve.
            *([] if all_day else [
                "--start-timezone", spec.get("start_timezone", "America/Chicago"),
                "--end-timezone",   spec.get("end_timezone",   "America/Chicago"),
            ]),
            "--reminder",       f"popup:{reminder_min}m",
        ]
        if spec.get("location"):
            cmd += ["--location", spec["location"]]
        if spec.get("description"):
            cmd += ["--description", spec["description"]]
        # ALL-DAY AND FREE/BUSY (2026-10-09): both keys are optional, so every
        # proposal written before today builds the identical command. Without
        # these two lines a proposal that asked to be all-day was approved and
        # silently created as a timed midnight-to-midnight event — which is how
        # a weekend trip lost its Sunday.
        if all_day:
            cmd += ["--all-day"]
        if spec.get("transparency"):
            cmd += ["--transparency", spec["transparency"]]
        cmd += ["-j"]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"Failed: {spec['summary']}: {r.stderr.strip()}", file=sys.stderr)
            sys.exit(2)

        try:
            event_id = json.loads(r.stdout).get("event", {}).get("id", "")
        except Exception:
            event_id = ""

        if not event_id or not _verify_event_exists(event_id):
            print(
                f"Failed: {spec['summary']}: gog reported success but the event "
                f"could not be verified on the calendar afterward.",
                file=sys.stderr,
            )
            sys.exit(2)

        created.append(spec["summary"])

        if spec.get("type") == "flight":
            try:
                import daemon as _d
                _d._schedule_flight_reminders(spec)
                out(f"Flight reminders scheduled for {spec['summary']}.")
            except Exception as e:
                print(f"Warning: flight reminders failed: {e}", file=sys.stderr)

        cleaned += _delete_google_duplicates(spec)

    _maybe_upgrade_flight_reminders(gog_events)

    # Decline+delete the practice's ICS invite if one was stored in the proposal
    # (practice-management / appointment booking systems).
    ics_uid = proposal.get("ics_uid", "")
    if ics_uid:
        try:
            import daemon as _d
            if _d._decline_and_delete_invite(ics_uid):
                out("Removed practice-created calendar event.")
        except Exception as e:
            print(f"Warning: could not decline ICS invite: {e}", file=sys.stderr)

    finish(approval_id, "created:" + ", ".join(created))
    CAL_FILE.unlink(missing_ok=True)
    _promote_next_queued()

    if len(created) == 1:
        out(f"Added to your calendar: {created[0]}")
    else:
        out("Added to your calendar:\n" + "\n".join(f"• {s}" for s in created))

    if cleaned:
        out("Removed Google auto-imports:\n" + "\n".join(f"• {s}" for s in cleaned))

    # `created` is only appended to after _verify_event_exists, so reaching here means the event
    # really is on the calendar — the one condition the user put on deleting the card.
    _finish(proposal, remove_card=bool(created))


if __name__ == "__main__":
    main()
