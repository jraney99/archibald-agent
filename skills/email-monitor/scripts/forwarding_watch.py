"""Gmail forwarding alarm.

Adding a forwarding address and switching on auto-forwarding silently copies
every future email to someone else — the most damaging thing a tricked agent
(or anyone who got into the Google account) could do. Archibald's gog sign-in
holds the gmail.settings.sharing scope that allows it, and gog can't drop that
scope without breaking sending, so outbound-guard blocks the commands and this
alarm catches whatever gets past it (built 2026-09-17, ✅ M1).

The email daemon calls these helpers every CHECK_INTERVAL_S. Its own token
(gmail.modify + gmail.settings.basic) can read, but not change, these settings.

State (daemon state file, key STATE_KEY):
    last_check          epoch seconds of the last attempt
    signature           canonical snapshot last reported ("" = clean)
    alerted_at          when that non-clean snapshot was last reported
    failures            consecutive failed reads
    failure_alerted_at  when a read-failure alert was last sent
"""
from __future__ import annotations

import html
import json
from typing import Optional

CHECK_INTERVAL_S = 900           # every 15 minutes
REALERT_S = 86400                # remind daily while forwarding stays on
FAILURE_ALERT_AFTER = 3          # consecutive failed reads (~45 min) before alerting
STATE_KEY = "forwarding_watch"
SOURCE = "gmail:forwarding"

FIX_STEPS = ("If you didn't set this up: in Gmail go to Settings → Forwarding and POP/IMAP, "
             "remove the address and turn forwarding off, then change your Google password "
             "and sign out other sessions. Archibald can't undo this for you.")


def read_settings(svc) -> dict:
    """Snapshot of forwarding addresses and auto-forwarding (read-only API calls)."""
    settings = svc.users().settings()
    listed = settings.forwardingAddresses().list(userId="me").execute() or {}
    auto = settings.getAutoForwarding(userId="me").execute() or {}
    return snapshot(listed.get("forwardingAddresses") or [], auto)


def snapshot(addresses: list, auto: dict) -> dict:
    return {
        "addresses": sorted(
            ({"email": str(a.get("forwardingEmail", "")).lower(),
              "status": str(a.get("verificationStatus", ""))} for a in addresses),
            key=lambda a: (a["email"], a["status"])),
        "auto_enabled": bool(auto.get("enabled")),
        "auto_email": str(auto.get("emailAddress") or "").lower(),
        "disposition": str(auto.get("disposition") or ""),
    }


def is_clean(snap: dict) -> bool:
    return not snap["addresses"] and not snap["auto_enabled"]


def signature(snap: dict) -> str:
    return "" if is_clean(snap) else json.dumps(snap, sort_keys=True)


def due(watch: dict, now: float) -> bool:
    return now - float(watch.get("last_check", 0)) >= CHECK_INTERVAL_S


_STATUS = {"accepted": "confirmed", "pending": "waiting for confirmation"}
_DISPOSITION = {
    "leaveInInbox": "keeps a copy in your inbox",
    "archive": "archives your copy",
    "trash": "deletes your copy",
    "markRead": "marks your copy read",
}


def alert_text(snap: dict) -> str:
    lines = ["🚨 <b>Your Gmail is set up to forward mail to someone else.</b>"]
    for a in snap["addresses"]:
        status = _STATUS.get(a["status"], a["status"] or "unknown status")
        lines.append(f"• Forwarding address: {html.escape(a['email'])} ({html.escape(status)})")
    if snap["auto_enabled"]:
        what = _DISPOSITION.get(snap["disposition"], snap["disposition"] or "")
        lines.append(f"• Auto-forwarding is ON → {html.escape(snap['auto_email'] or 'unknown address')}"
                     + (f" ({html.escape(what)})" if what else ""))
    lines.append("")
    lines.append(FIX_STEPS)
    return "\n".join(lines)


CLEAR_TEXT = "✅ Gmail forwarding is off again — no forwarding addresses, auto-forwarding off."


def evaluate(snap: dict, watch: dict, now: float) -> tuple[Optional[str], dict]:
    """(message to send or None, new watch state) for a successful read."""
    new = {**watch, "last_check": now, "failures": 0}
    sig = signature(snap)
    previous = watch.get("signature", "")
    if not sig:
        new["signature"] = ""
        new.pop("alerted_at", None)
        return (CLEAR_TEXT if previous else None), new
    if sig == previous and now - float(watch.get("alerted_at", 0)) < REALERT_S:
        return None, new
    new["signature"] = sig
    new["alerted_at"] = now
    return alert_text(snap), new


def record_failure(watch: dict, error: str, now: float) -> tuple[Optional[str], dict]:
    """(message or None, new watch state) for a failed read."""
    failures = int(watch.get("failures", 0)) + 1
    new = {**watch, "last_check": now, "failures": failures}
    last_alert = watch.get("failure_alerted_at")
    if failures >= FAILURE_ALERT_AFTER and (last_alert is None or now - float(last_alert) >= REALERT_S):
        new["failure_alerted_at"] = now
        return (f"⚠️ The Gmail forwarding alarm couldn't read your forwarding settings "
                f"{failures} times in a row, so it isn't protecting you right now. "
                f"Latest error: {html.escape(error[:200])}"), new
    return None, new
