#!/usr/bin/env python3
"""The user's email relevance policy — the single source of truth.

Imported by every component that decides what happens to a message or a
sender, so none of them can drift from the others:

  daemon.py                runtime triage (inbox loop + labeled-out backstop)
  sender_filter.py         one-tap filter / unsubscribe proposals
  email_filter_audit.py    read-only live audit of Gmail filters
  filter_migration_plan.py broad-rule → exact-address migration planner

Policy (The user, 2026-09-15):
  1. Surface mail only when it directly affects security, account access,
     money, investments, required actions, active travel, appointments,
     health, home, purchases, services, or personal relationships.
  2. Surveys, feedback requests, promotions, newsletters, generic education,
     product announcements, cross-sells and engagement prompts are clutter.
  3. A relationship with the company is not enough.
  4. Permanent filters use exact sender addresses; the only broad rule allowed
     is @substack.com → Trash.
  5. Before recommending a permanent rule, inspect up to 30 messages over 90
     days and require at least two consistent examples. Homogeneous records →
     label/archive; homogeneous marketing → unsubscribe/Trash; security,
     mixed, unknown or insufficient history → message-level review.
  6. Financial security/fraud/transactions/statements/trades/payment failures
     and account changes must surface; marketing and surveys must not.
  7. Never remove Gmail's SPAM label.
  8. Inactive-provider mail is irrelevant unless it raises a security concern.
  9. Flight proposals need flight number, origin, destination, departure and
     arrival (enforced in daemon._build_flight_event).

Pure module: no network or disk access at import time.
"""
from __future__ import annotations

import json
import importlib
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

_SHARED = str(Path(__file__).resolve().parents[2] / "shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
import statement_email_rules as _statement_rules

# The email daemon is long lived while the finance watcher is a fresh cron
# process.  Importing RULES once caused newly enabled statement providers to be
# surfaced as raw FYIs until the daemon happened to restart.  Refresh this pure
# module when its source changes so both processes use the same rules without a
# service restart.  A failed/partial reload keeps the last known-good rules.
STATEMENT_RULES = _statement_rules.RULES  # compatibility for policy/reporting callers
_STATEMENT_RULES_SIGNATURE = None


def _live_statement_rules():
    global _statement_rules, STATEMENT_RULES, _STATEMENT_RULES_SIGNATURE
    try:
        path = Path(_statement_rules.__file__).resolve()
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if _STATEMENT_RULES_SIGNATURE is None:
            _STATEMENT_RULES_SIGNATURE = signature
        elif signature != _STATEMENT_RULES_SIGNATURE:
            importlib.invalidate_caches()
            refreshed = importlib.reload(_statement_rules)
            rules = getattr(refreshed, "RULES", None)
            if not isinstance(rules, dict) or not rules:
                raise ValueError("statement rules reload produced no rules")
            _statement_rules = refreshed
            STATEMENT_RULES = rules
            _STATEMENT_RULES_SIGNATURE = signature
    except (OSError, ImportError, ValueError, TypeError):
        pass
    return STATEMENT_RULES


def _identify_statement_provider(address: str, subject: str) -> Optional[str]:
    _live_statement_rules()
    return _statement_rules.identify_provider(address, subject)

# ── Message classes ──────────────────────────────────────────────────────────
# Classification is by subject (plus Gmail's own category labels) so it is
# cheap, deterministic, and identical wherever it runs. Priority order is
# security > survey/upsell > material > marketing > unknown, so a subject that
# carries any security signal can never be treated as clutter.

KINDS = ("security", "material", "marketing", "unknown")

SECURITY_SUBJECT_RE = re.compile(
    r'(?i)('
    # fraud / unauthorised use
    r'unusual (sign-?in |login |account )?activity|'
    r'suspicious (activity|transaction|charge|payment|login attempt)|'
    r'fraud(ulent)? (activity|charge|transaction|attempt|detected)|fraud alert|'
    r'dark web monitor alert|data (exposure|breach) detected|'
    r'did you (make|try to make|attempt|authori[sz]e) (this|a) '
    r'(charge|transaction|purchase|payment|transfer)|'
    r'was this (purchase|charge|transaction|you\?)|'
    r'verify (it was you|this (charge|transaction|purchase|payment|transfer))|'
    r'unauthori[sz]ed (charge|transaction|transfer|access|activity|login attempt|sign-?in)|'
    r'we (noticed|detected|have detected|placed a hold on|have locked)|'
    # payment failures (rule 6)
    r'payment (failed|failure|was declined|could not be processed|unsuccessful)|'
    r'declined (payment|transaction|charge|card)|'
    r'(card|payment|transaction|charge) (was |has been )?declined|'
    r'recent declined|'
    # account access
    r'security (alert|notification|notice|warning|code|check-?up)|'
    r'your (card|account) (was |has been |is )?(now )?'
    r'(locked|suspended|frozen|compromised|restricted|disabled|deactivated)|'
    r'card is now (locked|frozen)|'
    r'(password|passcode|pin) (was |has been )?(changed|reset|updated)|'
    r'(changed|reset|updated) your ([\w-]+ ){0,2}(password|passcode|pin)\b|'
    r'tried to (reset|change|access|sign in|log in)|'
    r'someone (tried|attempted|is trying)|'
    r'account (was |has been )?(recovered|taken over)|'
    r'account recovery|recovery (email|phone|code|information|info)|'
    r'recent (sign-?in|login)|signed in with|'
    r'new [\w .&-]{0,40}(login|sign-?in) (on|from|to)|new (sign-?in|login)\b|'
    r'(sign(ed)?|log(ged)?)[ -]?in (from|with) (a )?(new|unrecognized|unrecognised) device|'
    r'\bnew device\b|(unrecognized|unrecognised) device|'
    r'verify your (device|identity|email|account|login|sign-?in)|'
    r'(making sure|confirm|verify) (it.?s|it is|that.?s) you|is this you\b|'
    r'(two.factor|2fa|mfa|multi.factor|2-?step|two-step|recovery (email|phone)) '
    r'(was |has been )?(changed|disabled|updated|turned off)|'
    r'verification code|one-time (pass)?code|sign-?in code|login code|'
    # access grants and custody-affecting changes
    r'api key|app password|access token|'
    r'share your data with|(authori[sz]ed|connected) a new (app|application|device)|'
    r'withdrawal address|new (payout|withdrawal|transfer) (address|destination|method)|'
    r'withdrawal (initiated|requested|pending|sent|submitted)|'
    r'(added|linked|removed) (an? )?(new |external )?(bank account|external account|payee|recipient|beneficiary)|'
    r'(updated|added to|removed from) your digital wallet|'
    r'(email address|phone number|mobile number) (was |has been )?changed|'
    r'confirmation of [\w® .]{0,30}(app|online|account) activity|'
    r'(many |multiple )?files were (recently )?deleted|deleted from your'
    r')'
)

# A survey or upsell is clutter even when it names something material ("Work
# Order Survey", "Add bags now and save on your flight to Dallas"). Checked
# after security and before material.
SURVEY_OR_UPSELL_RE = re.compile(
    r'(?i)('
    r'\bsurveys?\b|feedback|how (was|did we do|would you rate)\b|how many stars|'
    r'rate (your|us|our)\b|tell us (what|how|about)|'
    r'share your (experience|thoughts|opinion)|leave (us )?a review|write a review|'
    r'review your (recent )?(visit|stay|order|purchase|experience)|'
    r'add bags|save on your|get the seat|choose your seat|seat (upgrade|bid)|'
    r'upgrade (your|to)\b|refer (a )?friends?|invite (your )?friends'
    r')'
)

MATERIAL_SUBJECT_RE = re.compile(
    r'(?i)('
    # statements, documents, tax (rule 6) — "statement" needs account context,
    # never an adjective ("STATEMENT DOORMATS for LESS")
    r"(your|monthly|quarterly|annual|account|card|hsa|brokerage|bank|billing|\d{4})"
    r" ([\w®'-]+ ){0,4}statements?\b|"
    r'\bstatements?\b( [\w-]+){0,3} (is |are )?(now )?(ready|available|posted|attached)|'
    r'\bstatements? (for|you requested)\b|'
    r'superbill|\binvoice\b|\breceipt\b|\bbilling\b|'
    r'your bill\b|bill (is )?(ready|available|due)|'
    r'(new |brokerage |tax )?documents? (is |are )?(now )?(ready|available|to (sign|review))|'
    r'tax (document|form)|\b1099\b|\bw-2\b|\bk-1\b|'
    r'please (docusign|sign|review and sign)|completed: |signature (requested|required|needed)|'
    # money movement
    r'transactions? (was |were )?(made|posted|approved|completed)|'
    r'transaction (alert|receipt)|purchase approved|large purchase|'
    r'\bpaid you\b|^you paid\b|you (sent|received) money|money (sent|received)|'
    r'payments? (received|processed|posted|scheduled|confirmation|confirmed|is processing|'
    r'is due|due|reminder|is coming up|coming up|was successful|successful)|'
    r'(received|processed|got) your payment|thanks for your payment|'
    r'payments? (are|is) (now )?automatic|auto-?pay(ment)?|automatic payments?|'
    r'automated payment|scheduled a [\w() ]{0,20}payment|due date|minimum payment|past due|'
    r'(zelle|venmo|paypal)\W.{0,30}(payment|money)|'
    r'deposit (was |has been )?(posted|received|completed|made|available|failed|returned)|'
    r'(mobile|direct) deposit|your deposit|'
    r'(withdrawal|transfer) (was |has been )?(posted|received|completed|processed|failed)|'
    r'funds (are )?available|refund|reimbursement|'
    # investments (rule 6)
    r'trade confirmation|trade (executed|confirmed)|order (was )?(filled|executed)|'
    r'you (bought|sold)\b|dividend|distribution|corporate action|proxy vote|'
    r'margin call|liquidation|redemption|maturity|'
    # account changes
    r'account (was |has been )?(linked|approved|opened|closed|created)|'
    r'card is approved|credit card is approved|welcome to your .{0,40}(card|account)|'
    r'card is (almost there|on (its|the) way)|paperless|notifications enabled|'
    r'(promotional )?credits? (was |were |has been |have been )?(restored|extended)|'
    # purchases and deliveries
    r'(order|package|shipment|parcel|delivery)\b.{0,40}\b(confirmed|shipped|delivered|'
    r'out for delivery|on the way|arriving|ready for pickup|delayed)|'
    r'order #?\w*\d\w* (confirmed|shipped|delivered)|order confirmation|'
    r'(upcoming|your) (subscription )?order\b|you.?ve got a package|out for delivery|'
    r'has shipped|(was|has been) delivered|'
    # home and services
    r'work order|maintenance (request|scheduled)|towing|'
    r'registration requirements|\blease\b|rent (is )?(due|reminder|receipt|payment)|'
    r'\boutage\b|renewal|action (required|needed|recommended)|'
    r'needs your attention|requires? your (action|attention)|'
    r'(trial|plan|subscription|membership|policy) (is )?(ending|expiring|renewing|renewed)|'
    # travel, reservations, appointments
    r'get ready for your (return )?trip|what you.ll need for your .{0,40} trip|'
    r'(trip|ride) with (uber|lyft)\b|'
    r'(trip|stay|flight|reservation|booking|itinerary)\b.{0,40}\b(confirm|confirmed|'
    r'confirmation|change|changed|updated|modified|cancel|cancelled|canceled|'
    r'cancellation|reminder|almost here|delayed)|'
    r'(reservation|booking|flight) (confirmation|cancellation|change)|'
    r'check in for|boarding pass|gate change|\bstay at\b|you.?re (going|flying) to|'
    r'(upcoming )?appointment (is |was |has been )?'
    r'(reminder|confirmed|updated|rescheduled|cancelled|canceled|booked|scheduled)|'
    r'appointment .{0,40}(reminder|booked)|is booked\b|booked for\b|'
    # health
    r'prescription|\brefill\b|\brx\b|lab results|test results|results are (in|ready|available)'
    r')'
)

MARKETING_SUBJECT_RE = re.compile(
    r'(?i)('
    r'\bsale\b|\boffers?\b|save \d|% off|\bdeals?\b|rewards faster|rewards (recap|summary)|'
    r'looking for your first|now available|newsletter|\bdigest\b|shop now|\bshop\b|'
    r'limited time|exclusive|refer a friend|\bpromo(tion)?s?\b|coupon|discount|'
    r'free (samples?|trial|shipping|gift)|\bbonus\b|earn (up to|\d|points|miles|rewards|cash)|'
    r'top movers?|what you missed|what.?s new|new features?|introducing|a new way to|'
    r'coming soon|see what.?s coming|don.?t miss|last chance|ends (tonight|today|soon)|'
    r'today only|\bwebinar\b|\btips\b|how to\b|learn (more|how)|\bexplore\b|\bdiscover\b|'
    r'recommendations|we miss you|come back|forgot about|haven.?t seen you|'
    r'special offers?|join (today|now|us)|sign up|register now|new arrivals|'
    r'fundraiser|donate|giveaway|sweepstakes|chance to win|weekly (report|update|recap|roundup)|'
    r'don.?t let your .{0,30}(expire|go unused|go to waste)|'
    r'start (using|making|earning|saving)|🔥'
    r')'
)

# Material updates that notify in real time (the pre-expansion material set).
# Other material mail stays visible but is batched into the 6pm FYI digest so a
# wider classifier never turns receipts and deliveries into instant pings.
REALTIME_MATERIAL_RE = re.compile(
    r'(?i)\b('
    r'(monthly |quarterly |annual )?'
    r'((account|hsa|brokerage|credit card) )?statement (is |was )?(now )?(ready|available)|'
    r'trade confirmation|order (was )?(filled|executed)|'
    r'dividend|distribution|corporate action|proxy vote|'
    r'(deposit|withdrawal|transfer) (was |has been )?(posted|received|completed|failed)|'
    r'funds (are )?available|account (was |has been )?(linked|approved)|'
    r'(promotional )?credits? (was |were |has been |have been )?(restored|extended)|'
    r'get ready for your (return )?trip|what you.ll need for your .{0,40} trip|'
    r'your .{0,40} trip .{0,20}(almost here|updated|changed)|'
    r'(upcoming )?appointment (is |was |has been )?'
    r'(reminder|confirmed|updated|rescheduled|cancelled|canceled)|'
    r'tax (document|form)|1099|k-1|margin call|liquidation|redemption|maturity'
    r')\b'
)


# A survey or form the user is REQUIRED to complete (clinical intake, pre-visit
# paperwork) is a required action, not clutter — it outranks the survey rule.
REQUIRED_ACTION_RE = re.compile(
    r'(?i)(action required|required (form|survey|questionnaire|paperwork)|'
    r'pre-?visit|pre-?appointment|intake (form|questionnaire|paperwork)|'
    r'before your (appointment|visit|procedure)|'
    r'complete (your|the) (intake|registration|paperwork|check-?in))'
)


def normalize_subject(subject: str) -> str:
    """Fold look-alike characters and strip invisible ones before matching.

    NFKC maps fullwidth and compatibility forms ("Ｓｅｃｕｒｉｔｙ") to plain
    text, and removing format characters defeats zero-width evasion
    ("Sec\u200burity alert"). Cross-script homoglyphs (a Cyrillic "е") are NOT
    folded by this; treat subject classification as a routing hint, never as
    proof of who sent a message.
    """
    text = unicodedata.normalize("NFKC", subject or "")
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def classify_subject(subject: str, labels=None) -> str:
    """Return security, material, marketing, or unknown in priority order."""
    subject = normalize_subject(subject)
    if SECURITY_SUBJECT_RE.search(subject):
        return "security"
    if REQUIRED_ACTION_RE.search(subject):
        return "material"
    if SURVEY_OR_UPSELL_RE.search(subject):
        return "marketing"
    if MATERIAL_SUBJECT_RE.search(subject):
        return "material"
    if "CATEGORY_PROMOTIONS" in set(labels or []) or MARKETING_SUBJECT_RE.search(subject):
        return "marketing"
    return "unknown"


def should_surface(subject: str, labels=None):
    """True/False for deterministic cases; None delegates ambiguous mail to L2."""
    kind = classify_subject(subject, labels)
    if kind in ("security", "material"):
        return True
    if kind == "marketing":
        return False
    return None


def is_realtime(kind: str, subject: str) -> bool:
    """Security always pings immediately; material only for the realtime set."""
    return kind == "security" or (
        kind == "material" and bool(REALTIME_MATERIAL_RE.search(subject or "")))


# ── Rule evidence (policy 5) ────────────────────────────────────────────────

HISTORY_DAYS = 90
HISTORY_CAP = 30
MIN_CONSISTENT = 2

ARCHIVE, TRASH, MESSAGE_LEVEL, INSUFFICIENT = (
    "archive", "trash", "message_level", "insufficient")


def empty_counts() -> dict:
    return {kind: 0 for kind in KINDS}


def recommend_rule(counts: dict) -> tuple[str, str]:
    """Map a sender's sampled history to a rule recommendation and reason.

    archive       every sample is a record (material) — at least two of them
    trash         every sample is marketing — at least two of them
    message_level any security sample, any mixture, or any unknown sample
    insufficient  fewer than two samples
    """
    c = {kind: int(counts.get(kind, 0)) for kind in KINDS}
    total = sum(c.values())
    if c["security"]:
        return MESSAGE_LEVEL, (f"{c['security']} security message(s) in history — "
                               "never blanket-filter a security sender")
    if total < MIN_CONSISTENT:
        return INSUFFICIENT, f"only {total} message(s) in {HISTORY_DAYS} days"
    if c["marketing"] == total:
        return TRASH, f"all {total} sampled messages are marketing"
    if c["material"] == total:
        return ARCHIVE, f"all {total} sampled messages are records"
    parts = [f"{c[k]} {k}" for k in ("material", "marketing", "unknown") if c[k]]
    return MESSAGE_LEVEL, "mixed history: " + ", ".join(parts)


# ── Permanent-filter scope (policy 4) ───────────────────────────────────────

ALLOWED_BROAD_TRASH = frozenset({"substack.com"})

# Domains that send on behalf of many unrelated businesses. A rule may target
# one exact address here, but the DOMAIN must never be learned as "a sender":
# teaching shopifyemail.com from one store would file every other store's mail
# the same way.
SHARED_SENDING_DOMAINS = frozenset({
    "sendgrid.net", "mailchimpapp.com", "mcsv.net", "rsgsv.net", "mailgun.org",
    "amazonses.com", "sparkpostmail.com", "mandrillapp.com", "shopifyemail.com",
    "klaviyomail.com", "sendinblue.com", "hubspotemail.net", "exacttarget.com",
    "mktomail.com", "postmarkapp.com", "zendesk.com", "intercom-mail.com",
    "substack.com", "beehiiv.com", "ghost.io", "cmail19.com", "cmail20.com",
    "createsend.com", "salesforce.com", "custhelp.com", "freshemail.io",
})


def is_shared_infrastructure(domain: str) -> bool:
    domain = (domain or "").lower()
    return any(domain == d or domain.endswith("." + d) for d in SHARED_SENDING_DOMAINS)
FINANCE_LABEL_ROOT = "Finance"
_ADDR_TOKEN_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.[a-z]{2,}")
_DOMAIN_TOKEN_RE = re.compile(r"[\w.\-]+\.[a-z]{2,}")


def filter_from_tokens(criteria: dict) -> set[str]:
    """Every exact address and bare domain a filter's sender criteria name."""
    blob = " ".join((criteria.get("from", ""), criteria.get("query", ""))).lower()
    tokens = set(_ADDR_TOKEN_RE.findall(blob))
    tokens |= set(re.findall(r"(?<![\w.+\-])@([\w.\-]+\.[a-z]{2,})", blob))
    tokens |= set(re.findall(r"from:\(?\s*([\w.\-]+\.[a-z]{2,})(?=[\s)]|$)", blob))
    for part in re.split(r"[\s,()]+|\bor\b", criteria.get("from", "").lower()):
        part = part.strip().lstrip("@")
        if part and "@" not in part and _DOMAIN_TOKEN_RE.fullmatch(part):
            tokens.add(part)
    return tokens


def is_broad_domain(token: str) -> bool:
    return "@" not in token and bool(_DOMAIN_TOKEN_RE.fullmatch(token))


def covers(address: str, token: str) -> bool:
    """True when a filter token (exact address or domain) matches the address."""
    address = (address or "").lower()
    if "@" in token:
        return address == token
    domain = address.rsplit("@", 1)[-1]
    return domain == token or domain.endswith("." + token)


def is_archive_action(action: dict) -> bool:
    return ("INBOX" in set(action.get("removeLabelIds", []))
            and "TRASH" not in set(action.get("addLabelIds", [])))


def is_trash_action(action: dict) -> bool:
    return "TRASH" in set(action.get("addLabelIds", []))


# Junk is filed, never deleted (2026-09-17): Gmail empties Trash after 30 days,
# so one wrong Trash rule loses mail for good. A junk rule skips the inbox and
# adds this label; everything that used to govern Trash rules (the security
# guard, exact addresses, @substack.com as the one allowed broad rule) now
# governs junk rules of either kind.
LOW_PRIORITY_LABEL = "Low Priority"


def junk_filter_action(low_priority_id: str = "") -> dict:
    """The action junk rules use — Gmail's SPAM label is never removed.

    Filter plans name the label (it may not exist yet); live writes pass its id.
    """
    if low_priority_id:
        return {"addLabelIds": [low_priority_id], "removeLabelIds": ["INBOX"]}
    return {"addLabelNames": [LOW_PRIORITY_LABEL], "removeLabelIds": ["INBOX"]}


def is_junk_action(action: dict, low_priority_id: str = "") -> bool:
    """A rule that disposes of mail: legacy Trash, or filed under Low Priority."""
    added = set(action.get("addLabelIds", [])) | set(action.get("addLabelNames", []))
    return "TRASH" in added or LOW_PRIORITY_LABEL in added or bool(
        low_priority_id and low_priority_id in added)


# Gmail "Doesn't have" clause every Trash rule carries, so an account-security
# message from a marketing address (or an inactive provider — policy 8) is
# never trashed. Deliberately broader than SECURITY_SUBJECT_RE: a promo that
# happens to say "login" merely stays in the inbox.
_GUARD_TERMS = ("security", "fraud", "password", "passcode", "new device", "sign-in",
                "sign in", "login", "verify", "verification", "unauthorized",
                "suspicious", "withdrawal", "declined", "locked", "recovery",
                "payment failed")
TRASH_SECURITY_GUARD = "subject:(" + " OR ".join(
    f'"{t}"' if not t.isalpha() else t for t in _GUARD_TERMS) + ")"
_GUARD_RE = re.compile(r"(?i)\b(" + "|".join(re.escape(t) for t in _GUARD_TERMS) + r")\b")


def trash_filter_criteria(address: str) -> dict:
    return {"from": address, "negatedQuery": TRASH_SECURITY_GUARD}


def has_security_guard(criteria: dict) -> bool:
    return (criteria.get("negatedQuery") or "").strip() == TRASH_SECURITY_GUARD


def trash_guard_spares(subject: str) -> bool:
    """Would the guard's "Doesn't have" clause keep this subject out of Trash?"""
    return bool(_GUARD_RE.search(normalize_subject(subject)))


# ── Monitoring coverage (what keeps archived mail visible) ──────────────────

# Gmail `label:` never matches sub-labels, so every Travel leaf is listed.
TRAVEL_LABELS = ("Travel", "Travel/Flights", "Travel/Hotels", "Travel/Itineraries")

BACKSTOP = "daemon.labeled_out_backstop"

STATEMENT_WATCH_OWNER = "cron.finance-statement-watch"


def _statement_watch_state_path() -> Path:
    # Resolve lazily: this module is also imported by read-only planning tools.
    import sys
    shared = str(Path(__file__).resolve().parents[2] / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    from data_paths import new_file
    return new_file("afrexai-personal-finance", "state", "statement_watch.json")


def statement_watch_active(provider: Optional[str] = None, *,
                           now: Optional[datetime] = None) -> bool:
    """Defer an enabled provider's notices while the watcher is working.

    Read on each ownership check so a disabled or stalled watcher hands new
    notices back to the general monitor without another daemon restart.
    Unreadable state and invalid clocks fail open to the general monitor.
    """
    try:
        state = json.loads(_statement_watch_state_path().read_text())
        if not isinstance(state, dict) or state.get("enabled") is not True:
            return False
        providers = state.get("enabled_providers", ["card_a"])
        if (not isinstance(providers, list) or not providers
                or any(p not in _live_statement_rules() for p in providers)):
            return False
        if provider is not None and provider not in providers:
            return False
        stamp = state.get("successful_poll_at")
        if not isinstance(stamp, str):
            return False
        last_poll = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if last_poll.tzinfo is None or last_poll.utcoffset() != timedelta(0):
            return False
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            return False
        age = (now - last_poll).total_seconds()
        return -60 <= age <= 15 * 60
    except (OSError, ValueError, TypeError, OverflowError):
        return False


def statement_watch_handled(message_id: Optional[str]) -> bool:
    """A delivered request or received/seeded notice may be consumed safely.

    This acknowledgment remains valid when polling later stops, preventing a
    second general notification for a message the finance watcher handled.
    """
    if not message_id:
        return False
    try:
        state = json.loads(_statement_watch_state_path().read_text())
        notices = state.get("notices", {}) if isinstance(state, dict) else {}
        if not isinstance(notices, dict):
            return False
        for notice in notices.values():
            if not isinstance(notice, dict):
                continue
            mids = notice.get("message_ids")
            if not isinstance(mids, list) or message_id not in mids:
                continue
            notified = notice.get("notified_at")
            if ((isinstance(notified, str) and bool(notified))
                    or notice.get("status") in ("received", "observed_before_activation")):
                return True
    except (OSError, ValueError, TypeError):
        pass
    return False


@dataclass(frozen=True)
class DedicatedPath:
    """A catch-up that owns a slice of labeled-out mail.

    sender_domains  domain suffixes whose mail the path reads (empty = any)
    subject_re      the subjects it acts on (None = every subject)
    labels          label names it reads (empty = not label-scoped)
    handles         what it does with the message
    """
    id: str
    handles: str
    sender_domains: tuple = ()
    subject_re: Optional[str] = None
    labels: tuple = ()

    def owns(self, address: str, subject: str, label_names: Iterable[str] = ()) -> bool:
        if self.id == STATEMENT_WATCH_OWNER:
            # A union of domains and subjects alone would cross-match providers.
            return _identify_statement_provider(address, subject) is not None
        if self.sender_domains and not any(
                covers(address, domain) for domain in self.sender_domains):
            return False
        if self.labels and not set(self.labels) & set(label_names or ()):
            return False
        return self.subject_re is None or bool(
            re.search(self.subject_re, subject or "", re.IGNORECASE))


# Keep these in step with the code they describe: the daemon catch-ups in
# daemon.py and the finance watcher crons in afrexai-personal-finance/scripts.
DEDICATED_PATHS = (
    DedicatedPath(
        "daemon.televisit_catchup",
        "TeleVisit appointment reminder → calendar proposal",
        sender_domains=("televisit.example",),
        subject_re=r"appointment reminder"),
    DedicatedPath(
        "daemon.reservations_label_catchup",
        "OpenTable reservation confirmation → calendar proposal",
        sender_domains=("opentable.com",),
        subject_re=(r"reservation confirm|confirmed for|you.?re confirmed|"
                    r"your (?:table|reservation) (?:at|for)")),
    DedicatedPath(
        "daemon.travel_label_catchup",
        "itinerary / booking change → calendar proposal or cancellation cleanup",
        labels=TRAVEL_LABELS),
    DedicatedPath(
        "cron.finance-txn-watch",
        "Card A transaction alert → live ledger + Telegram",
        sender_domains=("carda.example",),
        subject_re=r"transaction was made"),
    DedicatedPath(
        STATEMENT_WATCH_OWNER,
        "Statement-ready notice → tracked statement upload request",
        sender_domains=tuple(domain for rule in STATEMENT_RULES.values()
                             for domain in rule["domains"]),
        subject_re="|".join(f'(?:{rule["subject_re"]})'
                            for rule in STATEMENT_RULES.values())),
    DedicatedPath(
        "cron.finance-p2p-watch",
        "P2P payment-app payment → live ledger + Telegram",
        sender_domains=("p2p.example",),
        subject_re=r"^(.+ paid you|you paid .+) \$[\d,]+\.\d{2}$"),
    DedicatedPath(
        "cron.finance-zelle-watch",
        "Bank A Zelle payment → live ledger + Telegram",
        sender_domains=("notify.banka.example",),
        subject_re=r"^you (sent|received) money with zelle"),
    DedicatedPath(
        "cron.finance-card-c-watch",
        "Card B large purchase → live ledger + Telegram",
        sender_domains=("cardc.example",),
        subject_re=r"large purchase approved"),
)


def dedicated_path_for(address: str, subject: str,
                       label_names: Iterable[str] = (), *,
                       message_id: Optional[str] = None) -> Optional[DedicatedPath]:
    """The dedicated catch-up that owns this message, if any."""
    for path in DEDICATED_PATHS:
        if path.owns(address, subject, label_names):
            if path.id == STATEMENT_WATCH_OWNER:
                provider = _identify_statement_provider(address, subject)
                if provider is None:
                    continue
                if not (statement_watch_handled(message_id)
                        or statement_watch_active(provider)):
                    continue
            return path
    return None


def has_travel_label(label_names: Iterable[str]) -> bool:
    return bool(set(TRAVEL_LABELS) & set(label_names or ()))


def backstop_disposition(kind: str, address: str, subject: str,
                         label_names: Iterable[str] = (), *,
                         message_id: Optional[str] = None) -> str:
    """How the labeled-out backstop treats a message.

    security → "surface" everywhere (no catch-up owns security mail)
    Travel-labeled → "travel": the daemon defers only for messages the travel
        catch-up actually HANDLED as itineraries (recorded per message). An
        itinerary false negative is not "owned" — it falls back to the rules
        below (audit D6: ownership must be earned, not assumed).
    owned by another dedicated catch-up → "owned" (that path surfaces it)
    Statement waiting for its healthy watcher → "defer" (retain for retry)
    material → "surface"; marketing → "drop"; unknown → "classify" (L1/L2)
    """
    if kind == "security":
        return "surface"
    if has_travel_label(label_names):
        return "travel"
    owner = dedicated_path_for(address, subject, label_names, message_id=message_id)
    if owner:
        if (message_id and owner.id == STATEMENT_WATCH_OWNER
                and not statement_watch_handled(message_id)):
            return "defer"
        return "owned"
    if kind == "material":
        return "surface"
    if kind == "marketing":
        return "drop"
    return "classify"


def visibility_path(kind: str, address: str, subject: str,
                    label_names: Iterable[str] = ()) -> Optional[str]:
    """Which path keeps an archived security/material message visible.

    Returns a path id, or None when nothing would surface it. Travel-labeled
    material only counts as covered when the travel catch-up would recognise
    it as an itinerary (daemon.ITINERARY_SUBJECT_RE) — other travel updates
    are a coverage gap.
    """
    if kind not in ("security", "material"):
        return None
    disposition = backstop_disposition(kind, address, subject, label_names)
    if disposition == "surface":
        return BACKSTOP
    if disposition == "travel":
        # Recognisable itineraries go to the travel catch-up; anything it does
        # not handle (a gate change, a check-in reminder) falls to the backstop.
        recognised = (ITINERARY_SUBJECT_RE.search(subject or "")
                      or ITINERARY_CANCEL_RE.search(subject or ""))
        return "daemon.travel_label_catchup" if recognised else BACKSTOP
    path = dedicated_path_for(address, subject, label_names)
    return path.id if path else None


# The itinerary pre-check the daemon runs on inbox mail; also the definition of
# what the travel catch-up can be relied on to recognise.
# A PERSON CONFIRMING A TIME IN PROSE (2026-10-09). The subject gate above catches
# machines — OpenTable, airlines, TeleVisit — whose subject lines are predictable.
# It cannot catch how humans actually book: a reply, the original subject, and the
# detail in the body. A venue manager replied "Re: Come see us" containing
# "Let's do 3pm Tuesday 10/13 - I've got you on the calendar" with a street address.
# Nothing read the body, so it was filed FYI and never reached the calendar.
#
# This is a CHEAP PRE-FILTER, not a parser: a clock time next to a weekday or a date,
# in a short stretch of body text. It decides only whether the real extractor is worth
# running, so a false positive costs one model call and a false negative costs a missed
# appointment. Deliberately requires BOTH a time and a day — "call me at 3" is not a
# booking, and "sometime Tuesday" is not either.
_CLOCK = r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b'
_DAYISH = (r'\b(?:mon|tues?|wed(?:nes)?|thur?s?|fri|sat(?:ur)?|sun)(?:day)?\b'
           r'|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b'
           r'|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b'
           r'|\b(?:today|tomorrow)\b')
ITINERARY_BODY_RE = re.compile(f'(?=.*(?:{_CLOCK}))(?=.*(?:{_DAYISH}))', re.I | re.S)


def body_looks_like_an_appointment(body: str, window: int = 600) -> bool:
    """True when a short stretch of body text names both a day and a clock time.

    Only a gate. Whether it IS an appointment is the extractor's job, and it is the
    one allowed to say no.
    """
    return bool(ITINERARY_BODY_RE.search((body or "")[:window]))


ITINERARY_SUBJECT_RE = re.compile(
    r'itinerary|e-?ticket|your (trip|flight|booking|reservation)|'
    r'(booking|reservation|order|flight|ticket)\s+confirm|'
    r"you.?re (going|flying) to|"           # Southwest: "You're going to <city>"
    r'appointment\s+(reminder|confirm|scheduled|notification)|'
    r'(upcoming|scheduled)\s+appointment|'
    r'your\s+appointment|'
    r'\([A-Z0-9]{5,8}\)',                   # confirmation code in parens: (ABC12D)
    re.IGNORECASE,
)

# A booking *cancellation* email (hotel/flight/restaurant/event) still carries
# the full property name, dates and confirmation number, so the itinerary check
# happily tags it is_itinerary=true and the pipeline would propose *creating*
# calendar events for a trip that no longer exists (a hotel, Sep 2026:
# "Reservation Cancellation #… for <hotel>" fired a "🏨 Hotel booking
# detected" proposal). Deliberately tight — never a bare "cancel", which
# appears in every confirmation's cancellation-policy boilerplate.
ITINERARY_CANCEL_RE = re.compile(
    r'\b(reservation|booking|itinerary|trip|flight|stay|order|ticket|appointment)s?\s+'
    r'(has\s+been\s+|have\s+been\s+|is\s+|was\s+|now\s+)?cancell?ed\b'
    r'|\bcancell?ation\s+(confirmation|notice|notification|complete|completed|successful)'
    r'|\bcancell?ation\s+(number|no\.?|#)\s*[:#]?\s*[A-Z0-9]{5,}'
    r'|\byour\s+(reservation|booking|flight|stay|trip|order|ticket)\s+(is|has\s+been|was|will\s+be)\s+cancell?ed'
    r'|\bsuccessfully\s+cancell?ed\b'
    r'|\bthis\s+(reservation|booking|stay|trip)\s+(has\s+been|is|was)\s+cancell?ed'
    r"|\bwe['’]?ve\s+cancell?ed\b"
    r'|\bcancell?ation\s+of\s+(your\s+)?(upcoming\s+)?(reservation|booking|stay|trip|flight|room|itinerary|hotel)\b',
    re.IGNORECASE,
)
