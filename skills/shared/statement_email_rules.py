"""Pure sender/subject rules shared by finance intake and email ownership.

Availability notices only: each subject belongs to its own provider domain.
These rules do not treat an email as a statement or establish its coverage.
"""
from __future__ import annotations

from email.utils import parseaddr
import re


RULES = {
    # Provider names and domains are placeholders in this public copy; the live
    # deployment lists the owner's real institutions here. Each entry pins the
    # provider's OWN sender domain and its exact availability subject.
    "card_a": {
        "name": "Card A",
        "domains": ("carda.example",),
        "subject_re": r"^Your statement is now available online[.!]?$",
        "query": 'from:carda.example subject:"Your statement is now available online"',
    },
    "card_b": {
        "name": "Card B",
        "domains": ("cardb.example",),
        "subject_re": r"^Your Card B Credit Card statement is ready[.!]?$",
        "query": 'from:cardb.example subject:"Your Card B Credit Card statement is ready"',
    },
    "broker_a": {
        "name": "Broker A",
        "domains": ("brokera.example",),
        "subject_re": r"^Your Broker A statement is ready[.!]?$",
        "query": 'from:brokera.example subject:"Your Broker A statement is ready"',
    },
    "hsa_a": {
        "name": "HSA A",
        "domains": ("hsaa.example",),
        "subject_re": r"^Your HSA statement is now available[.!]?$",
        "query": 'from:hsaa.example subject:"Your HSA statement is now available"',
    },
    # 2026-10-02: two card issuers' statement-ready mail was never parsed, so those
    # statements could never be requested. Subjects verified against the real mail.
    "card_c": {
        "name": "Card C",
        "domains": ("cardc.example",),
        "subject_re": r"^Your [A-Za-z]+ \d{4} statement is ready(?: [–-] please review important notices)?[.!]?$",
        "query": 'from:cardc.example subject:"statement is ready"',
    },
    # 2026-10-07: this brokerage sent exactly ONE availability notice in 180 days and
    # nothing watched for it, so the only signal that a quarterly statement existed
    # went unseen. Subject and sender verified against the real mail.
    # The notice carries NO balance and NO attachment — it names the period and the
    # account and says to log in — so this buys a tracked request, never a live feed.
    "broker_b": {
        "name": "Broker B",
        "domains": ("brokerb.example",),
        "subject_re": r"^New account statements and disclosures available[.!]?$",
        "query": 'from:brokerb.example subject:"New account statements and disclosures available"',
    },
    "card_d": {
        "name": "Card D",
        "domains": ("cardd.example",),
        "subject_re": r"^Your credit card statement is available[.!]?$",
        "query": 'from:cardd.example subject:"Your credit card statement is available"',
    },
}


def identify_provider(address: str, subject: str) -> str | None:
    """Match a provider's own sender domain and exact availability subject."""
    mailbox = parseaddr(address or "")[1].lower()
    if "@" not in mailbox:
        return None
    domain = mailbox.rpartition("@")[2]
    subject = " ".join((subject or "").split())
    for provider, rule in RULES.items():
        if (any(domain == allowed or domain.endswith("." + allowed)
                for allowed in rule["domains"])
                and re.fullmatch(rule["subject_re"], subject, flags=re.I)):
            return provider
    return None


def gmail_query(providers, after: int) -> str:
    """Build one bounded Gmail query from explicitly enabled providers."""
    if isinstance(providers, str):
        raise ValueError("Providers must be a sequence of provider names")
    providers = list(dict.fromkeys(providers))
    if not providers or any(provider not in RULES for provider in providers):
        raise ValueError("At least one known statement provider is required")
    if isinstance(after, bool) or not isinstance(after, int) or after < 0:
        raise ValueError("after must be a nonnegative epoch timestamp")
    terms = " OR ".join(f'({RULES[provider]["query"]})' for provider in providers)
    return f"({terms}) after:{after}"
