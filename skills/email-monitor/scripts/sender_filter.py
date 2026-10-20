#!/usr/bin/env python3
"""
Recurring-sender auto-filing — shared detection + Gmail-filter helpers.

Used by:
  daemon.py            — _maybe_propose_sender_filter(): on every inbound email,
                         decide whether to offer the user a one-tap "file this sender
                         forever" button.
  confirm_filter.py    — creates the native Gmail filter + back-files the inbox.
  skip_filter.py       — records a permanent decline.
  test_sender_filter.py

Design notes
------------
* The DOMAIN is matched against senders.json to recognise file-worthy recurring
  mail and pick a label (any label, any area — finance, travel, packages, …).
  The Gmail FILTER is always scoped to the exact From address that triggered it
  — bank.example / airline.example / hotel.example all carry real 1:1 mail, so a domain-wide
  archive rule would bury people.
* Detection is deterministic: a known-domain match, or a conservative
  finance-only subject heuristic for genuinely new institutions. No LLM call.
* Every decision is deduped by exact address via the daemon state file
  (`finance_filter` block — kept that key name for state back-compat):
  proposed / declined / created.

Importing this module is side-effect-free (no network / disk at import time) so
the daemon's test suite can load it by path.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Optional

import email_policy

OPENCLAW = Path("~/.openclaw")
_SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = _SKILL_DIR / "senders.json"
LEARNED_FILE = _SKILL_DIR / "learned_senders.json"
_LEGACY_CONFIG_FILE = _SKILL_DIR / "finance_senders.json"
TOKEN_FILE = OPENCLAW / "gmail_monitor_token.json"

# Gmail settings.basic is already on the daemon token.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]

# How long a cached copy of the live Gmail filter list stays fresh in state.
FILTER_CACHE_TTL_S = 3600
PROPOSAL_TTL_S = 900

_ADDR_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,}")
_AUTOMATED_LOCAL_RE = re.compile(
    r"^(no.?reply|donotreply|do.not.reply|noreply|alert|alerts|notification|"
    r"notifications|statements?|estatements?|service|customerservice|secure|"
    r"account|accounts|billing|email|mail|myaccount|online|member|members|"
    r"automated|auto|system|info)",
    re.IGNORECASE,
)


# ── config ────────────────────────────────────────────────────────────────────

def load_config(path: Path = CONFIG_FILE) -> dict:
    for p in (path, _LEGACY_CONFIG_FILE):
        try:
            cfg = json.loads(p.read_text())
        except Exception:
            continue
        # Accept either the new `senders` key or the legacy `institutions` key.
        if "senders" not in cfg and "institutions" in cfg:
            cfg["senders"] = cfg["institutions"]
        cfg.setdefault("senders", [])
        cfg["senders"] = cfg["senders"] + load_learned_senders()
        return cfg
    return {"senders": load_learned_senders(), "heuristic": {"enabled": False}}


_CONFIG_CACHE: dict = {"stamp": None, "config": None}


def load_config_cached() -> dict:
    """load_config(), reloaded whenever senders.json or learned_senders.json
    changes on disk — so a sender the user has just confirmed is recognised by the
    running daemon without waiting for a restart."""
    stamp = tuple(p.stat().st_mtime if p.exists() else 0
                  for p in (CONFIG_FILE, LEARNED_FILE))
    if _CONFIG_CACHE["stamp"] != stamp:
        _CONFIG_CACHE.update(stamp=stamp, config=load_config())
    return _CONFIG_CACHE["config"]


def load_learned_senders(path: Path = LEARNED_FILE) -> list:
    """Senders learned from the user's own ✅ taps (sender_ledger).

    Kept in their own file so the hand-maintained senders.json stays readable
    and anything the daemon taught itself can be reviewed or deleted in one
    place. Curated entries are matched first.
    """
    try:
        entries = json.loads(path.read_text())
        return entries if isinstance(entries, list) else []
    except Exception:
        return []


def remember_learned_sender(name: str, domain: str, label: str, area: str,
                            path: Path = LEARNED_FILE) -> bool:
    """Record a confirmed learned sender so its whole domain is recognised next
    time. Returns False when the domain is already known, or when it is shared
    sending infrastructure (one store must not teach every other store on the
    same platform) — the exact-address filter still stands either way."""
    entries = load_learned_senders(path)
    domain = (domain or "").lower()
    if not domain or email_policy.is_shared_infrastructure(domain):
        return False
    if any(domain in [d.lower() for d in e.get("domains", [])] for e in entries):
        return False
    entries.append({"name": name, "area": area, "domains": [domain],
                    "label": label, "learned_at": time.strftime("%Y-%m-%d")})
    path.write_text(json.dumps(entries, indent=2) + "\n")
    return True


def extract_address(sender: str) -> str:
    """'Bank A <no.reply.alerts@bank.example>' -> 'no.reply.alerts@bank.example' (lower)."""
    m = re.search(r"<([^>]+)>", sender or "")
    cand = m.group(1).strip() if m else (sender or "").strip()
    if _ADDR_RE.fullmatch(cand):
        return cand.lower()
    m2 = _ADDR_RE.search(sender or "")
    return m2.group(0).lower() if m2 else cand.lower()


def _domain_of(address: str) -> str:
    return address.split("@", 1)[1].lower() if "@" in address else ""


def _domain_matches(domain: str, patterns: list[str]) -> bool:
    for p in patterns:
        p = p.lower().strip()
        if domain == p or domain.endswith("." + p):
            return True
    return False


def _config_senders(config: dict) -> list:
    return config.get("senders") or config.get("institutions") or []


def match_institution(address: str, config: dict) -> Optional[dict]:
    """Return the sender dict whose domain covers this address, else None."""
    domain = _domain_of(address)
    if not domain:
        return None
    for inst in _config_senders(config):
        if _domain_matches(domain, inst.get("domains", [])):
            return inst
    return None


def classify_sender(address: str, subject: str, config: dict) -> Optional[dict]:
    """Decide whether this sender is one we'd auto-file.

    Returns None, or {name, label, area, type, source: 'known'|'heuristic'}.
    """
    inst = match_institution(address, config)
    if inst:
        return {
            "name": inst["name"],
            "label": inst.get("label", "Records/Notifications"),
            "area": inst.get("area", inst.get("type", "other")),
            "type": inst.get("type", "sender"),
            # A learned entry was taught by ONE address at the domain. It tells
            # us who the sender is, not what every other address there sends.
            "source": "learned-domain" if inst.get("learned_at") else "known",
            # Subjects from this sender that are routine paperwork: filed, never
            # surfaced. See the daemon's finance override and senders.json.
            "routine_re": inst.get("routine_re"),
        }

    heur = config.get("heuristic", {})
    if not heur.get("enabled"):
        return None
    local = address.split("@", 1)[0] if "@" in address else address
    if not _AUTOMATED_LOCAL_RE.match(local):
        return None
    try:
        if not re.search(heur["subject_re"], subject or ""):
            return None
    except re.error:
        return None
    return {
        "name": _domain_of(address) or address,
        "label": heur.get("default_label", "Finance"),
        "area": heur.get("area", "finance"),
        "type": "unknown",
        "source": "heuristic",
    }


def summarize_history(rows: list[dict]) -> dict:
    """Count sampled {subject, kind} rows into the shared policy classes."""
    counts = email_policy.empty_counts()
    for row in rows:
        counts[row["kind"]] += 1
    return {"total": len(rows), **counts, "samples": rows[:3]}


def profile_sender_history(svc, address: str,
                           cap: int = email_policy.HISTORY_CAP) -> dict:
    """Classify up to `cap` messages from exactly `address` over the policy
    window. Trash is included (a trashed security alert is still evidence);
    Spam is not (spoofed mail must not shape a rule). Gmail's `from:` also
    matches display names, so rows from any other address are discarded."""
    resp = svc.users().messages().list(
        userId="me", q=f"newer_than:{email_policy.HISTORY_DAYS}d from:{address} -in:spam",
        maxResults=cap, includeSpamTrash=True).execute()
    rows = []
    for item in resp.get("messages", []):
        full = svc.users().messages().get(
            userId="me", id=item["id"], format="metadata",
            metadataHeaders=["From", "Subject"],
        ).execute()
        headers = {h["name"].lower(): h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        if extract_address(headers.get("from", "")) != address.lower():
            continue
        subject = headers.get("subject", "(no subject)")
        kind = email_policy.classify_subject(subject, full.get("labelIds", []))
        rows.append({"subject": subject, "kind": kind})
    return summarize_history(rows)


def recommend_sender_policy(profile: dict, known: bool) -> str:
    """Return archive, marketing, mixed, security, or unknown.

    Thin adapter over email_policy.recommend_rule so proposals, the audit and
    the migration planner apply the same evidence rule. Only homogeneous
    record history (all material) may be archived, and only for a recognised
    sender; unknown subjects count against a blanket rule, never toward one.
    """
    rule, _ = email_policy.recommend_rule(profile)
    if rule == email_policy.TRASH:
        return "marketing"
    if rule == email_policy.ARCHIVE:
        return "archive" if known else "unknown"
    if rule == email_policy.MESSAGE_LEVEL:
        if int(profile.get("security", 0)):
            others = sum(int(profile.get(k, 0)) for k in ("material", "marketing", "unknown"))
            return "mixed" if others else "security"
        return "mixed"
    return "unknown"


# ── state / dedup ─────────────────────────────────────────────────────────────

def _block(state: dict) -> dict:
    return state.setdefault(
        "finance_filter",
        {"proposed": {}, "declined": [], "created": {}, "filter_cache": {}},
    )


def already_handled(address: str, state: dict) -> Optional[str]:
    """Return a reason string if we should NOT propose for this address."""
    b = _block(state)
    if address in b.get("created", {}):
        return "filter already created"
    if address in b.get("declined", []):
        return "declined by the user"
    if address in b.get("proposed", {}):
        return "proposal already pending"
    return None


def cached_filter_addresses(state: dict) -> Optional[set]:
    """Set of addresses/domains covered by existing Gmail filters, or None if the
    cache is missing/stale."""
    cache = _block(state).get("filter_cache", {})
    if not cache or (time.time() - cache.get("ts", 0)) > FILTER_CACHE_TTL_S:
        return None
    return set(cache.get("covered", []))


def store_filter_cache(state: dict, covered: set) -> None:
    _block(state)["filter_cache"] = {"ts": time.time(), "covered": sorted(covered)}


def existing_filter_covers(address: str, covered: set) -> bool:
    domain = _domain_of(address)
    for c in covered:
        c = c.lower()
        if c == address or c == domain:
            return True
        if domain and (domain == c or domain.endswith("." + c)):
            return True
    return False


def mark_proposed(address: str, state: dict) -> None:
    _block(state)["proposed"][address] = time.time()


def mark_declined(address: str, state: dict) -> None:
    b = _block(state)
    b["proposed"].pop(address, None)
    if address not in b["declined"]:
        b["declined"].append(address)


def mark_created(address: str, filter_id: str, state: dict) -> None:
    b = _block(state)
    b["proposed"].pop(address, None)
    b["created"][address] = filter_id


def expire_stale_proposals(state: dict, now: Optional[float] = None,
                           ttl: int = PROPOSAL_TTL_S,
                           active_filter: str = "", active_unsub: str = "") -> list[str]:
    """Clear proposal dedup entries whose actionable button has expired.

    Active addresses are retained only when the caller has verified that their
    pending proposal file is still fresh. Returns the addresses cleared for
    logging and tests.
    """
    now = time.time() if now is None else now
    cleared: list[str] = []
    for block_name, active in (("finance_filter", active_filter),
                               ("unsub_filter", active_unsub)):
        block = state.get(block_name)
        if not isinstance(block, dict):
            continue
        proposed = block.setdefault("proposed", {})
        for address, proposed_at in list(proposed.items()):
            if address == active:
                continue
            if now - float(proposed_at or 0) >= ttl:
                proposed.pop(address, None)
                cleared.append(address)
    return cleared


# ── Gmail API ─────────────────────────────────────────────────────────────────

def gmail_service():
    """Through the shared loader, which keeps the token's scopes from being narrowed by
    another caller and refreshes when the stored one is missing gmail.settings.basic
    (2026-10-05: creating a filter failed 403 on a token that was valid but narrow)."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
    from gmail_token import gmail_service as _service
    return _service(TOKEN_FILE, SCOPES)


def list_filter_coverage(svc) -> set:
    """Every from-address / domain any current Gmail filter matches on.

    Handles both `criteria.from` (`@info6.bank.example`, `a@b.com`) and
    `criteria.query` forms including the parenthesised
    `from:(a@x.com OR b@y.com)` that gog writes for multi-address filters.
    """
    covered: set = set()
    # Deliberately no try/except: an unreadable filter list must surface as an
    # error, never as "there are no filters" (which callers used to cache for an
    # hour and then propose duplicates against — audit D8).
    resp = svc.users().settings().filters().list(userId="me").execute()
    for f in resp.get("filter", []):
        crit = f.get("criteria", {})
        # criteria.from is a structured field: a bare address, a bare domain,
        # "@domain", or several joined by OR / spaces.
        for tok in re.split(r"[\s,]+|\bOR\b", (crit.get("from") or "").lower()):
            tok = tok.strip().strip("()").lstrip("@")
            if re.fullmatch(r"[\w.+\-]+@[\w.\-]+\.[a-z]{2,}", tok) or \
               re.fullmatch(r"[\w.\-]+\.[a-z]{2,}", tok):
                covered.add(tok)
        # query strings: from:(a@x OR b@y), from:@domain, plain addresses
        blob = (crit.get("query") or "").lower()
        for tok in re.findall(r"[\w.+\-]+@[\w.\-]+\.[a-z]{2,}", blob):
            covered.add(tok)
        for tok in re.findall(r"from:\(?\s*@?([\w.\-]+\.[a-z]{2,})", blob):
            covered.add(tok)
    return covered


def resolve_label_id(svc, name: str) -> Optional[str]:
    resp = svc.users().labels().list(userId="me").execute()
    for lb in resp.get("labels", []):
        if lb.get("name") == name:
            return lb.get("id")
    # create nested label if missing
    try:
        made = svc.users().labels().create(
            userId="me",
            body={"name": name, "labelListVisibility": "labelShow",
                  "messageListVisibility": "show"},
        ).execute()
        return made.get("id")
    except Exception:
        return None


def create_filter(svc, address: str, label_id: str) -> str:
    body = {
        "criteria": {"from": address},
        "action": {"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
    }
    made = svc.users().settings().filters().create(userId="me", body=body).execute()
    return made.get("id", "")


def backfile_inbox(svc, address: str, label_id: str, cap: int = 200) -> int:
    """Apply the label + drop INBOX on mail already sitting in the inbox from this
    sender. Returns the count moved."""
    ids: list[str] = []
    page = None
    while len(ids) < cap:
        resp = svc.users().messages().list(
            userId="me", q=f"in:inbox from:{address}", maxResults=100,
            pageToken=page,
        ).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            break
    ids = ids[:cap]
    if not ids:
        return 0
    for i in range(0, len(ids), 1000):
        svc.users().messages().batchModify(
            userId="me",
            body={"ids": ids[i:i + 1000],
                  "addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
        ).execute()
    return len(ids)


# ── Unsubscribe + trash (one-tap "kill this promo sender") ────────────────────
# Sibling of the "file this sender" flow above, for UNKNOWN bulk senders that
# keep landing in the inbox. State lives in a separate `unsub_filter` block.

_LU_URL_RE = re.compile(r"<\s*(https?://[^>]+)\s*>", re.IGNORECASE)
_LU_MAILTO_RE = re.compile(r"<\s*mailto:([^>]+)\s*>", re.IGNORECASE)


def parse_list_unsubscribe(header: str) -> dict:
    """`List-Unsubscribe: <https://…>, <mailto:…>` -> {'http': url|None, 'mailto': addr|None}."""
    if not header:
        return {"http": None, "mailto": None}
    m_http = _LU_URL_RE.search(header)
    m_mail = _LU_MAILTO_RE.search(header)
    return {
        "http": m_http.group(1).strip() if m_http else None,
        "mailto": m_mail.group(1).strip() if m_mail else None,
    }


def _unsub_block(state: dict) -> dict:
    return state.setdefault(
        "unsub_filter", {"proposed": {}, "declined": [], "created": {}})


def unsub_already_handled(address: str, state: dict) -> Optional[str]:
    b = _unsub_block(state)
    if address in b.get("created", {}):
        return "already unsubscribed"
    if address in b.get("declined", []):
        return "kept by the user"
    if address in b.get("proposed", {}):
        return "proposal already pending"
    return None


def unsub_mark_proposed(address: str, state: dict) -> None:
    _unsub_block(state)["proposed"][address] = time.time()


def unsub_mark_declined(address: str, state: dict) -> None:
    b = _unsub_block(state)
    b["proposed"].pop(address, None)
    if address not in b["declined"]:
        b["declined"].append(address)


def unsub_mark_created(address: str, filter_id: str, state: dict) -> None:
    b = _unsub_block(state)
    b["proposed"].pop(address, None)
    b["created"][address] = filter_id


def inbox_count_from(svc, address: str, cap: int = 10) -> int:
    try:
        r = svc.users().messages().list(
            userId="me", q=f"in:inbox from:{address}", maxResults=cap).execute()
        return len(r.get("messages", []))
    except Exception:
        return 0


def _low_priority_id(svc) -> str:
    label_id = resolve_label_id(svc, email_policy.LOW_PRIORITY_LABEL)
    if not label_id:
        raise RuntimeError(f"could not find or create the {email_policy.LOW_PRIORITY_LABEL!r} label")
    return label_id


def create_junk_filter(svc, address: str) -> str:
    # Junk is filed under Low Priority, never deleted. Never strip SPAM here:
    # that is Gmail's "Never send it to Spam" and would let spoofed mail from
    # this address bypass spam protection (policy 7). The negated security
    # clause keeps account alerts in the inbox (policy 6/8).
    body = {"criteria": email_policy.trash_filter_criteria(address),
            "action": email_policy.junk_filter_action(_low_priority_id(svc))}
    made = svc.users().settings().filters().create(userId="me", body=body).execute()
    return made.get("id", "")


def backlog_trash_query(address: str) -> str:
    """Inbox mail from exactly this sender, minus anything security-shaped."""
    return f"in:inbox from:{address} -{email_policy.TRASH_SECURITY_GUARD}"


def file_junk_from_sender(svc, address: str, cap: int = 300) -> int:
    """Move inbox mail from this sender to Low Priority. Returns the count moved."""
    ids: list = []
    page = None
    while len(ids) < cap:
        resp = svc.users().messages().list(
            userId="me", q=backlog_trash_query(address), maxResults=100,
            pageToken=page).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            break
    ids = ids[:cap]
    if not ids:
        return 0
    action = email_policy.junk_filter_action(_low_priority_id(svc))
    for i in range(0, len(ids), 1000):
        svc.users().messages().batchModify(userId="me", body={"ids": ids[i:i + 1000], **action}).execute()
    return len(ids)


def try_unsubscribe_http(header: str, timeout: int = 10) -> str:
    """Best-effort one-click unsubscribe. RFC 8058: if List-Unsubscribe-Post is
    offered the caller passes the raw List-Unsubscribe header and we POST
    `List-Unsubscribe=One-Click`; otherwise a plain GET of the https link. Never
    raises — returns a short status string."""
    import urllib.request
    parsed = parse_list_unsubscribe(header)
    url = parsed.get("http")
    if not url:
        return "no http unsubscribe link (filter only)"
    try:
        req = urllib.request.Request(
            url, data=b"List-Unsubscribe=One-Click",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": "Mozilla/5.0"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return f"unsubscribe POST {r.status}"
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return f"unsubscribe GET {r.status}"
    except Exception as e:
        return f"unsubscribe link failed ({str(e)[:60]}) — filter still applied"
