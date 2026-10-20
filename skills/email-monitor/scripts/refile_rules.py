"""The user's filing rule as an ordered, first-match-wins rule list.

Principle (2026-09-17): file by WHAT an email is, not who sent it — one email,
one folder. Order: packages, receipts, documents, codes/login/fraud notices — from ANY
sender, banks included — then one folder per account or area. Rules live in ../refile_spec.json so the one-time
re-file, the archive backfill and the daemon's arrival filing share one source.

A rule:
    {"id": "...", "to": "<label>" | "@archive" | "@action",
     "from": ["exact@address"], "from_re": "...", "text_re": "...",
     "subject_re": "...", "not_subject_re": "..."}
All given conditions must hold. `text_re` searches "<display name> <subject>".
"@archive" = leave the inbox with no folder (promotions, surveys).
"@action"  = the Action Needed folder.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

SPEC = Path(__file__).resolve().parent.parent / "refile_spec.json"
ACTION_LABEL = "Action Needed"
ARCHIVE, ACTION = "@archive", "@action"
# Never treated as folders: Gmail's own markers.
SYSTEM_PREFIXES = ("CATEGORY_", "IMPORTANT", "UNREAD", "STARRED", "SENT", "INBOX", "DRAFT",
                   "SPAM", "TRASH", "CHAT", "YELLOW_STAR")
_KEYS = {"id", "to", "from", "from_re", "text_re", "subject_re", "not_subject_re", "_why"}


def load_rules(path: Path = SPEC) -> list:
    spec = json.loads(Path(path).read_text())
    patterns = spec.get("patterns", {})
    rules = []
    for raw in spec["rules"]:
        unknown = set(raw) - _KEYS
        if unknown:
            raise ValueError(f"rule {raw.get('id')}: unknown keys {sorted(unknown)}")
        if not raw.get("id") or not raw.get("to"):
            raise ValueError(f"rule needs id and to: {raw}")
        rule = dict(raw)
        rule["from"] = [a.lower() for a in raw.get("from", [])]
        for key in ("from_re", "text_re", "subject_re", "not_subject_re"):
            if raw.get(key):
                rule[key] = re.compile(patterns.get(raw[key], raw[key]), re.I)
        if not (rule["from"] or rule.get("from_re") or rule.get("text_re") or rule.get("subject_re")):
            raise ValueError(f"rule {rule['id']}: no condition")
        rules.append(rule)
    ids = [r["id"] for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate rule ids")
    return rules


def matches(rule: dict, addr: str, name: str, subject: str) -> bool:
    addr = (addr or "").lower()
    if rule["from"] and addr not in rule["from"]:
        return False
    if rule.get("from_re") and not rule["from_re"].search(addr):
        return False
    if rule.get("text_re") and not rule["text_re"].search(f"{name or ''} {subject or ''}"):
        return False
    if rule.get("subject_re") and not rule["subject_re"].search(subject or ""):
        return False
    if rule.get("not_subject_re") and rule["not_subject_re"].search(subject or ""):
        return False
    return True


# Automated mail that must not be filed out of sight, whatever folder its rule
# names. Shared by the re-file planner and arrival filing so they cannot disagree.
NEEDS_YOU_RE = re.compile(
    r"action (required|needed|advised)|needs your attention|respond by|deadline|final notice|past due|"
    r"payment (failed|declined|unsuccessful)|could not (process|be processed)|claim (your|by)|"
    r"expires? (on|in|soon)|will be (cancel+ed|closed|suspended)|has been suspended", re.I)
NEEDS_YOU_RULE = {"id": "needs-you", "to": ACTION, "from": []}

AUTOMATED_ADDRESS_RE = re.compile(
    r"(^|[._+-])(no-?reply|do-?not-?reply|donotreply|notifications?|alerts?|mailer|billing|receipts?|"
    r"news(letter)?|updates?|marketing|auto[_-]?reply)([._+-]|@)|@(e|em|mail|info|email|news|notify)\d*\.", re.I)
BULK_CATEGORIES = {"CATEGORY_UPDATES", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}


def is_automated(addr: str, label_ids, has_list_unsubscribe: bool) -> bool:
    """Machine-sent mail. Anything else is treated as a person writing to the user."""
    return bool(has_list_unsubscribe or BULK_CATEGORIES & set(label_ids or ())
                or AUTOMATED_ADDRESS_RE.search(addr or ""))


def decide(rules: list, addr: str, name: str, subject: str, automated: bool = True,
           needs_you: bool = True) -> Optional[dict]:
    """The first matching rule, or None (leave the message exactly where it is).

    Rules that name no sender (the subject-only "type" rules) never apply to
    mail from a person: someone forwarding "Security alert" or writing "Re:
    receipt" is correspondence, and correspondence stays in the inbox.
    """
    found = None
    for rule in rules:
        if not automated and not (rule["from"] or rule.get("from_re")):
            continue
        if matches(rule, addr, name, subject):
            found = rule
            break
    # A deadline outranks the sender's folder — unless the sender is known junk,
    # whose "final notice!" is marketing urgency.
    # Only for mail still in the inbox (needs_you=False when re-filing old mail):
    # Action Needed holds what is current, not a months-old "needs your attention".
    if needs_you and automated and NEEDS_YOU_RE.search(subject or "") and not (found and found["to"] == ARCHIVE):
        return NEEDS_YOU_RULE
    return found


def user_labels(label_names) -> list:
    return [n for n in label_names if not n.startswith(SYSTEM_PREFIXES)]


def plan_message(rule: Optional[dict], label_names: list, in_inbox: bool, starred: bool) -> Optional[dict]:
    """Label changes for one message, or None when nothing should change.

    One email, one folder: every other folder label comes off. A starred
    message is the user's "keep this" signal and is never touched. Filed mail
    leaves the inbox; mail already out of the inbox is never put back.
    """
    if rule is None or starred or ACTION_LABEL in label_names:
        return None          # Action Needed is the user's list: only he (or the item's resolution) clears it
    target = {ARCHIVE: None, ACTION: ACTION_LABEL}.get(rule["to"], rule["to"])
    current = user_labels(label_names)
    add = [target] if target and target not in current else []
    remove = [n for n in current if n != target]
    if in_inbox:
        remove.append("INBOX")
    if not add and not remove:
        return None
    return {"add": add, "remove": remove, "rule": rule["id"]}
