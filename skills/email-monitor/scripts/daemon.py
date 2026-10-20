#!/usr/bin/env python3
"""
Email agent daemon — single persistent process replacing both cron jobs.

Two threads:
  telegram  Long-polls Telegram (timeout=30) for instant message handling.
            Handles grammar correction, ✅ email sends, /pending, /cancel.
  gmail     Polls Gmail every 60 s, classifies with Claude, notifies via Telegram.

State:   ~/.openclaw/email_monitor_state.json
Logs:    ~/.openclaw/logs/email-agent.log
Restart: launchd  ~/Library/LaunchAgents/com.openclaw.email-agent.plist
"""
import atexit, base64, html, json, os, re, signal, subprocess, sys, threading, time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")
from usage_logger import log_usage
from current_location import get_tz
from monitoring import MonitoringStore

sys.path.insert(0, "~/.openclaw/workspace/Skills/email-monitor/scripts")
from airport_timing import leave_time_for_flight
from compute_leave_time import one_line_reason
from fetch_calendar import gog_env
import sender_filter as _ff
import sender_ledger as _ledger
import email_policy as _policy
import forwarding_watch as _fwd
import arrival_filing as _arrive
import refile_rules as _refile
import networking_plan as _networking

sys.path.insert(0, "~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts")
import finance_mail_rules as _finance_mail

def _get_key(name: str) -> str:
    """Secrets come from the shared reader, not from a private copy of its logic.

    2026-10-06: this read openclaw.json itself, so it was the one Telegram sender in the
    workspace that bypassed runtime_secrets — and therefore the only one the test-time
    Telegram fence could not reach. Everything else already asks there."""
    try:
        sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")
        from runtime_secrets import get_secret
        return get_secret(name, "")
    except Exception:
        return ""

# ── Constants ──────────────────────────────────────────────────────────────────
TOKEN_FILE      = "~/.openclaw/gmail_monitor_token.json"
STATE_FILE      = "~/.openclaw/email_monitor_state.json"
SCOPES          = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
MY_EMAIL        = "owner@example.com"
TG_TOKEN        = _get_key("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID      = "1000000001"
API_KEY         = _get_key("ANTHROPIC_API_KEY")
MODEL           = "claude-sonnet-4-6"
MODEL_FAST      = "claude-haiku-4-5-20251001"
CT              = ZoneInfo("America/Chicago")
GMAIL_INTERVAL  = 60
LOOKBACK_S      = 7200
INBOX_LOOKBACK_S = 7 * 86400  # recover ordinary inbox mail after longer outages
SENDER_SWEEP_INTERVAL_S = 900
TG_POLL_TIMEOUT = 30
PENDING_TTL_S   = 172800   # 48 h
SIG_TTL_S       = 43200    # 12 h
_MONITOR = MonitoringStore(Path("~/.openclaw/workspace/state/monitoring.sqlite"))

# VIP senders — people the user always wants to hear about, regardless of how the
# email looks. A match (a) bypasses the L1 bulk/no-reply gate and (b) floors the
# L2 verdict at "fyi" so a "skip" classification can never silently drop it.
# "action" verdicts are left untouched. Seeded below; edit vip_senders.json in
# this skill dir (list of full addresses and/or "@domain.com" suffixes) to change
# it without touching code.
_VIP_FILE = Path(__file__).resolve().parent.parent / "vip_senders.json"
_VIP_SEED = ["family.member@example.com", "advisor@advisor.example"]

def _load_vips() -> set:
    entries = list(_VIP_SEED)
    try:
        extra = json.loads(_VIP_FILE.read_text())
        if isinstance(extra, list):
            entries += [str(e) for e in extra]
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Boot] vip_senders.json unreadable, using seed only: {e}", flush=True)
    return {e.strip().lower() for e in entries if e and e.strip()}

VIP_SENDERS = _load_vips()

# Exact senders for relationships the user has explicitly said are inactive. These
# bypass sender proposals and ordinary relevance classification. Security mail
# still surfaces because an old account can still be compromised.
_INACTIVE_FILE = Path(__file__).resolve().parent.parent / "inactive_senders.json"

def _load_inactive_senders() -> set:
    try:
        entries = json.loads(_INACTIVE_FILE.read_text())
        if isinstance(entries, list):
            return {str(e).strip().lower() for e in entries if str(e).strip()}
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Boot] inactive_senders.json unreadable: {e}", flush=True)
    return set()

INACTIVE_SENDERS = _load_inactive_senders()

def _is_inactive_sender(sender: str) -> bool:
    address = _ff.extract_address(sender).lower()
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    return address in INACTIVE_SENDERS or any(
        entry.startswith("@") and
        (domain == entry[1:] or domain.endswith("." + entry[1:]))
        for entry in INACTIVE_SENDERS
    )

AUTOSEND_RE = re.compile(
    r'^(no.?reply|donotreply|do.not.reply|noreply|mailer.daemon|'
    r'postmaster|bounce|bounces|unsubscribe|newsletter|notification|'
    r'notifications|alert|alerts|support|billing|confirm|orders|'
    r'hello|info|team|hi|hey|news|updates|digest|report|reports|'
    r'automated|system|daemon|robot|bot)$',
    re.IGNORECASE,
)

REPLY_INTENT_RE = re.compile(
    r'\b(respond|reply|write back|get back to|draft a reply)\b',
    re.IGNORECASE,
)

CANCEL_RE = re.compile(
    r'\b(cancel(l?ed)?|reschedul(ed)?|postpone[d]?)\b',
    re.IGNORECASE,
)

UV_FILE             = Path("~/.openclaw/pending_uv_event.json")
DIARY_FILE          = Path("~/.openclaw/pending_diary_entry.json")
CAL_FILE            = Path("~/.openclaw/pending_cal_event.json")
CAL_QUEUE_FILE      = Path("~/.openclaw/pending_cal_queue.json")
JS_LOG_FILE         = Path("~/.openclaw/workspace/diary/log.md")
SEND_PROPOSAL       = "~/.openclaw/workspace/Skills/shared/send_proposal.py"

PENDING_ACTIONS     = {"uv": UV_FILE, "diary": DIARY_FILE, "cal": CAL_FILE}
MORNING_FILE        = Path("~/.openclaw/pending_morning_journal.json")
GRATITUDE_FILE      = Path("~/.openclaw/pending_morning_gratitude.json")
MORNING_SCRIPT      = "~/.openclaw/workspace/Skills/morning-briefing/scripts/morning_briefing.py"

HEVY_CONFIG_FILE    = Path("~/.openclaw/workspace/Skills/hevy-workout/config/hevy_config.json")
COACH_OFFICE_NAME    = "Example Park Office"
COACH_OFFICE_ADDRESS = "100 Example St, Example City, ST 00000"


def _owner_at_second_home() -> bool:
    """True when the gym travel detector (detect_travel.py) has the user on the
    gym_b equipment profile, i.e. currently at his second home base. Reused here so
    coaching appointments propose in-person instead of video while he's there."""
    try:
        cfg = json.loads(HEVY_CONFIG_FILE.read_text())
        return cfg.get("equipment", {}).get("active_profile") == "gym_b"
    except Exception:
        return False

# Shared with the audit's coverage model (what the travel catch-up recognises).
# ITINERARY_CANCEL_RE keeps a booking cancellation from being re-proposed as a
# booking — see its definition in email_policy for the TownePlace incident.
ITINERARY_SUBJECT_RE = _policy.ITINERARY_SUBJECT_RE
ITINERARY_CANCEL_RE = _policy.ITINERARY_CANCEL_RE

BULK_HEADERS = {
    "x-campaign-id", "x-mailchimp-campaign",
    "x-sendgrid-campaign", "list-id", "x-mailer-recency",
    "x-mc-user", "x-klaviyo",
}

# ── State ──────────────────────────────────────────────────────────────────────
_lock = threading.Lock()

def _load() -> dict:
    with _lock:
        p = Path(STATE_FILE)
        return json.loads(p.read_text()) if p.exists() else {}

def _save(state: dict) -> None:
    with _lock:
        Path(STATE_FILE).write_text(json.dumps(state, indent=2))

def _log(prefix: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [{prefix}] {msg}", flush=True)

# ── Signature cache ────────────────────────────────────────────────────────────
_sig_cache: dict = {"text": "", "fetched_at": 0.0}

def _get_signature() -> str:
    if time.time() - _sig_cache["fetched_at"] < SIG_TTL_S:
        return _sig_cache["text"]
    try:
        from googleapiclient.discovery import build
        svc     = build("gmail", "v1", credentials=_credentials(), cache_discovery=False)
        entries = svc.users().settings().sendAs().list(userId="me").execute().get("sendAs", [])
        sig_html = next((e.get("signature", "") for e in entries if e.get("isDefault")), "")
        sig = re.sub(r'<br\s*/?>', '\n', sig_html, flags=re.IGNORECASE)
        sig = re.sub(r'<[^>]+>', '', sig)
        result  = re.sub(r'\n{3,}', '\n\n', sig).strip()
    except Exception:
        result = ""
    _sig_cache["text"]       = result
    _sig_cache["fetched_at"] = time.time()
    return result

# The user 2026-10-09: every meeting reply went out double-signed. The drafting
# model closes its own letter ("Looking forward to it. / the user") and the Gmail
# signature was then appended underneath it, so the body he was asked to ✅
# ended "The user" / "Best," / "The user". The prompts now say not to close, but a
# prompt is a request, not a guarantee — joining the two halves happens here,
# once, and the signature is the only sign-off left in the body.
_VALEDICTION_RE = re.compile(
    r"^(best|best regards|warm(est)? regards|kind regards|regards|"
    r"thanks( again| so much)?|thank you|cheers|sincerely|warmly|"
    r"all the best|talk soon|speak soon)[,.!]?$", re.IGNORECASE)
_SELF_NAME_RE = re.compile(r"^[-–—]?\s*(?:owner|the\s+user)(\s+r\.?)?[,.!]?$", re.IGNORECASE)


def _strip_closing(draft: str) -> str:
    """Drop a sign-off the model wrote for itself, leaving the body intact.

    Only whole trailing lines that are nothing but a valediction or the user's own
    name go. A real sentence ("Looking forward to it.") is body text and stays,
    and a draft that is nothing but a sign-off is returned untouched rather than
    handing the send path an empty email.
    """
    kept = draft.rstrip().splitlines()
    while kept:
        last = kept[-1].strip()
        if last and not (_VALEDICTION_RE.match(last) or _SELF_NAME_RE.match(last)):
            break
        kept.pop()
    return "\n".join(kept).rstrip() if kept else draft.rstrip()


def _with_signature(draft: str, sig: str) -> str:
    """The one place a draft body and the Gmail signature are joined.

    No signature means the model's own closing is all the draft has, so it is
    left where it is; one sign-off is the point, not which one.
    """
    if not sig:
        return draft.rstrip()
    return f"{_strip_closing(draft)}\n\n{sig}"

# ── Gmail ──────────────────────────────────────────────────────────────────────
def _credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    path  = Path(TOKEN_FILE)
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    return creds

def _gmail():
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=_credentials(), cache_discovery=False)

def _calendar():
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=_credentials(), cache_discovery=False)

def _parse_dt(val):
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
        return dt if dt.tzinfo else dt.replace(tzinfo=CT)
    except Exception:
        return None

def _received_dt(full: dict):
    """The message's Gmail receive time as a CT-aware datetime, for anchoring
    year inference on yearless dates. Falls back to now on any parse failure."""
    try:
        ms = int(full.get("internalDate") or 0)
        if ms > 0:
            return datetime.fromtimestamp(ms / 1000, tz=CT)
    except Exception:
        pass
    return datetime.now(CT)


EXTRA_CALENDARS = ["owner@work.example"]

def _find_free_slots(lookahead=7, slot_min=30, day_start=9, day_end=18, max_slots=4):
    cal    = _calendar()
    now    = datetime.now(CT)
    end    = now + timedelta(days=lookahead)
    params = dict(timeMin=now.isoformat(), timeMax=end.isoformat(), singleEvents=True, orderBy="startTime")
    events = cal.events().list(calendarId="primary", **params).execute().get("items", [])
    for cal_id in EXTRA_CALENDARS:
        try:
            events += cal.events().list(calendarId=cal_id, **params).execute().get("items", [])
        except Exception as e:
            _log("Cal", f"could not fetch {cal_id}: {e}")
    busy = sorted([
        (_parse_dt(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")).astimezone(CT),
         _parse_dt(e.get("end",   {}).get("dateTime") or e.get("end",   {}).get("date")).astimezone(CT))
        for e in events
        if _parse_dt(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
    ])
    free, day = [], (now.date() + timedelta(days=1))
    while day <= end.date() and len(free) < max_slots:
        if day.weekday() >= 5:
            day += timedelta(days=1); continue
        cursor    = datetime(day.year, day.month, day.day, day_start, tzinfo=CT)
        day_close = datetime(day.year, day.month, day.day, day_end,   tzinfo=CT)
        for bs, be in [(s, e) for s, e in busy if s.date() == day]:
            if (min(bs, day_close) - cursor) >= timedelta(minutes=slot_min):
                free.append((cursor, min(bs, day_close)))
                if len(free) >= max_slots: break
            cursor = max(cursor, be)
        if len(free) < max_slots and (day_close - cursor) >= timedelta(minutes=slot_min):
            free.append((cursor, day_close))
        day += timedelta(days=1)
    return free[:max_slots]

def _fmt_slots(slots) -> str:
    """Start AND end. Printing only the start threw away how long the opening was,
    so a 30-minute gap (slot_min, the floor) read exactly like a five-hour one and
    a draft could offer "Monday at 9:00 AM" when 9:00–9:30 was all that was free —
    an invitation to book an hour into half an hour."""
    return "\n".join(f"• {s:%A %b %-d}, {s:%-I:%M %p}–{e:%-I:%M %p} CT" for s, e in slots)

def _get_body(msg_full: dict) -> str:
    def decode(data):
        try: return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except: return ""
    def extract(part, mime):
        if part.get("mimeType") == mime:
            d = part.get("body", {}).get("data", "")
            if d: return decode(d)
        for sub in part.get("parts", []):
            r = extract(sub, mime)
            if r: return r
        return ""
    payload = msg_full.get("payload", {})
    raw = extract(payload, "text/plain")
    if not raw:
        html = extract(payload, "text/html")
        if html:
            raw = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', html, flags=re.IGNORECASE|re.DOTALL)
            raw = re.sub(r'<br\s*/?>', '\n', raw, flags=re.IGNORECASE)
            raw = re.sub(r'<[^>]+>', ' ', raw)
            raw = re.sub(r'&nbsp;', ' ', raw)
            raw = re.sub(r'&amp;', '&', raw)
            raw = re.sub(r'&lt;', '<', raw)
            raw = re.sub(r'&gt;', '>', raw)
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    raw = re.sub(r'[ \t]+', ' ', raw)
    return _strip_quoted(raw.strip())

def _strip_quoted(text: str) -> str:
    lines = text.splitlines()
    cut = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('>'): cut = i; break
        if re.match(r'^On .{10,120} wrote:$', s): cut = i; break
        if s.startswith('From:') and i + 2 < len(lines):
            if re.search(r'Sent:|To:|Subject:', ' '.join(l.strip() for l in lines[i+1:i+4])):
                cut = i; break
        if s == '--': cut = i; break
        if s.startswith('EXTERNAL EMAIL'): cut = i; break
    result = '\n'.join(lines[:cut]).strip()
    return result if result else text

def _layer1(hdrs: dict):
    # Bulk/marketing headers
    for h in BULK_HEADERS:
        if hdrs.get(h):
            return False, f"bulk-header:{h}"
    if hdrs.get("list-unsubscribe"):
        return False, "List-Unsubscribe"
    prec = hdrs.get("precedence", "").lower()
    if prec in ("bulk", "list", "junk"):
        return False, f"Precedence:{prec}"
    auto = hdrs.get("auto-submitted", "").lower()
    if auto and auto != "no":
        return False, f"Auto-Submitted:{auto}"
    # Automated sender local part
    from_raw = hdrs.get("from", "")
    m        = re.search(r'<([^>]+)>', from_raw)
    addr     = m.group(1).lower() if m else from_raw.strip().lower()
    local    = addr.split("@")[0] if "@" in addr else addr
    if AUTOSEND_RE.match(local):
        return False, f"no-reply:{local}"
    return True, "ok"


def _passes_filters(hdrs: dict, subject: str):
    """L1 gate used by the Gmail loop, with one override: automated booking
    systems (clinics, salons, etc.) legitimately send from no-reply addresses,
    so a subject that plainly reads as an appointment/booking notice should
    still get through even when the sender-pattern heuristic alone would skip
    it. Only that specific heuristic is overridden — bulk-header,
    List-Unsubscribe, Precedence, and Auto-Submitted skips are stronger
    true-bulk-mail signals and are never overridden.

    Regression case: noreply@clinic-booking.example sent a real "Appointment
    Reminder" for a scheduled visit; it was silently dropped at L1 before the
    itinerary check ever saw the subject (see LRN-20260714 in LEARNINGS.md).
    """
    passes, reason = _layer1(hdrs)
    if not passes and reason.startswith("no-reply:") and ITINERARY_SUBJECT_RE.search(subject):
        return True, "ok"
    return passes, reason

def _extract_email(sender: str) -> str:
    m    = re.search(r'<([^>]+)>', sender)
    addr = m.group(1).strip() if m else sender.strip()
    if '@' in addr and '.' in addr.split('@')[-1]:
        return addr
    m2 = re.search(r'[\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,}', sender)
    return m2.group(0) if m2 else addr

def _is_vip(sender: str) -> bool:
    """True when the From address is on the user's VIP list, either as a full
    address or via an "@domain" suffix entry."""
    addr = _extract_email(sender).lower()
    if not addr or "@" not in addr:
        return False
    if addr in VIP_SENDERS:
        return True
    domain = "@" + addr.split("@", 1)[1]
    return domain in VIP_SENDERS

# ── Itinerary helpers ──────────────────────────────────────────────────────────

def _has_ics_attachment(full_email: dict) -> bool:
    def scan(parts):
        for p in parts:
            mt = p.get("mimeType", "")
            fn = p.get("filename", "")
            if mt in ("text/calendar", "application/ics") or fn.lower().endswith(".ics"):
                return True
            if p.get("parts") and scan(p["parts"]):
                return True
        return False
    return scan(full_email.get("payload", {}).get("parts", []))


def _get_ics_content(full_email: dict, msg_id: str) -> Optional[str]:
    import base64 as _b64
    def scan(parts):
        for p in parts:
            mt = p.get("mimeType", "")
            fn = p.get("filename", "")
            if mt in ("text/calendar", "application/ics") or fn.lower().endswith(".ics"):
                body   = p.get("body", {})
                data   = body.get("data", "")
                att_id = body.get("attachmentId", "")
                if data:
                    try:
                        return _b64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                    except Exception:
                        pass
                if att_id:
                    try:
                        att  = _gmail().users().messages().attachments().get(
                            userId="me", messageId=msg_id, id=att_id
                        ).execute()
                        raw  = att.get("data", "")
                        if raw:
                            return _b64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace")
                    except Exception as e:
                        _log("Cal", f"attachment fetch error: {e}")
            if p.get("parts"):
                r = scan(p["parts"])
                if r: return r
        return None
    return scan(full_email.get("payload", {}).get("parts", []))


class DeliveryFailed(Exception):
    """A notification or proposal never reached Telegram. The source message
    must stay unprocessed so the next poll retries it — recording it as handled
    would lose it for good (audit D3/D4, 2026-09-16)."""


def already_on_calendar(gog_events: list) -> str:
    """The summary of an existing event this proposal would duplicate, or "".

    WHY (2026-10-13, twice): The user books a simulator bay through the golf skill, which puts
    "Golf <> the user @ Venue A" on his calendar; Venue A then emails a
    confirmation, the itinerary path reads it as an event ticket and proposes the
    SAME hour again. He saw "🎟 Event ticket detected" for a booking already on
    his calendar. Anything he books through his own skills arrives this way —
    restaurants, golf, flights — so the check belongs here, once, rather than in
    each skill.

    Deliberately narrow: same start minute AND same place (first line of the
    location, or the venue in the summary). Two genuinely different things rarely
    start in the same minute at the same address, and a near-miss should still
    reach the user — an unasked silence is worse than one extra question.
    """
    # The confirmation number is the only identity both sides can agree on, and
    # the EMAIL is where it reliably appears — the booking skill's own browser
    # scrape is best-effort and returns "" silently, which is why the Oct 14
    # event carries no number at all. When it IS on an event, match it exactly;
    # time-and-place below is the fallback for the first booking of a venue.
    confs = set()
    for ev in gog_events:
        for field in ("description", "summary"):
            confs.update(re.findall(r"\b([A-Z]{3}[A-Z0-9]{6,})\b", str(ev.get(field) or "")))
    for ev in gog_events:
        start = str(ev.get("from") or "")
        if not start:
            continue
        try:
            day = start[:10]
            hhmm = start[11:16]
        except Exception:  # noqa: BLE001
            continue
        place = (str(ev.get("location") or "").split(",")[0].strip()
                 or str(ev.get("summary") or "").split("@")[-1].strip())
        if not place:
            continue
        try:
            r = subprocess.run(
                ["/opt/homebrew/bin/gog", "cal", "ls", "--account", MY_EMAIL,
                 "--all", "--from", day, "--to", day, "-j", "--max", "50"],
                capture_output=True, text=True, timeout=25, env=gog_env()
            )
            if r.returncode != 0:
                return ""          # cannot prove a duplicate; propose as before
            existing = json.loads(r.stdout).get("events", [])
        except Exception as e:  # noqa: BLE001
            _log("Cal", f"duplicate-check lookup error: {e}")
            return ""
        for cur in existing:
            whole = f"{cur.get('summary','')} {cur.get('description','')} {cur.get('location','')}"
            if confs and any(c in whole for c in confs):
                return str(cur.get("summary") or "an existing event")
            cur_start = str((cur.get("start") or {}).get("dateTime") or "")
            if cur_start[11:16] != hhmm:
                continue
            blob = f"{cur.get('summary','')} {cur.get('location','')}".lower()
            if place.lower() in blob:
                return str(cur.get("summary") or "an existing event")
    return ""


def _publish_cal_proposal(proposal: dict, log_fn) -> None:
    """
    Send a calendar proposal's Telegram message and store it as the pending
    proposal. CAL_FILE is a single slot read unconditionally by confirm:cal /
    skip:cal — if a proposal is already sitting there unconfirmed, overwriting
    it silently discards it (a later invite arriving before an earlier flight
    proposal was confirmed used to erase the flight proposal entirely, so
    tapping the flight message's button acted on the invite's data instead).
    Queue instead of clobbering; confirm_cal_event.py / skip_cal.py promote the
    next queued proposal into CAL_FILE once the active one is resolved — and
    that promotion is what sends *this* proposal's real buttons. A queued
    proposal never gets working buttons up front: confirm:cal/skip:cal read
    whatever is currently in CAL_FILE with no per-message check, so buttons
    sent here before this proposal is actually in CAL_FILE could be tapped
    while a different proposal still occupies the slot and silently act on
    that one instead — the same class of bug the queue itself was built to
    fix, just moved from "the write" to "the buttons."
    """
    proposal.setdefault("created_epoch", time.time())
    dupe = already_on_calendar(proposal.get("gog_events") or [])
    if dupe:
        # Silent by design: there is nothing for the user to do about a confirmation
        # for something already on his calendar, and asking him is the bug.
        log_fn(f"not proposing — already on the calendar as {dupe!r}")
        return
    unusable, why = _cal_proposal_is_unusable(proposal)
    if unusable:
        # Silent on Telegram by design: this is an extraction failure, not
        # something the user can act on. It belongs in the log, not his chat.
        log_fn(f"not proposing — {why}; the email carried no usable date")
        return
    if CAL_FILE.exists():
        try:
            queue = json.loads(CAL_QUEUE_FILE.read_text()) if CAL_QUEUE_FILE.exists() else []
        except Exception:
            queue = []
        queue.append(proposal)
        CAL_QUEUE_FILE.write_text(json.dumps(queue))
        _tg_send(f"{proposal['proposal_text']}\n\n(queued behind an earlier unconfirmed proposal — "
                 f"you'll get buttons for this one once that's resolved)")
        log_fn(f"proposal queued behind an unconfirmed one ({len(queue)} waiting)")
    else:
        msg_id = _tg_send_with_buttons(proposal["proposal_text"], "cal", proposal=proposal)
        if not msg_id:
            # No buttons reached the user: storing it would wedge the single slot
            # behind a proposal he can never see or resolve.
            raise DeliveryFailed("calendar proposal buttons were not delivered")
        proposal["source_tg_msg_id"] = msg_id
        CAL_FILE.write_text(json.dumps(proposal))


# ── Calendar-proposal queue: self-draining maintenance ─────────────────────────
#
# CAL_FILE is a single slot; later proposals queue in CAL_QUEUE_FILE and are
# only promoted when the user taps ✅/❌ on the blocking message (confirm_cal_event
# / skip_cal). If he never taps — e.g. a hotel booking he ignored, then
# cancelled — that one proposal wedges the slot and every later calendar
# proposal (a coaching session, a flight) queues behind it forever with no
# buttons. These helpers give the queue a drain path that does NOT depend on a
# tap: a proposal whose event has already happened, or that has sat unconfirmed
# too long, is dropped and the next one promoted in its place.

STALE_CAL_PROPOSAL_MAX_AGE_DAYS = 14


def _first_line(proposal: dict) -> str:
    return (proposal.get("proposal_text", "").splitlines() or ["(calendar proposal)"])[0].strip()


def _cal_proposal_latest_dt(proposal: dict) -> Optional[datetime]:
    """Latest instant referenced by a proposal's gog_events (end, else start),
    as an aware datetime. None for proposals that carry no dated events
    (an action:"delete" cancellation-cleanup proposal)."""
    latest = None
    for ev in proposal.get("gog_events", []) or []:
        for key in ("to", "from"):
            raw = ev.get(key)
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except Exception:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if latest is None or dt > latest:
                latest = dt
    return latest


def _cal_proposal_is_stale(proposal: dict, now: Optional[datetime] = None):
    """(is_stale, reason). Stale = the event it proposes is already in the past,
    or it has been sitting unconfirmed for STALE_CAL_PROPOSAL_MAX_AGE_DAYS."""
    now = now or datetime.now(timezone.utc)
    latest = _cal_proposal_latest_dt(proposal)
    if latest is not None and latest < now:
        return True, "event date has passed"
    created = proposal.get("created_epoch")
    if created:
        try:
            age_days = (now.timestamp() - float(created)) / 86400.0
        except (TypeError, ValueError):
            age_days = 0
        if age_days >= STALE_CAL_PROPOSAL_MAX_AGE_DAYS:
            return True, f"unconfirmed for {int(age_days)} days"
    return False, ""


# gog cannot create an event without these. A proposal missing one is not a
# judgement call about whether the user wants it — there is nothing to put on the
# calendar. Cancellations ({calendar_id, event_id, summary}) and ICS invites
# carry no from/to by design and are exempt.
_REQUIRED_EVENT_FIELDS = ("summary", "from", "to")


def _cal_proposal_is_unusable(proposal: dict):
    """(is_unusable, reason). 2026-09-24: a ticket email with no parseable date
    became '<name> @ None / None' with a live ✅ button; tapping it put None into
    subprocess's argv, and the TypeError landed *after* the approval was claimed,
    wedging every later calendar proposal behind an 'executing' row."""
    if proposal.get("type") == "ics_invite" or proposal.get("action") == "delete":
        return False, ""
    for index, spec in enumerate(proposal.get("gog_events") or [], start=1):
        missing = [f for f in _REQUIRED_EVENT_FIELDS if not spec.get(f)]
        if missing:
            label = spec.get("summary") or f"event {index}"
            return True, f"{label} has no {', '.join(missing)}"
    return False, ""


def _cal_proposal_should_drop(proposal: dict, now: Optional[datetime] = None):
    """(should_drop, reason) — everything that disqualifies a proposal from
    occupying the single slot, so every drain point applies the same rule."""
    unusable, why = _cal_proposal_is_unusable(proposal)
    if unusable:
        return True, why
    return _cal_proposal_is_stale(proposal, now)


def _load_cal_chain(cal_file: Path = CAL_FILE, queue_file: Path = CAL_QUEUE_FILE):
    """(active_proposal_or_None, [queued...]) from the single-slot files."""
    active = None
    if cal_file.exists():
        try:
            active = json.loads(cal_file.read_text())
        except Exception:
            active = None
    queue = []
    if queue_file.exists():
        try:
            queue = [x for x in json.loads(queue_file.read_text()) if isinstance(x, dict)]
        except Exception:
            queue = []
    return active, queue


def _rebuild_cal_slot(survivors: list, prev_active, *,
                      cal_file: Path = CAL_FILE, queue_file: Path = CAL_QUEUE_FILE,
                      send_buttons=True) -> None:
    """Write `survivors` (ordered) back into the slot + queue, promoting
    survivors[0]. Fresh buttons are sent only when the head is not the proposal
    that already held the slot (whose buttons are still live in Telegram)."""
    if not survivors:
        cal_file.unlink(missing_ok=True)
        queue_file.unlink(missing_ok=True)
        return
    head = survivors[0]
    if head is not prev_active and send_buttons:
        msg_id = _tg_send_with_buttons(head["proposal_text"], "cal", proposal=head)
        if msg_id:
            head["source_tg_msg_id"] = msg_id
    cal_file.write_text(json.dumps(head))
    if survivors[1:]:
        queue_file.write_text(json.dumps(survivors[1:]))
    else:
        queue_file.unlink(missing_ok=True)


def _sweep_stale_cal_proposals(*, cal_file: Path = CAL_FILE,
                               queue_file: Path = CAL_QUEUE_FILE) -> int:
    """Drop calendar proposals whose event has passed / that are too old and
    promote the next live one. Runs every Gmail poll cycle. Returns the number
    dropped."""
    active, queue = _load_cal_chain(cal_file, queue_file)
    if active is None:
        return 0
    survivors, dropped = [], []
    for p in [active] + queue:
        stale, why = _cal_proposal_should_drop(p)
        (dropped if stale else survivors).append((p, why))
    if not dropped:
        return 0
    for p, why in dropped:
        _log("Cal", f"dropped stale calendar proposal ({why}): {_first_line(p)}")
    _rebuild_cal_slot([p for p, _ in survivors], active,
                      cal_file=cal_file, queue_file=queue_file)
    summary = "\n".join(f"• {_first_line(p)}  ({why})" for p, why in dropped)
    _tg_send(f"🗑 Cleared {len(dropped)} calendar proposal(s) you never actioned:\n{summary}")
    return len(dropped)


def _ics_field(ics: str, field: str) -> str:
    """Extract an ICS property's value, skipping any ;PARAM=value segments.
    e.g. 'SUMMARY;LANGUAGE=en-US:Introduction' -> 'Introduction', not
    'LANGUAGE=en-US:Introduction' (the old first-delimiter match)."""
    m = re.search(rf'^{field}((?:;[^:\r\n]*)*):([^\r\n]+)', ics, re.MULTILINE | re.IGNORECASE)
    return m.group(2).strip() if m else ""


def _accept_calendar_invite(ical_uid: str) -> bool:
    try:
        cal    = _calendar()
        result = cal.events().list(calendarId="primary", iCalUID=ical_uid).execute()
        items  = result.get("items", [])
        if not items:
            _log("Cal", f"invite UID not found in calendar: {ical_uid[:40]}")
            return False
        event      = items[0]
        attendees  = event.get("attendees", [])
        found      = False
        for att in attendees:
            if att.get("email", "").lower() == MY_EMAIL.lower():
                att["responseStatus"] = "accepted"
                found = True
                break
        if not found:
            attendees.append({"email": MY_EMAIL, "responseStatus": "accepted"})
        cal.events().patch(
            calendarId="primary",
            eventId=event["id"],
            body={"attendees": attendees},
            sendUpdates="all",
        ).execute()
        return True
    except Exception as e:
        _log("Cal", f"accept invite error: {e}")
        return False


def _accept_and_rename_invite(ical_uid: str, new_summary: str) -> bool:
    """Accept a calendar invite and rename our copy to new_summary in one patch call.

    The organiser sees 'accepted'. Our calendar shows the '<> the user @' title.
    """
    if not ical_uid:
        return False
    try:
        cal    = _calendar()
        result = cal.events().list(calendarId="primary", iCalUID=ical_uid).execute()
        items  = result.get("items", [])
        if not items:
            _log("Cal", f"invite UID not found: {ical_uid[:40]}")
            return False
        event     = items[0]
        attendees = event.get("attendees", [])
        found     = False
        for att in attendees:
            if att.get("email", "").lower() == MY_EMAIL.lower():
                att["responseStatus"] = "accepted"
                found = True
                break
        if not found:
            attendees.append({"email": MY_EMAIL, "responseStatus": "accepted"})
        cal.events().patch(
            calendarId="primary",
            eventId=event["id"],
            body={"attendees": attendees, "summary": new_summary},
            sendUpdates="all",
        ).execute()
        _log("Cal", f"accepted + renamed to: {new_summary}")
        return True
    except Exception as e:
        _log("Cal", f"accept+rename error: {e}")
        return False


def _decline_and_delete_invite(ical_uid: str) -> bool:
    """Decline a calendar invite by UID and delete it from the calendar.

    Used after we've created our own '<> the user @' event to replace the
    organiser-created one (e.g. TeleVisit coaching bookings).
    """
    if not ical_uid:
        return False
    try:
        cal    = _calendar()
        result = cal.events().list(calendarId="primary", iCalUID=ical_uid).execute()
        items  = result.get("items", [])
        if not items:
            return False
        event_id = items[0]["id"]
        cal.events().delete(calendarId="primary", eventId=event_id,
                            sendUpdates="none").execute()
        _log("Cal", f"declined+deleted invite UID: {ical_uid[:40]}")
        return True
    except Exception as e:
        _log("Cal", f"decline invite error: {e}")
        return False


def _check_itinerary(subject: str, body: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    try:
        resp = client.messages.create(
            model=MODEL_FAST, max_tokens=80, temperature=0,
            system=ITINERARY_CHECK_PROMPT,
            messages=[{"role": "user", "content": f"Subject: {subject}\n\n{body[:400]}"}],
        )
        log_usage("daemon.py:_check_itinerary", MODEL_FAST, resp.usage, response_id=getattr(resp, "id", None))
        raw = resp.content[0].text.strip()
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            return {"is_itinerary": False, "type": None}
        data = json.loads(m.group(0))
        return {"is_itinerary": bool(data.get("is_itinerary")), "type": data.get("type")}
    except Exception as e:
        _log("Claude", f"itinerary check error: {e}")
        return {"is_itinerary": False, "type": None}


def _extract_itinerary(subject: str, body: str, reference_dt=None) -> Optional[dict]:
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    # Date anchor: many confirmations print a weekday + month/day with NO year
    # ("Tuesday, September 15"). Without an anchor Sonnet picks an arbitrary past
    # year where that weekday matched, the extracted datetime lands years ago,
    # and _handle_itinerary's freshness guard silently drops the proposal
    # (an investment-firm webinar, 2026-08-31: resolved to 2020-09-15).
    ref = reference_dt or datetime.now(CT)
    anchor = (
        f"Today is {ref.strftime('%A, %B %-d, %Y')}. Any date written without a "
        f"year is the next occurrence on or after today — never a past year.\n\n"
    )
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=800, temperature=0,
            system=ITINERARY_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": f"{anchor}Subject: {subject}\n\n{body[:3000]}"}],
        )
        log_usage("daemon.py:_extract_itinerary", MODEL, resp.usage, response_id=getattr(resp, "id", None))
        raw = resp.content[0].text.strip()
        # Find first JSON object or array and parse it cleanly
        start = next((i for i, c in enumerate(raw) if c in ('{', '[')), None)
        if start is None:
            return None
        obj, _ = json.JSONDecoder().raw_decode(raw, start)
        return obj
    except Exception as e:
        _log("Claude", f"itinerary extract error: {e}")
        return None


def _meal_type(dt_iso: str) -> str:
    try:
        hour = datetime.fromisoformat(dt_iso).hour
        if hour < 11:
            return "Breakfast"
        elif hour < 14:
            return "Lunch"
        elif hour < 17:
            return "Brunch"
        else:
            return "Dinner"
    except Exception:
        return "Dinner"


def _current_tz_display(dt_iso: str, source_tz: str) -> str:
    """Render an itinerary time where the user currently is, with an abbreviation.

    Extraction preserves the event's source timezone for the calendar write,
    but proposal text is read by the user in his current location. Keeping those
    concerns separate prevents a 1:30 PM Mountain webinar from looking like a
    different event than its 2:30 PM Central calendar entry.
    """
    dt = datetime.fromisoformat(dt_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(source_tz or "America/Chicago"))
    return dt.astimezone(get_tz()).strftime("%b %-d at %-I:%M %p %Z")


def _build_flight_event(f: dict) -> Optional[dict]:
    fn       = f.get("flight_number", "")
    orig     = f.get("origin_code", "")
    dest     = f.get("dest_code", "")
    orig_name = f.get("origin_name", orig)
    dep_iso  = f.get("departure_iso", "")
    arr_iso  = f.get("arrival_iso", "")
    seat     = f.get("seat")
    cabin    = f.get("cabin", "Economy") or "Economy"
    conf     = f.get("confirmation")
    orig_tz  = f.get("origin_tz", "America/Chicago")
    dest_tz  = f.get("dest_tz", "America/Chicago")
    required = (fn, orig, dest, orig_name, dep_iso, arr_iso)
    if any(v is None or str(v).strip().lower() in ("", "none", "null", "?")
           for v in required):
        return None
    try:
        dep_dt = datetime.fromisoformat(dep_iso)
        arr_dt = datetime.fromisoformat(arr_iso)
        dep_display  = dep_dt.strftime("%-I:%M %p")
        arr_display  = arr_dt.strftime("%-I:%M %p")
    except Exception:
        return None
    desc_parts = [f"{orig} {dep_display} → {dest} {arr_display}"]
    if seat:
        desc_parts.append(f"Seat: {seat} {cabin}".strip())
    if conf:
        desc_parts.append(f"Confirmation: {conf}")
    return {
        "type":           "flight",
        "summary":        f"{fn} {orig} → {dest} <> the user @ {orig_name}",
        "from":           dep_iso,
        "to":             arr_iso,
        "location":       orig_name,
        "description":    " | ".join(desc_parts),
        "start_timezone": orig_tz,
        "end_timezone":   dest_tz,
        "_flight_number": fn,
        "_dep_display":   dep_display,
        "_orig":          orig,
        "_dest":          dest,
        "_orig_tz":       orig_tz,
    }


def _build_cal_proposal(extracted) -> Optional[dict]:
    # Handle round-trip: Sonnet returns a list of flight objects
    if isinstance(extracted, list):
        legs = [f for f in extracted if isinstance(f, dict) and f.get("type") == "flight"]
        if not legs:
            return None
        gog_events = [e for e in (_build_flight_event(f) for f in legs) if e]
        if len(gog_events) != len(legs):
            return None
        conf = legs[0].get("confirmation")
        leg_lines = []
        for f in legs:
            orig, dest = f.get("origin_code","?"), f.get("dest_code","?")
            try:
                dep = datetime.fromisoformat(f.get("departure_iso","")).strftime("%a %b %-d %-I:%M %p")
            except Exception:
                dep = "?"
            leg_lines.append(f"{f.get('flight_number','')} · {orig} → {dest} · {dep}")
        proposal = "✈️ Round trip detected\n\n" + "\n".join(leg_lines) + "\n"
        if conf:
            proposal += f"Confirmation: {conf}\n"
        return {"proposal_text": proposal, "gog_events": gog_events}

    t = extracted.get("type")

    if t == "flight":
        f        = extracted
        conf     = f.get("confirmation")
        fn       = f.get("flight_number", "")
        orig     = f.get("origin_code", "")
        dest     = f.get("dest_code", "")
        dep_iso  = f.get("departure_iso", "")
        try:
            dep_dt = datetime.fromisoformat(dep_iso)
            arr_dt = datetime.fromisoformat(f.get("arrival_iso",""))
            dep_display  = dep_dt.strftime("%-I:%M %p")
            arr_display  = arr_dt.strftime("%-I:%M %p")
            date_display = dep_dt.strftime("%a %b %-d")
        except Exception:
            dep_display = arr_display = date_display = "?"
        gog_events = [e for e in [_build_flight_event(f)] if e]
        if not gog_events:
            return None
        proposal = (
            f"✈️ Flight detected\n\n{fn} · {orig} → {dest}\n"
            f"{date_display} · {dep_display} – {arr_display}\n"
        )
        if conf:
            proposal += f"Confirmation: {conf}\n"

    elif t == "hotel":
        name      = extracted.get("property_name", "Hotel")
        address   = extracted.get("address") or name
        ci_date   = extracted.get("checkin_date", "")
        co_date   = extracted.get("checkout_date", "")
        ci_time   = extracted.get("checkin_time") or "15:00"
        co_time   = extracted.get("checkout_time") or "11:00"
        conf      = extracted.get("confirmation")
        tz        = extracted.get("timezone", "America/Chicago")
        try:
            from zoneinfo import ZoneInfo
            ci_aware  = datetime.strptime(f"{ci_date}T{ci_time}", "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(tz))
            co_aware  = datetime.strptime(f"{co_date}T{co_time}", "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(tz))
            ci_iso    = ci_aware.isoformat()
            ci_end    = (ci_aware + timedelta(minutes=30)).isoformat()
            co_iso    = co_aware.isoformat()
            co_end    = (co_aware + timedelta(minutes=30)).isoformat()
            ci_label  = ci_aware.strftime("%b %-d")
            co_label  = co_aware.strftime("%b %-d")
        except Exception as e:
            _log("Cal", f"hotel datetime error: {e}")
            return None
        ci_desc = f"Check-out: {co_label}" + (f" | Confirmation: {conf}" if conf else "")
        co_desc = f"Confirmation: {conf}" if conf else ""
        proposal = (
            f"🏨 Hotel booking detected\n\n{name}\n"
            f"Check-in: {ci_label} ({ci_time}) · Check-out: {co_label} ({co_time})\n"
        )
        if conf:
            proposal += f"Confirmation: {conf}\n"
        gog_events = [
            {"summary": f"Check-in <> the user @ {name}",  "from": ci_iso, "to": ci_end,
             "location": address, "description": ci_desc, "start_timezone": tz, "end_timezone": tz},
            {"summary": f"Check-out <> the user @ {name}", "from": co_iso, "to": co_end,
             "location": address, "description": co_desc, "start_timezone": tz, "end_timezone": tz},
        ]

    elif t == "restaurant":
        name    = extracted.get("restaurant_name", "Restaurant")
        address = extracted.get("address") or name
        dt_iso  = extracted.get("datetime_iso", "")
        party   = extracted.get("party_size")
        conf    = extracted.get("confirmation")
        tz      = extracted.get("timezone", "America/Chicago")
        meal    = _meal_type(dt_iso)
        try:
            dt      = datetime.fromisoformat(dt_iso)
            end_iso = (dt + timedelta(hours=1, minutes=30)).isoformat()
            date_display = _current_tz_display(dt_iso, tz)
        except Exception:
            end_iso = dt_iso
            date_display = dt_iso
        desc_parts = []
        if party:
            desc_parts.append(f"Party of {party}")
        if conf:
            desc_parts.append(f"Confirmation: {conf}")
        proposal = f"🍽 Reservation detected\n\n{name} — {meal}\n{date_display}"
        if party:
            proposal += f" · Party of {party}"
        proposal += "\n"
        if conf:
            proposal += f"Confirmation: {conf}\n"
        gog_events = [{
            "summary":        f"{meal} <> the user @ {name}",
            "from":           dt_iso,
            "to":             end_iso,
            "location":       address,
            "description":    " | ".join(desc_parts),
            "start_timezone": tz,
            "end_timezone":   tz,
        }]

    elif t == "event":
        event_name = extracted.get("event_name", "Event")
        venue      = extracted.get("venue_name", "")
        address    = extracted.get("address") or venue
        dt_iso     = extracted.get("datetime_iso", "")
        end_iso    = extracted.get("end_datetime_iso")
        section    = extracted.get("section")
        row        = extracted.get("row")
        seat       = extracted.get("seat")
        order      = extracted.get("order")
        tz         = extracted.get("timezone", "America/Chicago")
        if not end_iso:
            # Reminder emails frequently omit an end time. A 3h block is fine as
            # a rough guess for a one-off event (concert, tour) but absurd for a
            # doctor/dentist/salon visit — default those to 1h.
            fallback_h = 1 if extracted.get("is_recurring_appointment") else 3
            try:
                end_iso = (datetime.fromisoformat(dt_iso) + timedelta(hours=fallback_h)).isoformat()
            except Exception:
                end_iso = dt_iso
        try:
            date_display = _current_tz_display(dt_iso, tz)
        except Exception:
            date_display = dt_iso
        seat_parts = [p for p in [
            f"Section {section}" if section else None,
            f"Row {row}"         if row     else None,
            f"Seat {seat}"       if seat    else None,
        ] if p]
        seat_str   = " · ".join(seat_parts)
        desc_parts = ([seat_str] if seat_str else []) + ([f"Order: {order}"] if order else [])
        proposal = f"🎟 Event ticket detected\n\n{event_name} @ {venue}\n{date_display}\n"
        if seat_str:
            proposal += f"{seat_str}\n"

        reminder_min = None
        if extracted.get("is_recurring_appointment") and extracted.get("address"):
            try:
                sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")
                from drive_time import leave_by_minutes
                reminder_min = leave_by_minutes(address)
            except Exception as e:
                _log("Cal", f"appointment drive-time error: {e}")
            if reminder_min:
                proposal += f"🚗 Reminder set {reminder_min} min before (drive + 5 min buffer)\n"

        gog_events = [{
            "summary":          f"{event_name} <> the user @ {venue}",
            "from":             dt_iso,
            "to":               end_iso,
            "location":         address,
            "description":      " | ".join(desc_parts),
            "start_timezone":   tz,
            "end_timezone":     tz,
            "reminder_minutes": reminder_min,
        }]

    else:
        return None

    return {"proposal_text": proposal, "gog_events": gog_events}


def _itinerary_latest_dt(extracted) -> Optional[datetime]:
    """Latest relevant datetime in an extracted itinerary: hotel check-out,
    a flight's arrival (or the last leg's, for a round trip), or the event /
    reservation time. Returns None when nothing parses. Naive values are read
    as America/Chicago so the result is always tz-aware and comparable."""
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")

    def _parse(s: str) -> Optional[datetime]:
        try:
            d = datetime.fromisoformat(s)
        except Exception:
            return None
        return d if d.tzinfo else d.replace(tzinfo=CT)

    items = extracted if isinstance(extracted, list) else [extracted]
    cands = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = it.get("type")
        if t == "hotel":
            tz = ZoneInfo(it.get("timezone") or "America/Chicago")
            for dkey, tval in (("checkout_date", it.get("checkout_time") or "11:00"),
                               ("checkin_date",  it.get("checkin_time")  or "15:00")):
                ds = it.get(dkey, "")
                if not ds:
                    continue
                try:
                    cands.append(datetime.strptime(f"{ds}T{tval}", "%Y-%m-%dT%H:%M").replace(tzinfo=tz))
                except Exception:
                    pass
        elif t == "flight":
            cands += [d for d in (_parse(it.get("arrival_iso", "")),
                                  _parse(it.get("departure_iso", ""))) if d]
        else:  # restaurant / event
            cands += [d for d in (_parse(it.get("datetime_iso", "")),
                                  _parse(it.get("end_datetime_iso", ""))) if d]
    return max(cands) if cands else None


def _cancellation_confs(subject: str, body: str, extracted) -> set:
    """Every plausible confirmation/record number tied to a cancellation email:
    the code(s) our itinerary extractor pulled out, plus anything shaped like a
    confirmation number in the subject or a 'Confirmation: …' line in the body.
    A hotel chain issues the cancellation email its own record number while the body
    still cites the original booking confirmation — collect both, matching is
    substring-scoped so a wrong one simply never lines up with an event."""
    confs: set = set()
    items = extracted if isinstance(extracted, list) else [extracted]
    for it in items:
        if isinstance(it, dict):
            for k in ("confirmation", "order"):
                v = it.get(k)
                if v and str(v).strip().lower() not in ("null", "none", ""):
                    confs.add(str(v).strip().upper())
    for m in re.finditer(r'#\s*([A-Z0-9]{6,})', subject, re.IGNORECASE):
        confs.add(m.group(1).upper())
    for m in re.finditer(r'(?:confirmation|reservation|record|itinerary|booking)\s*'
                         r'(?:number|no\.?|code|id|#|ID)?\s*[:#]?\s*([A-Z0-9]{6,})',
                         body[:4000], re.IGNORECASE):
        confs.add(m.group(1).upper())
    return {c for c in confs if len(c) >= 6 and not c.isalpha()}


def _find_cal_events_by_conf(confs: set) -> list:
    """Future calendar events (all of the user's calendars) whose summary,
    description or location contains one of these confirmation numbers.
    Google does the text filtering via --query; we re-check the substring
    locally so a loose free-text match can't delete an unrelated event."""
    out, seen = [], set()
    for conf in confs:
        try:
            r = subprocess.run(
                ["/opt/homebrew/bin/gog", "cal", "ls", "--account", MY_EMAIL,
                 "--all", "--query", conf, "--days", "400", "-j", "--max", "50"],
                capture_output=True, text=True, timeout=25, env=gog_env()
            )
            if r.returncode != 0:
                continue
            events = json.loads(r.stdout).get("events", [])
        except Exception as e:
            _log("Cal", f"cancellation event lookup error: {e}")
            continue
        for ev in events:
            blob = f"{ev.get('summary','')} {ev.get('description','')} {ev.get('location','')}".upper()
            if conf not in blob:
                continue
            key = (ev.get("CalendarID", "") or "primary", ev.get("id", ""))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            out.append({"calendar_id": key[0], "event_id": key[1],
                        "summary": ev.get("summary", "(untitled)")})
    return out


def _find_cal_events_by_name_window(name: str, start_date: str, end_date: str,
                                    seen_ids: set) -> list:
    """Secondary matcher for cancellations whose satellite events don't carry
    the confirmation number — Google's 'fromGmail' auto-import and our own
    'Leave for <airport>' drive-time reminder both reference the property by
    name only. Scoped hard: the property name must appear verbatim in the
    event title AND the event must start inside the cancelled stay's window,
    so an unrelated event can't be swept up (see feedback-cal-duplicate-
    cleanup-bug — unscoped deletion once nuked unrelated events)."""
    name = (name or "").strip()
    if len(name) < 8 or not start_date:
        return []
    from zoneinfo import ZoneInfo
    try:
        lo = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=1)).date()
        hi = (datetime.strptime(end_date or start_date, "%Y-%m-%d") + timedelta(days=2)).date()
    except Exception:
        return []
    try:
        r = subprocess.run(
            ["/opt/homebrew/bin/gog", "cal", "ls", "--account", MY_EMAIL,
             "--all", "--query", name, "--days", "400", "-j", "--max", "50"],
            capture_output=True, text=True, timeout=25, env=gog_env()
        )
        if r.returncode != 0:
            return []
        events = json.loads(r.stdout).get("events", [])
    except Exception as e:
        _log("Cal", f"cancellation name lookup error: {e}")
        return []
    out = []
    for ev in events:
        key = (ev.get("CalendarID", "") or "primary", ev.get("id", ""))
        if not key[1] or key in seen_ids:
            continue
        if name.upper() not in ev.get("summary", "").upper():
            continue
        st = ev.get("start", {})
        raw = st.get("dateTime") or st.get("date") or ""
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except Exception:
            continue
        if not (lo <= d <= hi):
            continue
        seen_ids.add(key)
        out.append({"calendar_id": key[0], "event_id": key[1],
                    "summary": ev.get("summary", "(untitled)")})
    return out


def _cancellation_matches_proposal(proposal: dict, confs: set, hotel: dict) -> bool:
    """True if this pending calendar proposal is for the just-cancelled booking.
    Matches on any confirmation number appearing in the proposal, OR — for a
    hotel — on the property name plus a check-in date inside the cancelled
    stay's window. The name+window path is what saves us when the confirmation
    number is OCR-mangled between the booking and the cancellation email
    (#10000001 booked vs #70000001 cancelled — the leading digit flipped)."""
    if proposal.get("action") == "delete":
        return False
    blob = proposal.get("proposal_text", "").upper()
    for ev in proposal.get("gog_events", []) or []:
        blob += " " + f"{ev.get('summary','')} {ev.get('description','')} {ev.get('location','')}".upper()
    if any(c.upper() in blob for c in confs):
        return True
    name = (hotel.get("property_name") or "").strip().upper()
    ci   = hotel.get("checkin_date") or ""
    if len(name) < 8 or name not in blob or not ci:
        return False
    try:
        lo = (datetime.strptime(ci, "%Y-%m-%d") - timedelta(days=1)).date()
        hi = (datetime.strptime(hotel.get("checkout_date") or ci, "%Y-%m-%d") + timedelta(days=2)).date()
    except Exception:
        return False
    for ev in proposal.get("gog_events", []) or []:
        try:
            d = datetime.fromisoformat(str(ev.get("from", "")).replace("Z", "+00:00")).date()
        except Exception:
            continue
        if lo <= d <= hi:
            return True
    return False


def _clear_cancelled_cal_proposals(confs: set, hotel: dict, *,
                                   cal_file: Path = CAL_FILE,
                                   queue_file: Path = CAL_QUEUE_FILE) -> int:
    """Drop any still-unconfirmed booking proposal for a reservation that has
    now been cancelled, and promote the next live proposal into the slot.
    Without this a booking the user ignored, then cancelled, wedges the queue
    forever (the cancellation only ever scanned the calendar, never the
    pending-proposal slot). Returns the number dropped."""
    active, queue = _load_cal_chain(cal_file, queue_file)
    if active is None:
        return 0
    survivors, dropped = [], []
    for p in [active] + queue:
        (dropped if _cancellation_matches_proposal(p, confs, hotel) else survivors).append(p)
    if not dropped:
        return 0
    for p in dropped:
        _log("Gmail", f"cancellation cleared stuck booking proposal: {_first_line(p)}")
    _rebuild_cal_slot(survivors, active, cal_file=cal_file, queue_file=queue_file)
    _tg_send(f"🗑 That booking was cancelled — dropped the pending calendar proposal "
             f"for it ({dropped[0].get('proposal_text','').splitlines()[0].strip() if dropped else ''}).")
    return len(dropped)


def _handle_itinerary_cancellation(subject: str, body: str, reference_dt=None) -> None:
    """A cancellation email reached the itinerary pipeline. Never propose a
    booking — instead find any calendar events we (or Google's auto-import)
    created for this reservation and propose removing them, reusing the same
    confirm:cal / skip:cal buttons via an action:"delete" proposal."""
    _log("Gmail", f"itinerary is a CANCELLATION — suppressing booking proposal: {subject[:60]}")
    extracted = _extract_itinerary(subject, body, reference_dt=reference_dt)
    confs = _cancellation_confs(subject, body, extracted if extracted else {})
    first = extracted[0] if isinstance(extracted, list) else (extracted or {})
    hotel = first if isinstance(first, dict) and first.get("type") == "hotel" else {}
    # Clear a not-yet-confirmed booking proposal for this reservation before it
    # wedges the queue — independent of whether we find calendar events below.
    _clear_cancelled_cal_proposals(confs, hotel)
    if not confs:
        _log("Gmail", "cancellation: no confirmation number found — nothing to match")
        return
    matches = _find_cal_events_by_conf(confs)
    seen_ids = {(m["calendar_id"], m["event_id"]) for m in matches}
    if isinstance(first, dict) and first.get("type") == "hotel":
        matches += _find_cal_events_by_name_window(
            first.get("property_name", ""), first.get("checkin_date", ""),
            first.get("checkout_date", ""), seen_ids)
    if not matches:
        _log("Gmail", f"cancellation: no calendar events match {sorted(confs)} — nothing to remove")
        return
    lines = "\n".join(f"• {m['summary']}" for m in matches)
    proposal = {
        "proposal_text": ("🗑 Booking cancelled — remove these from your calendar?\n\n"
                          f"{lines}\n"),
        "action":        "delete",
        "delete_events": matches,
    }
    _publish_cal_proposal(proposal, lambda m: _log("Gmail", m))
    _log("Gmail", f"cancellation removal proposal sent ({len(matches)} events): {subject[:50]}")


def _calendar_title_parts(summary: str) -> tuple[str, str]:
    """Return normalized (event name, venue) from a house-format title."""
    parts = re.split(r"\s*<>\s*the user\s*@\s*", summary or "", maxsplit=1,
                     flags=re.IGNORECASE)
    if len(parts) != 2:
        return "", ""

    def canonical(value: str) -> str:
        value = re.sub(r"\([^)]*\)", " ", value.lower())
        value = value.replace("&", " and ")
        return " ".join(re.findall(r"[a-z0-9]+", value))

    return canonical(parts[0]), canonical(parts[1])


# A party named in one title's venue must be this distinctive before it may
# match the other title's event name — short venues ("dr smith", "zoom") are
# not enough to call two appointments the same booking.
MIN_CROSS_FIELD_PARTY = 12


def _mirrored_party(name: str, venue: str) -> bool:
    """True when a distinctive venue on one side is named inside the other's event name."""
    return (len(venue) >= MIN_CROSS_FIELD_PARTY and len(name) >= MIN_CROSS_FIELD_PARTY
            and venue in name)


# The generic lead-in on an appointment title, which drifts between a booking and its
# reminder for the SAME visit: "Doctor appointment with X" vs "Medical appointment with X".
# Up to two words before "appointment" covers "phone call appointment with X" too.
APPOINTMENT_LEAD_RE = re.compile(r"^(?:[a-z0-9]+\s+){0,2}appointment(?:\s+with)?\s+")


def _appointment_party(name: str) -> str:
    """The distinctive half of an appointment title: who it is with."""
    return APPOINTMENT_LEAD_RE.sub("", name or "").strip()


def _same_calendar_event(proposed_summary: str, existing_summary: str) -> bool:
    """Match the same named event and venue despite harmless presentation drift."""
    proposed_name, proposed_venue = _calendar_title_parts(proposed_summary)
    existing_name, existing_venue = _calendar_title_parts(existing_summary)
    if not all((proposed_name, proposed_venue, existing_name, existing_venue)):
        return False
    name_matches = (proposed_name == existing_name or
                    (min(len(proposed_name), len(existing_name)) >= 12 and
                     (proposed_name in existing_name or existing_name in proposed_name)))
    venue_matches = (proposed_venue == existing_venue or
                     proposed_venue in existing_venue or existing_venue in proposed_venue)
    if name_matches and venue_matches:
        return True
    # 2026-10-06 (Dr. Example again): the booked event read "Medical appointment with Dr.
    # Example <> the user @ Example Clinic..." and the reminder proposed "Doctor
    # appointment with Dr. Example <> the user @ Example Clinic...". Same venue,
    # same person, same 12:30 slot — but only the first word differed, so neither name
    # contained the other and the substring rule above missed it. Compare who the
    # appointment is WITH once the generic lead-in is dropped. Guarded on the venue being
    # identical and the party being distinctive, so two different providers at one clinic
    # still cannot collapse into each other.
    if proposed_venue == existing_venue:
        proposed_party = _appointment_party(proposed_name)
        existing_party = _appointment_party(existing_name)
        if (proposed_party and existing_party
                and min(len(proposed_party), len(existing_party)) >= MIN_CROSS_FIELD_PARTY
                and (proposed_party == existing_party
                     or proposed_party in existing_party or existing_party in proposed_party)):
            return True
    # 2026-09-23 (Dr. Example): the same appointment arrived with the fields
    # mirrored — the practitioner was the venue on the booked event ("Phone call
    # appointment <> the user @ Dr. Example") and sat inside the event name on
    # the proposal ("Doctor appointment with Dr. Example <> the user @
    # Example Clinic..."). Neither field lined up, so a duplicate went
    # out. Only name<->venue is compared here; name<->name and venue<->venue are
    # the strict rule above, so a shared generic phrase ("phone call
    # appointment") still cannot collapse two different providers' bookings.
    return (_mirrored_party(proposed_name, existing_venue) or
            _mirrored_party(existing_name, proposed_venue))


def _proposal_already_on_calendar(proposal: dict) -> bool:
    """True when a '<> the user @ <venue>' event for this proposal already exists on
    any of the user's calendars near the proposed start. Guards against a booking's later
    *reminder* email (which often carries no end time → a wrong default
    duration) creating a duplicate of the event the *confirmation* email already
    produced. Event names and venues are normalized ("&"/"and", parenthetical
    organizer labels) inside a ±90 min window so an unrelated Zoom meeting does
    not suppress a real proposal."""
    for ev in proposal.get("gog_events", []) or []:
        start = ev.get("from", "")
        summary = ev.get("summary", "")
        proposed_name, proposed_venue = _calendar_title_parts(summary)
        if not start or not proposed_name or not proposed_venue:
            continue
        try:
            sdt = datetime.fromisoformat(start)
        except Exception:
            continue
        w_from = (sdt - timedelta(minutes=90)).isoformat()
        w_to   = (sdt + timedelta(minutes=90)).isoformat()
        try:
            r = subprocess.run(
                ["/opt/homebrew/bin/gog", "cal", "ls", "--account", MY_EMAIL,
                 "--all", "--from", w_from, "--to", w_to, "-j", "--max", "50"],
                capture_output=True, text=True, timeout=15, env=gog_env()
            )
            if r.returncode != 0:
                continue
            for e in json.loads(r.stdout).get("events", []):
                if _same_calendar_event(summary, e.get("summary", "")):
                    return True
        except Exception as e:
            _log("Gmail", f"itinerary calendar-dedup check failed: {e}")
    return False


def _handle_itinerary(subject: str, body: str, itin_type: str, reference_dt=None) -> None:
    if ITINERARY_CANCEL_RE.search(subject) or ITINERARY_CANCEL_RE.search(body[:2000]):
        _handle_itinerary_cancellation(subject, body, reference_dt=reference_dt)
        return
    _log("Gmail", "extracting itinerary (Sonnet)…")
    extracted = _extract_itinerary(subject, body, reference_dt=reference_dt)
    if not extracted:
        _log("Gmail", "itinerary extraction failed — skipping")
        return
    # Freshness guard: airlines/hotels re-send confirmations, folios and "thanks
    # for staying" notes weeks after the fact (e.g. a hotel chain re-sent a past
    # stay's confirmation four weeks after checkout). Never propose calendar events for travel
    # that has already happened. 12h grace keeps a same-day / in-progress stay.
    latest = _itinerary_latest_dt(extracted)
    if latest is not None and latest < datetime.now(latest.tzinfo) - timedelta(hours=12):
        _log("Gmail", f"itinerary already past ({latest.date()}) — skipping proposal: {subject[:50]}")
        return
    proposal = _build_cal_proposal(extracted)
    if not proposal:
        _log("Gmail", "itinerary build failed — skipping")
        return
    if _proposal_already_on_calendar(proposal):
        _log("Gmail", f"itinerary already on calendar — skipping proposal: {subject[:50]}")
        return
    _publish_cal_proposal(proposal, lambda m: _log("Gmail", m))
    _log("Gmail", f"cal proposal sent [{itin_type}]: {subject[:50]}")


TELEVISIT_SENDER_RE = re.compile(r'televisit\.com', re.IGNORECASE)
TELEVISIT_SUBJECT_RE = re.compile(r'(video\s+)?appointment\s+reminder', re.IGNORECASE)

# Old format: "Monday, June 30, 2026 at 10:00 AM CDT"
_SP_DATE_RE = re.compile(
    r'(?:\w+,\s+)?(\w+ \d{1,2},?\s+\d{4})\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)\s*(\w+)?',
    re.IGNORECASE,
)
# New format: "at 2:00 pm (PT) on Thursday, July 2nd"
_SP_DATE_RE_NEW = re.compile(
    r'at\s+(\d{1,2}:\d{2}\s*[AP]M)\s*\((\w+)\)\s+on\s+\w+,\s+(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?',
    re.IGNORECASE,
)
# Fallback: GCal link embedded in body "dates=20260702T210000Z%2F20260702T215000Z"
_SP_GCAL_RE = re.compile(
    r'dates=(\d{8}T\d{6}Z)(?:%2F|/)(\d{8}T\d{6}Z)',
    re.IGNORECASE,
)
_SP_LINK_RE = re.compile(
    r'https?://(?:video\.televisit\.com[^\s<>"\']+'
    r'|[\w.-]*televisit\.com/video[^\s<>"\']+'
    r'|[\w.-]*zoom\.us/j/[^\s<>"\']+)',
    re.IGNORECASE,
)

_TZ_MAP = {
    "CDT": "America/Chicago", "CST": "America/Chicago",
    "EDT": "America/New_York", "EST": "America/New_York",
    "PDT": "America/Los_Angeles", "PST": "America/Los_Angeles",
    "MDT": "America/Denver", "MST": "America/Denver",
    "PT":  "America/Los_Angeles", "CT": "America/Chicago",
    "ET":  "America/New_York",    "MT": "America/Denver",
}


def _parse_televisit_dt(body: str, reference_dt=None):
    """Try three date formats from TeleVisit emails.
    Returns (start_dt, end_dt, iana_tz) or None.

    reference_dt: anchor for year inference on formats that omit the year.
    Pass the email's received timestamp in the catchup so old emails don't get
    bumped to next year. Defaults to now when called from live polling.
    """
    from zoneinfo import ZoneInfo
    # Old: "June 30, 2026 at 10:00 AM CDT" — year is explicit, no inference needed
    m = _SP_DATE_RE.search(body)
    if m:
        try:
            tz    = _TZ_MAP.get((m.group(3) or "CDT").upper(), "America/Chicago")
            naive = datetime.strptime(f"{m.group(1).replace(',', '')} {m.group(2).strip()}", "%B %d %Y %I:%M %p")
            dt    = naive.replace(tzinfo=ZoneInfo(tz))
            return dt, dt + timedelta(minutes=50), tz
        except Exception:
            pass
    # New: "at 2:00 pm (PT) on Thursday, July 2nd" — no year; infer from reference_dt
    m2 = _SP_DATE_RE_NEW.search(body)
    if m2:
        try:
            tz  = _TZ_MAP.get(m2.group(2).upper(), "America/Chicago")
            ref = (reference_dt or datetime.now(tz=ZoneInfo("UTC"))).astimezone(ZoneInfo(tz))
            naive = datetime.strptime(
                f"{m2.group(3)} {m2.group(4)} {ref.year} {m2.group(1).strip()}", "%B %d %Y %I:%M %p"
            )
            dt = naive.replace(tzinfo=ZoneInfo(tz))
            if dt < ref - timedelta(days=1):
                dt = dt.replace(year=ref.year + 1)
            return dt, dt + timedelta(minutes=50), tz
        except Exception:
            pass
    # Fallback: GCal link embedded in body
    m3 = _SP_GCAL_RE.search(body)
    if m3:
        try:
            start = datetime.strptime(m3.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
            end   = datetime.strptime(m3.group(2), "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
            return start, end, "America/Chicago"
        except Exception:
            pass
    return None


def _parse_ics_dt(raw: str) -> Optional[datetime]:
    """Parse an ICS DTSTART/DTEND value like 'TZID=America/Chicago:20260630T140000'."""
    try:
        if ":" in raw:
            params, val = raw.rsplit(":", 1)
            tz_str = None
            for part in params.split(";"):
                if part.upper().startswith("TZID="):
                    tz_str = part[5:]
            dt = datetime.strptime(val.strip(), "%Y%m%dT%H%M%S")
            if tz_str:
                from zoneinfo import ZoneInfo
                dt = dt.replace(tzinfo=ZoneInfo(tz_str))
            return dt
        return datetime.strptime(raw.strip(), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=__import__("zoneinfo").ZoneInfo("UTC")
        )
    except Exception:
        return None


def _ics_location_to_venue(location: str) -> str:
    """Convert an ICS LOCATION value to a short venue name for '<> the user @'."""
    loc = location.strip()
    if not loc:
        return ""
    low = loc.lower()
    if "zoom.us" in low or "zoom " in low:
        return "Zoom"
    if "meet.google.com" in low or "google meet" in low:
        return "Google Meet"
    if "teams.microsoft.com" in low or "microsoft teams" in low:
        return "Teams"
    if "webex.com" in low:
        return "Webex"
    # Physical address: use first line / first comma-segment as venue name
    first = loc.split("\n")[0].split(",")[0].strip()
    return first if first else loc[:40]


def _handle_invite_proposal(ics_content: str, sender: str) -> bool:
    """
    Parse a METHOD:REQUEST .ics invite and send a Telegram proposal with
    a '<> the user @' formatted title for the user to accept or decline.

    On ✅: confirm_cal_event.py calls _accept_and_rename_invite so the
    organiser sees 'accepted' and our calendar shows the correct format.
    """
    # Scope all field extraction to the VEVENT block only. A VTIMEZONE block
    # (present on any invite from a DST-observing timezone) has its own
    # DTSTART line for the DST transition rule (e.g. "DTSTART:19700308T020000")
    # that appears before the real VEVENT — an unscoped search over the whole
    # file grabs that instead of the actual event start time.
    vevent_match = re.search(r'BEGIN:VEVENT.*?END:VEVENT', ics_content, re.DOTALL | re.IGNORECASE)
    vevent = vevent_match.group(0) if vevent_match else ics_content

    uid      = _ics_field(vevent, "UID")
    raw_sum  = _ics_field(vevent, "SUMMARY") or "(no title)"
    raw_loc  = _ics_field(vevent, "LOCATION")
    raw_org_line = re.search(r'^ORGANIZER[^\r\n]*', vevent, re.MULTILINE | re.IGNORECASE)
    raw_org  = raw_org_line.group(0) if raw_org_line else ""
    raw_start = re.search(r'^DTSTART[^\r\n]*', vevent, re.MULTILINE | re.IGNORECASE)
    raw_end   = re.search(r'^DTEND[^\r\n]*',   vevent, re.MULTILINE | re.IGNORECASE)

    start_raw = raw_start.group(0).split(":", 1)[-1].strip() if raw_start else ""
    end_raw   = raw_end.group(0).split(":", 1)[-1].strip()   if raw_end   else ""

    # Parse DTSTART with optional TZID
    start_full = raw_start.group(0) if raw_start else ""
    end_full   = raw_end.group(0)   if raw_end   else ""
    start_dt   = _parse_ics_dt(start_full.split("DTSTART", 1)[-1].lstrip(";"))
    end_dt     = _parse_ics_dt(end_full.split("DTEND", 1)[-1].lstrip(";"))

    # Organiser display name (CN= parameter)
    org_name = _display_name(sender)
    cn_match = re.search(r'CN=([^:;]+)', raw_org, re.IGNORECASE)
    if cn_match:
        org_name = cn_match.group(1).strip().strip('"')

    # Build '<> the user @' formatted title
    venue        = _ics_location_to_venue(raw_loc) or org_name
    new_summary  = f"{raw_sum} <> the user @ {venue}"

    # Dedup: if a '<> the user @' event already exists at this time, just accept
    # and rename silently — no new Telegram proposal needed.
    if start_dt:
        try:
            w_from = (start_dt - timedelta(minutes=5)).isoformat()
            w_to   = (start_dt + timedelta(minutes=5)).isoformat()
            chk = subprocess.run(
                ["/opt/homebrew/bin/gog", "cal", "ls", "--account", MY_EMAIL,
                 "--from", w_from, "--to", w_to, "-j", "--max", "10"],
                capture_output=True, text=True, timeout=15, env=gog_env()
            )
            if chk.returncode == 0:
                existing = json.loads(chk.stdout).get("events", [])
                if any("<> the user @" in e.get("summary", "") for e in existing):
                    _log("Cal", f"invite dedup — already have event at {start_dt}, accepting+renaming silently")
                    _accept_and_rename_invite(uid, new_summary)
                    return True
        except Exception as e:
            _log("Cal", f"invite dedup check failed: {e}")

    # Build proposal
    try:
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
        start_display = start_dt.astimezone(ct).strftime("%a %b %-d · %-I:%M %p") if start_dt else "?"
        end_display   = end_dt.astimezone(ct).strftime("%-I:%M %p CT")            if end_dt   else "?"
        time_display  = f"{start_display} – {end_display}"
    except Exception:
        time_display = start_raw

    proposal_text = (
        f"📅 Invite from {org_name}\n\n"
        f"{raw_sum}\n"
        f"{time_display}\n"
        + (f"📍 {_ics_location_to_venue(raw_loc) or raw_loc}\n" if raw_loc else "")
        + f"\nWill save as:\n<b>{_h(new_summary)}</b>"
    )

    # ICS start/end ISO for the proposal (used if we need to create rather than rename)
    start_iso = start_dt.isoformat() if start_dt else ""
    end_iso   = end_dt.isoformat()   if end_dt   else ""

    proposal = {
        "proposal_text": proposal_text,
        "type":          "ics_invite",
        "ics_uid":       uid,
        "new_summary":   new_summary,
        "gog_events":    [],           # empty — confirm path uses accept+rename, not create
        "start_iso":     start_iso,
        "end_iso":       end_iso,
        "location":      _ics_location_to_venue(raw_loc),
    }
    _publish_cal_proposal(proposal, lambda m: _log("Cal", m))
    _log("Cal", f"invite proposal sent: {new_summary}")
    return True


def _handle_televisit(subject: str, body: str, ics_uid: str = "") -> bool:
    """
    Detect a TeleVisit video appointment reminder, build a calendar proposal,
    and send it with ✅/❌ inline buttons.
    Returns True if handled, False if parsing failed and caller should fall through.

    ics_uid: UID from the .ics attachment — stored in the proposal so
             confirm_cal_event.py can decline+delete the practice's event after
             creating our '<> the user @' version.
    """
    parsed = _parse_televisit_dt(body)
    if not parsed:
        _log("Gmail", "TeleVisit: date not found — falling through to itinerary path")
        return False

    aware, end_aware, iana_tz = parsed

    def _localize(tz):
        # Single source of truth: start_iso/end_iso and display always come from
        # the same tz conversion, so they can never show a different time than
        # the calendar event actually gets created with.
        s = aware.astimezone(tz)
        e = end_aware.astimezone(tz)
        return s.isoformat(), e.isoformat(), s.strftime("%b %-d at %-I:%M %p")

    try:
        from zoneinfo import ZoneInfo
        start_iso, end_iso, display = _localize(ZoneInfo("America/Chicago"))
    except Exception as e:
        _log("Gmail", f"TeleVisit: datetime format error {e} — falling through")
        return False

    # Dedup: if a '<> the user @' event already exists at this time, this is a
    # repeat reminder — decline/delete the ICS invite silently and stop.
    try:
        from zoneinfo import ZoneInfo as _ZI
        window_from = (aware - timedelta(minutes=5)).isoformat()
        window_to   = (aware + timedelta(minutes=5)).isoformat()
        cal_check = subprocess.run(
            ["/opt/homebrew/bin/gog", "cal", "ls", "--account", MY_EMAIL,
             "--from", window_from, "--to", window_to, "-j", "--max", "10"],
            capture_output=True, text=True, timeout=15, env=gog_env()
        )
        if cal_check.returncode == 0:
            existing = json.loads(cal_check.stdout).get("events", [])
            if any("<> the user @" in e.get("summary", "") for e in existing):
                _log("Gmail", f"TeleVisit repeat reminder — event already exists at {display}, declining ICS")
                if ics_uid:
                    _decline_and_delete_invite(ics_uid)
                return True
    except Exception as e:
        _log("Gmail", f"TeleVisit dedup check failed: {e}")

    link_match = _SP_LINK_RE.search(body)
    video_link = link_match.group(0) if link_match else ""

    in_person = _owner_at_second_home()

    if in_person:
        # At the second home base — Coach meets the user in person regardless of
        # what TeleVisit's reminder email says, since her scheduling
        # system always sends a video link by default.
        from zoneinfo import ZoneInfo as _ZI2
        pt = _ZI2("America/Los_Angeles")
        start_iso, end_iso, display = _localize(pt)

        desc = "Duration: 50 min | In-person"

        reminder_min = None
        try:
            sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")
            from drive_time import leave_by_minutes
            reminder_min = leave_by_minutes(COACH_OFFICE_ADDRESS)
        except Exception as e:
            _log("Cal", f"Coach drive-time error: {e}")

        proposal_text = (
            f"📅 Coaching appointment detected\n\n"
            f"Coach — {display}\n"
            f"In-person @ {COACH_OFFICE_NAME} (50 min)\n"
            + (f"🚗 Reminder set {reminder_min} min before (drive + 5 min buffer)\n" if reminder_min else "")
        )

        gog_events = [{
            "summary":          f"Coaching with Coach <> the user @ {COACH_OFFICE_NAME}",
            "from":             start_iso,
            "to":               end_iso,
            "location":         COACH_OFFICE_ADDRESS,
            "description":      desc,
            "start_timezone":   "America/Los_Angeles",
            "end_timezone":     "America/Los_Angeles",
            "reminder_minutes": reminder_min,
        }]
    else:
        desc = "Duration: 50 min"
        if video_link:
            desc += f" | {video_link}"

        proposal_text = (
            f"📅 Coaching appointment detected\n\n"
            f"Coach — {display}\n"
            f"Video session (50 min)\n"
            + (f"{video_link}\n" if video_link else "")
        )

        # Always store times in CT regardless of the coach's timezone in the email.
        # The <> the user @ format represents the user's local time; using the PT timezone from
        # the email caused a timezone mismatch that hid the event on phone calendars.
        from zoneinfo import ZoneInfo as _ZI2
        start_iso, end_iso, display = _localize(_ZI2("America/Chicago"))

        # Coach's practice runs its own video platform (video.televisit.example),
        # not Zoom — the join link in `video_link` is a TeleVisit URL.
        gog_events = [{
            "summary":        "Coaching with Coach <> the user @ TeleVisit",
            "from":           start_iso,
            "to":             end_iso,
            "location":       "TeleVisit",
            "description":    desc,
            "start_timezone": "America/Chicago",
            "end_timezone":   "America/Chicago",
        }]

    proposal = {"proposal_text": proposal_text, "gog_events": gog_events}
    if ics_uid:
        proposal["ics_uid"] = ics_uid
    _publish_cal_proposal(proposal, lambda m: _log("Gmail", m))
    _log("Gmail", f"TeleVisit cal proposal sent: {display}")
    return True


def _gmail_list_all(svc, q: str, page_size: int = 50) -> list[dict]:
    """Return every Gmail message id for a query, following all page tokens."""
    messages, page_token = [], None
    while True:
        kwargs = {"userId": "me", "q": q, "maxResults": page_size}
        if page_token:
            kwargs["pageToken"] = page_token
        result = svc.users().messages().list(**kwargs).execute()
        messages.extend(result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return messages


def _televisit_catchup() -> None:
    """On startup, scan the past 14 days for TeleVisit appointment reminders
    not yet in processed_ids. Catches emails that arrived during any downtime > 2h."""
    state     = _load()
    processed = set(state.get("processed_ids", []))

    try:
        svc    = _gmail()
        cutoff = int(time.time()) - 14 * 86400
        q      = f'from:televisit.example subject:"appointment reminder" after:{cutoff}'
        msgs   = _gmail_list_all(svc, q)
    except Exception as e:
        _log("Gmail", f"catchup scan error: {e}")
        _MONITOR.record_source("email:televisit", "unavailable", detail=str(e)[:300])
        raise

    from zoneinfo import ZoneInfo as _ZI
    now            = datetime.now(tz=_ZI("UTC"))
    proposed_times: set = set()
    new_ids, caught, fetch_errors = [], 0, 0

    for msg in msgs:
        mid = msg["id"]
        if mid in processed:
            new_ids.append(mid)
            continue

        try:
            full = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception as e:
            _log("Gmail", f"catchup fetch error {mid}: {e}")
            fetch_errors += 1
            continue

        hdrs    = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
        subject = hdrs.get("subject", "")
        body    = _get_body(full)

        # Use the email's received timestamp as the year anchor so old emails
        # don't get bumped to next year by the year-inference logic.
        email_ts = datetime.fromtimestamp(
            int(full.get("internalDate", 0)) / 1000, tz=_ZI("UTC")
        )

        # Skip past appointments and deduplicate same-time reminders within this run
        parsed = _parse_televisit_dt(body, reference_dt=email_ts)
        if parsed:
            appt_dt, _, _ = parsed
            if appt_dt < now:
                _log("Gmail", f"catchup: skipping past appointment — {subject[:40]}")
                new_ids.append(mid)
                continue
            appt_key = appt_dt.strftime("%Y%m%dT%H%M")
            if appt_key in proposed_times:
                _log("Gmail", f"catchup: dedup — already proposed {appt_key}")
                new_ids.append(mid)
                continue
            proposed_times.add(appt_key)

        ics_content = _get_ics_content(full, mid) if _has_ics_attachment(full) else None
        ics_uid     = _ics_field(ics_content, "UID") if ics_content else ""

        _log("Gmail", f"catchup: missed TeleVisit reminder — {subject[:50]}")
        try:
            handled = _handle_televisit(subject, body, ics_uid=ics_uid)
        except DeliveryFailed as e:
            _log("Gmail", f"catchup delivery failed, will retry: {e}")
            handled = False
        if handled:
            new_ids.append(mid)
            caught += 1
        # If not handled, leave out of new_ids so next restart retries

    state    = _load()
    existing = set(state.get("processed_ids", []))
    state["processed_ids"] = list(existing | set(new_ids))[-2000:]
    _save(state)
    _MONITOR.record_source(
        "email:televisit", "partial" if fetch_errors else "complete",
        coverage={"listed": len(msgs), "fetch_errors": fetch_errors})
    _log("Gmail", f"catchup scan: {len(msgs)} checked, {caught} missed → proposed")


# The Travel label tree (Gmail `label:` never matches sub-labels, so list each).
# _travel_label_catchup owns this mail's itineraries; the backstop still reads
# it for security alerts, which no catch-up handles.
# 2026-09-09 taxonomy split: airlines → Travel/Flights, hotels → Travel/Hotels, …
_TRAVEL_LABELS = _policy.TRAVEL_LABELS
_TRAVEL_LABEL_QUERY = " OR ".join(f"label:{l}" for l in _TRAVEL_LABELS)


def _travel_label_catchup() -> None:
    """Scan the last 21 days of mail under the Travel label tree for itinerary
    emails not yet in processed_ids. Several airline/hotel senders (e.g.
    an airline's notification address) have a user filter that files them under Travel and
    removes INBOX at delivery time, so run_gmail_check's `in:inbox` query never
    sees them — flight changes/cancellations silently never reach the itinerary
    pipeline. Same shape as _televisit_catchup, which fixed the identical
    problem for TeleVisit reminders (see LRN-20260716-001 in LEARNINGS.md).

    NOTE: Gmail `label:Travel` matches ONLY the exact label, never its
    sub-labels, so every Travel/* leaf must be listed explicitly (2026-09-09
    taxonomy split — airlines → Travel/Flights, hotels → Travel/Hotels, …)."""
    state     = _load()
    processed = set(state.get("processed_ids", []))

    try:
        svc    = _gmail()
        cutoff = int(time.time()) - 21 * 86400
        q      = f"({_TRAVEL_LABEL_QUERY}) after:{cutoff}"
        msgs   = _gmail_list_all(svc, q)
    except Exception as e:
        _log("Gmail", f"travel catchup error: {e}")
        _MONITOR.record_source("email:travel", "unavailable", detail=str(e)[:300])
        raise

    new_ids, handled_ids, caught, fetch_errors = [], [], 0, 0

    for msg in msgs:
        mid = msg["id"]
        if mid in processed:
            new_ids.append(mid)
            continue

        try:
            full = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception as e:
            _log("Gmail", f"travel catchup fetch error {mid}: {e}")
            fetch_errors += 1
            continue

        hdrs    = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
        subject = hdrs.get("subject", "")
        body    = _get_body(full)

        # No subject-regex pre-filter here (unlike run_gmail_check): the Travel
        # label is already the user's own curated scope (10 msgs in 21 days, all
        # genuine airline/hotel mail — see diagnosis), and subject wording is
        # too unpredictable across senders for change/cancellation emails to
        # risk a second silent-miss on regex wording.
        itin = _check_itinerary(subject, body[:400])
        if not itin["is_itinerary"]:
            new_ids.append(mid)
            continue

        _log("Gmail", f"travel catchup: missed itinerary [{itin['type']}] — {subject[:50]}")
        try:
            _handle_itinerary(subject, body, itin["type"], reference_dt=_received_dt(full))
        except DeliveryFailed as e:
            _log("Gmail", f"travel catchup delivery failed, will retry: {e}")
            continue
        new_ids.append(mid)
        handled_ids.append(mid)
        caught += 1

    state    = _load()
    existing = set(state.get("processed_ids", []))
    state["processed_ids"] = list(existing | set(new_ids))[-2000:]
    # Only messages this path actually acted on are "owned" by it; the backstop
    # reads this to decide whether a Travel-labeled message still needs it.
    state["travel_handled_ids"] = list(
        set(state.get("travel_handled_ids", [])) | set(handled_ids))[-2000:]
    _save(state)
    _MONITOR.record_source(
        "email:travel", "partial" if fetch_errors else "complete",
        coverage={"listed": len(msgs), "fetch_errors": fetch_errors})
    _log("Gmail", f"travel catchup: {len(msgs)} checked, {caught} missed → proposed")


def _opentable_conf(body: str) -> Optional[str]:
    """Pull the OpenTable reservation confirmation number out of an email body.

    Both confirmation and cancellation emails carry it, labelled
    "Confirmation number" / "Confirmation #" near a 4-7 digit number. Used to
    match a confirmation against a later cancellation for the same booking."""
    if not body:
        return None
    m = re.search(r"[Cc]onfirmation\s*(?:number|#|no\.?|code)?\D{0,20}(\d{4,7})", body)
    return m.group(1) if m else None


def _reservations_label_catchup() -> None:
    """Scan recent OpenTable confirmation mail for restaurant reservations not
    yet in processed_ids and propose a house-style calendar event.

    The user has two Gmail filters on `from:@opentable.com` that remove INBOX (one
    also files the mail under the 'Notifications' label) at delivery time, so
    run_gmail_check's `in:inbox` query never sees an OpenTable confirmation —
    the restaurant-itinerary pipeline silently never fires for a reservation
    booked directly on OpenTable (only reservations booked through the
    restaurant-booking skill get a calendar event). Same root cause and same
    shape as _travel_label_catchup / _televisit_catchup: a filter strips
    INBOX before the poll can see the message.

    Scoped to the opentable.com sender rather than the whole 'Notifications'
    label — that label is broad and mostly noise, and OpenTable is the exact
    sender the filters divert.

    Window is deliberately short (2 days). The daemon polls every 60 s and its
    realistic downtime is minutes-to-hours, so 2 days is ample headroom to
    catch a confirmation that landed during an outage — while keeping the scan
    away from older mail, where a reservation may since have been changed or
    cancelled and re-proposing it would be worse than missing it.

    The window alone is not a sufficient guard: a reservation booked and then
    cancelled inside the same 2 days (e.g. a restaurant-booking skill test run)
    leaves a live confirmation email that this scan would happily re-propose.
    So before proposing anything we first collect the confirmation numbers of
    every OpenTable *cancellation* email in the window and skip any confirmation
    whose number appears there. We also dedupe by confirmation number within a
    single run so a rebooked-under-a-new-number pair doesn't double-propose."""
    state     = _load()
    processed = set(state.get("processed_ids", []))

    try:
        svc  = _gmail()
        q    = "from:opentable.com newer_than:2d"
        msgs = _gmail_list_all(svc, q)
    except Exception as e:
        _log("Gmail", f"reservations catchup error: {e}")
        _MONITOR.record_source("email:reservations", "unavailable", detail=str(e)[:300])
        raise

    # Phase 1: fetch every message once, and build the set of confirmation
    # numbers that have since been cancelled.
    fetched, cancelled_confs, fetch_errors = {}, set(), 0
    for msg in msgs:
        mid = msg["id"]
        try:
            full = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception as e:
            _log("Gmail", f"reservations catchup fetch error {mid}: {e}")
            fetch_errors += 1
            continue
        hdrs    = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
        subject = hdrs.get("subject", "")
        body    = _get_body(full)
        fetched[mid] = (subject, body, _received_dt(full))
        if re.search(r"cancell?ation|cancell?ed", subject, re.IGNORECASE):
            conf = _opentable_conf(body)
            if conf:
                cancelled_confs.add(conf)

    new_ids, caught, seen_confs = [], 0, set()

    for msg in msgs:
        mid = msg["id"]
        if mid in processed:
            new_ids.append(mid)
            continue
        if mid not in fetched:
            continue   # fetch failed — retry next cycle, never mark it processed

        subject, body, recv_dt = fetched[mid]

        # Only act on genuine confirmations — skip review requests, waitlist
        # pings, marketing, and cancellation notices (a cancellation has no
        # forward-looking event to add; let it fall through untouched).
        if not re.search(r"reservation confirm|confirmed for|you.?re confirmed|"
                         r"your (?:table|reservation) (?:at|for)", subject, re.IGNORECASE):
            new_ids.append(mid)
            continue

        conf = _opentable_conf(body)
        if conf and conf in cancelled_confs:
            _log("Gmail", f"reservations catchup: skipping cancelled reservation "
                          f"(conf {conf}) — {subject[:50]}")
            new_ids.append(mid)
            continue
        if conf and conf in seen_confs:
            _log("Gmail", f"reservations catchup: skipping duplicate (conf {conf}) — {subject[:50]}")
            new_ids.append(mid)
            continue

        itin = _check_itinerary(subject, body[:400])
        if not itin["is_itinerary"] or itin["type"] != "restaurant":
            new_ids.append(mid)
            continue

        _log("Gmail", f"reservations catchup: missed restaurant reservation — {subject[:50]}")
        try:
            _handle_itinerary(subject, body, itin["type"], reference_dt=recv_dt)
        except DeliveryFailed as e:
            _log("Gmail", f"reservations catchup delivery failed, will retry: {e}")
            continue
        if conf:
            seen_confs.add(conf)
        new_ids.append(mid)
        caught += 1

    state    = _load()
    existing = set(state.get("processed_ids", []))
    state["processed_ids"] = list(existing | set(new_ids))[-2000:]
    _save(state)
    _MONITOR.record_source(
        "email:reservations", "partial" if fetch_errors else "complete",
        coverage={"listed": len(msgs), "fetch_errors": fetch_errors})
    _log("Gmail", f"reservations catchup: {len(msgs)} checked, {caught} missed → proposed")


# ── Generic "labeled-out" backstop ────────────────────────────────────────────
# Any Gmail filter with removeLabelIds:["INBOX"] hides its mail from
# run_gmail_check(), whose query is `in:inbox`. Dedicated catch-ups own slices
# of that mail (TeleVisit reminders, OpenTable confirmations, Travel
# itineraries, and the finance watcher crons) — see email_policy.DEDICATED_PATHS.
# This is the generic net for everything else that gets routed straight to a
# label:
#   • a security / fraud / payment-failure / account-access alert — from ANY
#     label or sender, Travel and dedicated senders included, because no
#     catch-up handles security mail
#   • a material record (statement, trade, bill, delivery, booking change…)
#     that no dedicated catch-up owns
#   • a real 1:1 note from a human at an otherwise-filtered domain
#     (e.g. a relationship manager @bank.example, an advisor @advisor.example)
# It runs the SAME L1 + L2 gate as the inbox loop. The backstop only notifies on:
#   • an L2 "action" verdict (someone genuinely needs the user),
#   • a security subject (real-time 🔒 ping),
#   • a VIP sender (floored to FYI),
#   • a material subject (real-time for the REALTIME_MATERIAL set, otherwise
#     the 6pm FYI digest), or an L2 "fyi" on a known finance sender.
# Marketing and surveys are dropped (policy 2 and 6). Mail a dedicated catch-up
# owns is left to it so nothing is announced twice.
#
# No draft is written here (the inbox loop's draft/send-button machinery is
# deliberately not duplicated) — an "action" verdict still notifies, and the user
# replies from Gmail. Dedup is a separate `backstop_ids` list so this never
# collides with the other catchups' `processed_ids` bookkeeping. The scan
# resumes from `backstop_last_scan_epoch`, so an outage longer than LOOKBACK_S
# cannot silently skip an archived security alert.

# Shared deterministic policy (email_policy): security/material mail surfaces
# even when bulk headers would otherwise stop it before L2; marketing is not.
_SECURITY_SUBJECT_RE = _policy.SECURITY_SUBJECT_RE
_MATERIAL_ACCOUNT_SUBJECT_RE = _policy.MATERIAL_SUBJECT_RE
BACKSTOP_CURSOR_OVERLAP_S = 600
LABEL_CACHE_TTL_S = 3600
_label_cache: dict = {"names": {}, "fetched_at": 0.0}


def _is_routine_subject(sender: str, subject: str) -> bool:
    """Mail this sender sends on a schedule the user already knows about.

    `routine_re` in senders.json (e.g. a crypto exchange's "^You (bought|sold) " for his
    thrice-daily recurring-buy receipts) marks subjects that are records, not news. They
    are still filed and labelled; they simply stop earning an override into his
    notifications.

    This exists because closing ONE door was not enough. The 2026-09-21 fix made
    `_known_finance_sender` honour routine_re, but a routine subject that also
    looks like money movement ("You bought <asset>" matches MATERIAL_SUBJECT_RE)
    kept overriding as a *material update* instead, and the user went on getting all
    three every day until 2026-10-04. Security is deliberately NOT routed
    through here: "Confirm your new withdrawal address" from the same sender
    must stay loud."""
    verdict = _ff.classify_sender(_ff.extract_address(sender), subject, _sender_cfg())
    routine = (verdict or {}).get("routine_re")
    return bool(routine and re.search(routine, subject or "", re.I))


def _remember_announced(mid: str) -> None:
    """Record that the user has been told about this message.

    The labelled-out backstop reads this so a message cannot be announced twice
    when a Gmail filter moves it after the inbox loop has already handled it.
    """
    state = _load()
    ids = state.setdefault("notified_ids", [])
    if mid not in ids:
        ids.append(mid)
        state["notified_ids"] = ids[-2000:]
        _save(state)


def _silently_ingested(sender: str, subject: str) -> bool:
    """True when an approved finance rule files this mail AND a watcher ingests it."""
    try:
        route = _finance_mail.classify(_extract_email(sender), subject)
    except Exception:  # noqa: BLE001 — a missing rule file must not stop the daemon
        return False
    return route.get("action") == "file" and bool(route.get("silent_ingest"))


def _known_finance_sender(sender: str, subject: str) -> bool:
    """A known finance sender, EXCEPT for its routine paperwork.

    Being known-finance overrides the no-reply L1 skip, which is right for a
    statement or a fraud notice and wrong for a recurring receipt. The user's exchange
    recurring buys fire three times a day; registering that exchange as a finance sender
    (2026-09-21, for a recurring-buy report) is what started pushing "You bought <asset>"
    past the no-reply gate and into his notifications, after months of it being
    skipped. A sender can now mark those subjects `routine_re` in senders.json:
    they are still filed and still labelled, they simply stop earning the
    override. Security and material mail from the same sender is unaffected —
    those carry their own overrides and are matched on the subject, not here."""
    verdict = _ff.classify_sender(_ff.extract_address(sender), subject, _sender_cfg())
    if not verdict or verdict.get("area") != "finance":
        return False
    return not _is_routine_subject(sender, subject)


def _label_names(svc, label_ids) -> list:
    """Map Gmail label ids to names (cached) for the shared coverage policy."""
    if time.time() - _label_cache["fetched_at"] > LABEL_CACHE_TTL_S:
        try:
            labels = svc.users().labels().list(userId="me").execute().get("labels", [])
            _label_cache["names"] = {lb["id"]: lb["name"] for lb in labels}
            _label_cache["fetched_at"] = time.time()
        except Exception as e:
            _log("Gmail", f"label list error: {e}")
    return [_label_cache["names"].get(lid, lid) for lid in (label_ids or [])]


def _backstop_since(state: dict, now: Optional[int] = None) -> int:
    """Resume from the last completed scan (with overlap), bounded by the inbox
    recovery window. First run falls back to LOOKBACK_S."""
    now = int(time.time()) if now is None else now
    last = int(state.get("backstop_last_scan_epoch") or 0)
    start = last - BACKSTOP_CURSOR_OVERLAP_S if last else now - LOOKBACK_S
    return max(start, now - INBOX_LOOKBACK_S)


def _labeled_out_backstop() -> None:
    state = _load()
    scan_started = int(time.time())
    since = _backstop_since(state, scan_started)
    q = (f"after:{since} -in:inbox -in:sent -in:chats -in:draft "
         f"-in:trash -in:spam -from:me")

    try:
        svc  = _gmail()
        msgs, page_token = [], None
        while True:
            kw: dict = {"userId": "me", "q": q, "maxResults": 50}
            if page_token:
                kw["pageToken"] = page_token
            r = svc.users().messages().list(**kw).execute()
            msgs.extend(r.get("messages", []))
            page_token = r.get("nextPageToken")
            if not page_token:
                break
    except Exception as e:
        _log("Gmail", f"backstop scan error: {e}")
        return

    seen  = set(state.get("backstop_ids", []))
    travel_seen = set(state.get("processed_ids", []))
    already_announced = set(state.get("notified_ids", []))
    travel_handled = set(state.get("travel_handled_ids", []))
    new_ids, notified, cheap_skipped = [], 0, 0
    hold_cursor = False   # a message still needs a retry — keep it in the window

    for msg in msgs:
        mid = msg["id"]
        if mid in seen:
            new_ids.append(mid)
            continue
        # 2026-10-06: a brokerage statement reached the user twice. The inbox loop
        # announced it, a Gmail filter then labelled it out, and this sweep — which
        # dedups on its own `backstop_ids` — announced it again. Anything already
        # announced is never announced again here. (processed_ids is deliberately
        # NOT used: the travel catch-up records messages there that it looked at and
        # left for this sweep.)
        if mid in already_announced:
            new_ids.append(mid)
            continue
        new_ids.append(mid)

        try:
            full = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception as e:
            # Leave it out of backstop_ids: a transient fetch failure must be
            # retried, never recorded as seen.
            _log("Gmail", f"backstop fetch error {mid}: {e}")
            new_ids.remove(mid)
            hold_cursor = True
            continue

        hdrs    = {h["name"].lower(): h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        sender  = hdrs.get("from", "unknown")
        subject = hdrs.get("subject", "(no subject)")
        address = _ff.extract_address(sender)
        labels  = _label_names(svc, full.get("labelIds", []))
        kind    = _policy.classify_subject(subject, full.get("labelIds", []))
        disposition = _policy.backstop_disposition(
            kind, address, subject, labels, message_id=mid)
        security = kind == "security"

        if disposition == "travel":
            if mid not in travel_seen:
                # The travel catch-up hasn't looked at it yet (it runs first each
                # cycle, so this is a race or its failure) — don't decide now.
                new_ids.remove(mid)
                hold_cursor = True
                continue
            if mid in travel_handled:
                continue
            # The travel catch-up looked and did nothing: it is ours now.
            disposition = _policy.backstop_disposition(
                kind, address, subject, [], message_id=mid)
        if disposition == "defer":
            # A live statement watcher has not acknowledged this notice yet.
            # Keep it retriable so its outage cannot turn into a silent loss.
            new_ids.remove(mid)
            hold_cursor = True
            continue
        if disposition == "owned":
            continue
        vip     = _is_vip(sender)
        routine = _is_routine_subject(sender, subject)
        finance = _known_finance_sender(sender, subject)
        if _is_inactive_sender(sender) and not security:
            cheap_skipped += 1
            continue
        passes, reason = _passes_filters(hdrs, subject)

        if not passes and not vip and (
                kind == "marketing" or (disposition == "classify" and not finance)):
            cheap_skipped += 1
            continue

        body   = _get_body(full)
        result = _classify(sender, subject, body[:500])
        tier   = result["tier"]

        if tier == "error" and not (disposition == "surface" or vip):
            # Do not add this message to backstop_ids. A transient model/API
            # failure must be retried on the next cycle rather than becoming a
            # permanent silent skip.
            new_ids.remove(mid)
            hold_cursor = True
            _log("Gmail", f"backstop classify retry [{result['reason']}]: {subject[:50]}")
            continue
        if tier == "error":
            tier = "fyi"

        # Bulk marketing never reaches here (skipped above); a human-looking
        # sender with a marketing-sounding subject is left to L2's judgement.
        realtime = True
        if tier == "action":
            pass
        elif security or vip:
            why = "VIP" if vip and not security else "security"
            _log("Gmail", f"backstop override [{why}]: {tier}→fyi — {subject[:50]}")
            tier = "fyi"
        elif kind == "material" and not routine:
            _log("Gmail", f"backstop override [material update]: {tier}→fyi — {subject[:50]}")
            tier = "fyi"
            realtime = _policy.is_realtime(kind, subject)
        elif finance and tier == "fyi":
            pass
        else:
            continue

        # Same rule as the inbox loop: mail an approved finance rule files with
        # `silent_ingest` is a feed, not news. Security and action mail never reach
        # here as silent — the rules' priority rule surfaces those first.
        if _silently_ingested(sender, subject) and not security:
            _crm_log(sender, subject, tier, mid, _received_dt(full).isoformat())
            _log("Gmail", f"backstop SILENT [finance feed]: {subject[:50]}")
            continue

        if not realtime:
            _queue_fyi(sender, subject, result.get("summary", "") or result.get("reason", ""), mid)
            _crm_log(sender, subject, tier, mid, _received_dt(full).isoformat())
            notified += 1
            _remember_announced(mid)
            _log("Gmail", f"backstop QUEUED [FYI]: {subject[:50]}")
            continue

        notification = _format_notification(
            tier, sender, subject, body, result["summary"], result["reason"], "", mid)
        tag = "🔒 " if security else ""
        if tag:
            notification = notification.replace("📧 <b>", f"📧 {tag}<b>", 1)
        if security and _auth_failed(hdrs):
            notification = f"{AUTH_WARNING}\n\n{notification}"

        if not _tg_send(notification):
            new_ids.remove(mid)
            hold_cursor = True
            _log("Gmail", f"backstop delivery failed, will retry: {subject[:50]}")
            continue
        _crm_log(sender, subject, tier, mid, _received_dt(full).isoformat())
        notified += 1
        _remember_announced(mid)
        _log("Gmail", f"backstop NOTIFIED [{tier.upper()}]: {subject[:50]}")
        time.sleep(0.5)

    state = _load()
    existing = set(state.get("backstop_ids", []))
    state["backstop_ids"] = list(existing | set(new_ids))[-2000:]
    if not hold_cursor:
        state["backstop_last_scan_epoch"] = scan_started
    _save(state)
    if notified or cheap_skipped:
        _log("Gmail",
             f"backstop: {len(msgs)} scanned, {cheap_skipped} L1-skipped, {notified} notified")


JOBS_FILE        = Path("~/.openclaw/cron/jobs.json")
TRAVEL_CFG_FILE  = Path("~/.openclaw/workspace/Skills/email-monitor/travel_config.json")
TRAVEL_REMINDER  = "~/.openclaw/workspace/Skills/email-monitor/scripts/travel_reminder.py"
PROMPT_FLIGHT_LOG = "~/.openclaw/workspace/Skills/email-monitor/scripts/prompt_flight_log.py"
PROMPT_CHECKIN   = "~/.openclaw/workspace/Skills/email-monitor/scripts/prompt_checkin.py"
SEND_TELEGRAM    = "~/.openclaw/workspace/Skills/shared/send_telegram.py"
RUN_AND_EXTRACT  = "~/.openclaw/workspace/Skills/shared/run_and_extract.py"
DETECT_TRAVEL    = "~/.openclaw/workspace/Skills/hevy-workout/scripts/detect_travel.py"


def _travel_cfg() -> dict:
    try:
        return json.loads(TRAVEL_CFG_FILE.read_text())
    except Exception:
        return {}


def _tomtom_drive_minutes(home_lat: float, home_lng: float,
                           apt_lat: float,  apt_lng: float,
                           key: str) -> Optional[int]:
    import urllib.request as ureq, math
    url = (
        f"https://api.tomtom.com/routing/1/calculateRoute/"
        f"{home_lat},{home_lng}:{apt_lat},{apt_lng}/json"
        f"?key={key}&traffic=true&travelMode=car"
    )
    try:
        with ureq.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        seconds = data["routes"][0]["summary"]["travelTimeInSeconds"]
        return max(10, math.ceil(seconds / 60))
    except Exception as e:
        _log("Cal", f"TomTom error: {e}")
        return None


def _home_for_origin(origin_code: str, cfg: dict) -> Optional[dict]:
    """Returns None for an origin that isn't one of the user's recognized home-base
    airports — e.g. the return leg of a trip, departing a city he's only
    visiting. Callers must not assume a home address/coordinates exist in that
    case (see _schedule_flight_reminders: falls back to fallback_drive_minutes
    instead of computing a real-world drive from a home city that may be
    thousands of miles from where the user actually is)."""
    if origin_code in cfg.get("dallas_airports", []):
        return cfg["homes"]["dallas"]
    if origin_code in cfg.get("la_airports", []):
        return cfg["homes"]["la"]
    return None


def _lodging_for_trip(dep_dt: datetime, cfg: dict) -> Optional[dict]:
    """Looks for a hotel check-in/check-out pair already on the calendar that
    covers this flight's departure date, and if found, returns its geocoded
    address as a home-shaped dict ({lat, lng, address, label}) — a drop-in
    replacement for _home_for_origin's return value, used when a flight
    departs a city the user is only visiting (see _schedule_flight_reminders).

    Returns None on any failure (no hotel found, stale/already-checked-out
    hotel, geocode failure) — callers fall back to the tier-based estimate."""
    lookback_days = cfg.get("hotel_lookback_days", 21)

    try:
        r = subprocess.run(
            ["/opt/homebrew/bin/gog", "cal", "ls", "--account", MY_EMAIL,
             "--from", (dep_dt - timedelta(days=lookback_days)).isoformat(),
             "--to",   (dep_dt + timedelta(days=1)).isoformat(),
             "--query", "Check-in", "-j", "--max", "25"],
            capture_output=True, text=True, timeout=20, env=gog_env()
        )
        if r.returncode != 0:
            return None
        events = json.loads(r.stdout).get("events", [])
    except Exception as e:
        _log("Cal", f"lodging lookup error: {e}")
        return None

    candidate = None
    for ev in events:
        summary = ev.get("summary", "")
        if "Check-in" not in summary or "<> the user @" not in summary:
            continue
        start_raw = ev.get("start", {}).get("dateTime", "")
        if not start_raw:
            continue
        try:
            start_dt = datetime.fromisoformat(start_raw)
            # A naive dateTime (no UTC offset) would raise TypeError against
            # dep_dt (always aware) below — treat that the same as an
            # unparseable date rather than letting it crash the whole
            # flight-reminder scheduling flow (see docstring: any failure
            # here should just fall back to the tier-based estimate).
            if start_dt > dep_dt:
                continue
        except Exception:
            continue
        if candidate is None or start_dt > candidate["start_dt"]:
            candidate = {"start_dt": start_dt, "summary": summary,
                         "location": ev.get("location", "")}

    if not candidate or not candidate["location"]:
        return None

    # Reject a stale hotel — a checkout for the same location that already
    # happened before this flight's departure means the stay is over.
    try:
        r = subprocess.run(
            ["/opt/homebrew/bin/gog", "cal", "ls", "--account", MY_EMAIL,
             "--from", candidate["start_dt"].isoformat(),
             "--to",   (dep_dt + timedelta(days=1)).isoformat(),
             "--query", "Check-out", "-j", "--max", "25"],
            capture_output=True, text=True, timeout=20, env=gog_env()
        )
        if r.returncode == 0:
            checkouts = json.loads(r.stdout).get("events", [])
            for ev in checkouts:
                if ev.get("location", "") != candidate["location"]:
                    continue
                co_raw = ev.get("start", {}).get("dateTime", "")
                if not co_raw:
                    continue
                co_dt = datetime.fromisoformat(co_raw)
                if co_dt < dep_dt:
                    return None
    except Exception as e:
        _log("Cal", f"lodging checkout check error: {e}")
        return None

    sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")
    try:
        from drive_time import geocode
        coords = geocode(candidate["location"])
    except Exception as e:
        _log("Cal", f"lodging geocode error: {e}")
        return None
    if not coords:
        return None

    label = candidate["summary"].split(" Check-in")[0].strip() or "Hotel"
    return {"lat": coords[0], "lng": coords[1], "address": candidate["location"], "label": label}


def _checkin_url(flight_number: str, confirmation: str, cfg: dict) -> str:
    code = flight_number.split()[0] if " " in flight_number else flight_number[:2]
    tmpl = cfg.get("airline_checkin", {}).get(code, "")
    return tmpl.replace("{conf}", confirmation) if tmpl and confirmation else tmpl


def _uber_link(airport_name: str, apt_lat: float, apt_lng: float) -> str:
    import urllib.parse
    enc = urllib.parse.quote(airport_name)
    return (
        f"https://m.uber.com/looking?"
        f"dropoff[formatted_address]={enc}"
        f"&dropoff[latitude]={apt_lat}"
        f"&dropoff[longitude]={apt_lng}"
    )


def _to_utc_iso(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write_job(job: dict) -> None:
    """Create a one-shot cron job via the `openclaw cron` CLI.

    Used to write straight to cron/jobs.json, but the cron store migrated to
    state/openclaw.sqlite (managed by the Gateway) and that file no longer
    exists — every call silently raised FileNotFoundError, so flight check-in
    and day-of reminders stopped being scheduled with no visible failure
    (see LRN-20260716-001 in LEARNINGS.md). `openclaw cron add` is the current
    supported way to create jobs; verify the returned id to confirm it stuck.
    """
    schedule = job["schedule"]
    payload  = job["payload"]
    delivery = job["delivery"]

    cmd = [
        "/opt/homebrew/bin/openclaw", "cron", "add", job["name"],
        "--description",   job.get("description", ""),
        "--at",             schedule["at"],
        "--wake",           job.get("wakeMode", "now"),
        "--channel",        delivery.get("channel", "last"),
        "--to",              delivery.get("to", ""),
        "--account",        delivery.get("accountId", "default"),
        "--best-effort-deliver",
        "--json",
    ]
    if payload.get("kind") == "command":
        # Direct command payload — no LLM turn, script self-reports to Telegram.
        # This is the pattern the rest of the deployment uses (morning-briefing,
        # hydration, flight-detect); mode:none so there is no fallback announce
        # on top of the message the script already sent.
        cmd += ["--command-argv", json.dumps(payload["argv"])]
    else:
        cmd += [
            "--message", payload["message"],
            "--session", "isolated" if job.get("sessionTarget") == "isolated" else "main",
        ]
    if job.get("deleteAfterRun"):
        cmd.append("--delete-after-run")
    if payload.get("timeoutSeconds"):
        cmd += ["--timeout-seconds", str(payload["timeoutSeconds"])]
    if delivery.get("mode") == "announce":
        cmd.append("--announce")
    elif delivery.get("mode") == "none":
        # No fallback delivery — the script self-reports to Telegram. Without
        # this the CLI defaults delivery to "announce" and the user gets a second
        # (LLM-rendered) copy of every reminder.
        cmd.append("--no-deliver")

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"openclaw cron add failed: {r.stderr.strip() or r.stdout.strip()}")
    out = json.loads(r.stdout)
    if not out.get("id"):
        raise RuntimeError(f"openclaw cron add returned no job id: {r.stdout.strip()}")


def _cancel_stale_flight_reminders(confirmation: str, keep_names: set) -> None:
    """Cancel any existing checkin-*/dayof-* cron jobs for the same booking
    before scheduling new ones.

    Regression: an airline schedule change rebooked XX 100 (day 1) as
    XX 200 (day 5) under the same confirmation ABC123. The calendar event
    was correctly renamed/rescheduled and new checkin-XX200-.../dayof-
    XX200-... jobs were created, but the original checkin-XX100-.../
    dayof-XX100-... jobs were never cancelled — they fired independently on
    their original (now stale) schedule, sending the user a check-in prompt and a
    "leave by" travel reminder for a flight that no longer departed at that
    time. Matching on the confirmation number (which schedule changes keep)
    rather than the flight number/date (which they change) is what lets a
    renamed booking find its own stale jobs.
    """
    if not confirmation:
        return
    try:
        r = subprocess.run(
            ["/opt/homebrew/bin/openclaw", "cron", "list", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            _log("Cal", f"stale-reminder scan failed: {r.stderr.strip()}")
            return
        jobs = json.loads(r.stdout).get("jobs", [])
    except Exception as e:
        _log("Cal", f"stale-reminder scan error: {e}")
        return

    for job in jobs:
        name = job.get("name", "")
        if name in keep_names:
            continue
        if not name.startswith(("checkin-", "dayof-", "recheck-", "flightlog-")):
            continue
        payload = job.get("payload", {})
        # checkin-* is an agentTurn (text in .message); dayof-/recheck-/
        # flightlog-* are command payloads (confirmation is an argv element).
        haystack = payload.get("message", "") or " ".join(
            str(a) for a in payload.get("argv", [])
        )
        # Whole-token match, not bare substring — a confirmation number that
        # happens to be a substring of a *different* flight's confirmation
        # (e.g. "ABC1" inside "ABC123") would otherwise cancel that other
        # flight's reminders too.
        if not re.search(r"\b" + re.escape(confirmation) + r"\b", haystack):
            continue
        job_id = job.get("id", "")
        d = subprocess.run(
            ["/opt/homebrew/bin/openclaw", "cron", "rm", job_id],
            capture_output=True, text=True, timeout=30,
        )
        if d.returncode == 0:
            _log("Cal", f"cancelled stale reminder job: {name}")
        else:
            _log("Cal", f"failed to cancel stale reminder job {name}: {d.stderr.strip()}")


def _schedule_flight_reminders(spec: dict, override_home: Optional[dict] = None) -> None:
    import uuid, time as _time
    dep_iso  = spec.get("from", "")
    fn       = spec.get("_flight_number", "")
    orig     = spec.get("_orig", "")
    dest     = spec.get("_dest", "")
    dep_disp = spec.get("_dep_display", "")
    orig_tz  = spec.get("_orig_tz", "America/Chicago")

    try:
        dep_dt = datetime.fromisoformat(dep_iso)
    except Exception as e:
        _log("Cal", f"flight reminders datetime error: {e}")
        return

    now_aware = datetime.now(tz=dep_dt.tzinfo)
    cfg       = _travel_cfg()
    apt_info  = cfg.get("airports", {}).get(orig, {})
    apt_lat   = apt_info.get("lat", 0.0)
    apt_lng   = apt_info.get("lng", 0.0)
    apt_name  = apt_info.get("name", orig)
    apt_addr  = apt_info.get("address", orig)

    if override_home is not None:
        home, home_source = override_home, "calendar"
    else:
        home = _home_for_origin(orig, cfg)
        if home is not None:
            home_source = "home"
        else:
            home = _lodging_for_trip(dep_dt, cfg)
            home_source = "calendar" if home is not None else "tier"

    token      = cfg.get("tomtom_key", "")
    fallbacks  = cfg.get("fallback_drive_minutes", {})
    date_label  = dep_dt.strftime("%a %b %-d")
    slug        = fn.replace(" ", "")
    date_slug   = dep_dt.strftime("%Y%m%d")
    now_ms      = int(_time.time() * 1000)
    checked_bag = bool(spec.get("checked_bag", False))
    conf       = spec.get("description", "").split("Confirmation: ")[-1].split("|")[0].strip() \
                 if "Confirmation:" in spec.get("description", "") else ""

    # Cancel any checkin-*/dayof-* jobs left over from a prior flight number/date
    # under this same confirmation (e.g. an airline schedule change) before
    # scheduling the current ones — see _cancel_stale_flight_reminders docstring.
    _cancel_stale_flight_reminders(
        conf, keep_names={
            f"checkin-{slug}-{date_slug}", f"dayof-{slug}-{date_slug}",
            f"recheck-{slug}-{date_slug}", f"flightlog-{slug}-{date_slug}",
            f"traveldetect-{slug}-{date_slug}",
        },
    )

    # ── 1. Leave-for-airport calendar block ────────────────────────────────────
    # `home` is None when `orig` isn't one of the user's recognized home-base
    # airports — e.g. the return leg of a trip, departing a city he's only
    # visiting (no known lodging address/coordinates). Computing a TomTom
    # drive time in that case would measure a real-world route from a home
    # city that may be thousands of miles from where the user actually is, so
    # skip TomTom entirely and go straight to the per-airport local estimate.
    drive_min = None
    if home and token and apt_lat:
        drive_min = _tomtom_drive_minutes(home["lat"], home["lng"], apt_lat, apt_lng, token)
    if drive_min is None:
        drive_min   = fallbacks.get(orig, 30)
        drive_source = "static"
        drive_src   = "estimated"
    else:
        drive_source = "live"
        drive_src   = "Mapbox"

    # Backward-from-gate-close calculation (compute_leave_time.py). Replaces the
    # old flat `precheck_h*60 + drive + drive_buf` buffer, which had the user at the
    # gate ~45 min early. Security is the static per-airport p85 fallback here;
    # the T-3h day-of run (travel_reminder.py) layers in live TSA data.
    lt = leave_time_for_flight(
        cfg=cfg, flight_number=fn, orig=orig, dest=dest, dep_dt=dep_dt,
        drive_minutes=drive_min, drive_source=drive_source, spec=spec, stage="final",
        checked_bag=checked_bag,
    )
    leave_dt      = datetime.fromisoformat(lt["leave_home"])
    leave_end     = leave_dt + timedelta(minutes=30)
    leave_iso     = leave_dt.isoformat()
    leave_end_iso = leave_end.isoformat()
    leave_disp    = lt["leave_home_display"]

    leave_event_id = ""
    if leave_dt > now_aware:
        leave_summary  = f"Leave for {orig} <> the user @ {home.get('label','Home')}" if home else f"Leave for {orig} Airport <> the user"
        leave_location = home["address"] if home else apt_addr
        cmd = [
            "/opt/homebrew/bin/gog", "cal", "create", MY_EMAIL,
            "--account",        MY_EMAIL,
            "--summary",        leave_summary,
            "--from",           leave_iso,
            "--to",             leave_end_iso,
            "--start-timezone", orig_tz,
            "--end-timezone",   orig_tz,
            "--location",       leave_location,
            "--description",    f"Leave {leave_disp} for {dep_disp} dep · {one_line_reason(lt)} · drive {drive_src} · {fn}",
            "--reminder",       "popup:15m",
            "-j",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=gog_env())
            if r.returncode == 0:
                _log("Cal", f"leave-for-airport block created: {leave_disp} ({drive_min} min drive)")
                try:
                    leave_event_id = json.loads(r.stdout).get("event", {}).get("id", "")
                except Exception:
                    pass
            else:
                _log("Cal", f"leave-for-airport create failed: {r.stderr.strip()}")
        except Exception as e:
            _log("Cal", f"leave-for-airport error: {e}")

    # ── 2. 24h check-in reminder ───────────────────────────────────────────────
    checkin_dt = dep_dt - timedelta(hours=24)
    if checkin_dt > now_aware:
        ci_url   = _checkin_url(fn, conf, cfg)
        uber_url = _uber_link(apt_name, apt_lat, apt_lng) if apt_lat else ""
        ci_lines = [f"Check-in is open for {fn} {orig} → {dest} · {date_label} {dep_disp}."]
        if ci_url:
            ci_lines.append(f"Check in: {ci_url}")
        if uber_url:
            ci_lines.append(f"Book Uber: {uber_url}")
        # Rough leave-time so the user can plan the evening. Low confidence — uses
        # the static drive fallback and static security; the real number lands
        # in the T-3h day-of reminder (travel_reminder.py).
        try:
            rough = leave_time_for_flight(
                cfg=cfg, flight_number=fn, orig=orig, dest=dest, dep_dt=dep_dt,
                drive_minutes=fallbacks.get(orig, 30), drive_source="static",
                spec=spec, stage="rough", checked_bag=checked_bag,
            )
            ci_lines.append(
                f"Rough plan: leave ~{rough['leave_home_display']} "
                f"(≈{rough['breakdown']['total_minutes_before_departure']} min before dep; "
                f"low confidence, refined ~3h out)."
            )
        except Exception as e:
            _log("Cal", f"rough leave-time estimate skipped: {e}")
        if conf:
            # Also lets _cancel_stale_flight_reminders match this job by
            # confirmation number — without this, its plain substring check
            # ("confirmation in message") never matches the check-in job at
            # all (unlike the day-of job, whose stored command always passes
            # --confirmation), leaving a stale duplicate behind on any
            # reschedule (airline change, or the hotel-triggered upgrade in
            # _maybe_upgrade_flight_reminders).
            ci_lines.append(f"Confirmation: {conf}")
        # Only ask if a bag isn't already known about — no point re-asking a
        # question set_checked_bag.py already answered from something the user
        # said earlier during booking.
        if not checked_bag:
            ci_lines.append("Checking a bag this trip? Reply yes/no and I'll adjust your leave time.")
        ci_lines.append("Safe travels, Master Wayne. 🎩")
        ci_key = f"{slug}-{date_slug}"
        ci_argv = [
            "python3", PROMPT_CHECKIN,
            "--text", "\n".join(ci_lines), "--key", ci_key, "--flight", fn,
        ]
        if conf:
            ci_argv += ["--confirmation", conf]
        if not checked_bag:
            ci_argv += ["--ask-bag"]
        try:
            _write_job({
                "id":             str(uuid.uuid4()),
                "name":           f"checkin-{slug}-{date_slug}",
                "description":    f"Check-in reminder for {fn} {orig}→{dest} on {date_label}",
                "enabled":        True,
                "deleteAfterRun": True,
                "createdAtMs":    now_ms,
                "schedule":       {"kind": "at", "at": _to_utc_iso(checkin_dt)},
                "wakeMode":       "now",
                # Direct command payload + delivery mode "none": prompt_checkin.py
                # posts the reminder itself (via send_telegram.py) and, when a bag
                # isn't already known, writes pending_checked_bag.json so a yes/no
                # reply routes to set_checked_bag.py (see RULES.md). No LLM turn,
                # no "announce" fallback — announce prepends a "[cron:<id> <name>]"
                # provenance header to the message (see
                # feedback-telegram-reconnect-delivery-race). Matches the day-of /
                # recheck / flightlog jobs below.
                "payload":        {"kind": "command", "argv": ci_argv, "timeoutSeconds": 60},
                "delivery":       {"mode": "none", "channel": "telegram", "to": TG_CHAT_ID, "accountId": "default"},
                "state":          {},
            })
            _log("Cal", f"check-in job scheduled: {fn} at {checkin_dt.strftime('%b %-d %-I:%M %p')}")
        except Exception as e:
            _log("Cal", f"check-in job write error: {e}")

    # ── 3. Day-of reminder (3h before) ────────────────────────────────────────
    # Direct command payload (argv, not a shell string) + delivery mode "none":
    # travel_reminder.py sends its own formatted Telegram message. No LLM turn,
    # no `--announce` fallback on top of it (that pairing double-sends — see
    # feedback-nutrition-cron-double-delivery). Matches the morning-briefing /
    # hydration cron shape.
    def _opt(flag: str, val) -> list:
        # `openclaw cron add --command-argv` rejects empty strings in the array,
        # so an optional flag with no value (no confirmation, unknown terminal)
        # must be omitted entirely rather than passed as `--flag ""`.
        s = "" if val is None else str(val)
        return [flag, s] if s else []

    def _tr_argv(extra: list) -> list:
        av = [
            "python3", TRAVEL_REMINDER,
            "--flight", fn, "--origin", orig, "--dest", dest,
            "--departure", dep_iso,
            "--airport-lat", str(apt_lat), "--airport-lng", str(apt_lng),
            "--airport-name", apt_name,
        ]
        av += _opt("--confirmation", conf)
        if home:
            av += ["--home-lat", str(home["lat"]), "--home-lng", str(home["lng"])]
        if checked_bag:
            av += ["--checked-bag"]
        return av + extra

    dayof_dt = dep_dt - timedelta(hours=3)
    if dayof_dt > now_aware and apt_lat:
        try:
            _write_job({
                "id":             str(uuid.uuid4()),
                "name":           f"dayof-{slug}-{date_slug}",
                "description":    f"Day-of travel reminder for {fn} {orig}→{dest} on {date_label}",
                "enabled":        True,
                "deleteAfterRun": True,
                "createdAtMs":    now_ms,
                "schedule":       {"kind": "at", "at": _to_utc_iso(dayof_dt)},
                "wakeMode":       "now",
                "payload":        {"kind": "command", "argv": _tr_argv([]), "timeoutSeconds": 120},
                "delivery":       {"mode": "none", "channel": "telegram", "to": TG_CHAT_ID, "accountId": "default"},
                "state":          {},
            })
            _log("Cal", f"day-of job scheduled: {fn} at {dayof_dt.strftime('%b %-d %-I:%M %p')}")
        except Exception as e:
            _log("Cal", f"day-of job write error: {e}")

    # ── 3b. T-90m conditional re-check ────────────────────────────────────────
    # Same command plus --recheck: travel_reminder.py stays silent unless
    # traffic/checkpoint data moved the leave time > 15 min from the T-3h
    # estimate it stored.
    recheck_dt = dep_dt - timedelta(minutes=90)
    if recheck_dt > now_aware and apt_lat:
        try:
            _write_job({
                "id":             str(uuid.uuid4()),
                "name":           f"recheck-{slug}-{date_slug}",
                "description":    f"T-90m leave-time re-check for {fn} {orig}→{dest} on {date_label}",
                "enabled":        True,
                "deleteAfterRun": True,
                "createdAtMs":    now_ms,
                "schedule":       {"kind": "at", "at": _to_utc_iso(recheck_dt)},
                "wakeMode":       "now",
                "payload":        {"kind": "command", "argv": _tr_argv(["--recheck"]), "timeoutSeconds": 120},
                "delivery":       {"mode": "none", "channel": "telegram", "to": TG_CHAT_ID, "accountId": "default"},
                "state":          {},
            })
            _log("Cal", f"recheck job scheduled: {fn} at {recheck_dt.strftime('%b %-d %-I:%M %p')}")
        except Exception as e:
            _log("Cal", f"recheck job write error: {e}")

    # ── 4. Post-flight capture prompt (departure + 45m) ───────────────────────
    # Asks the user for two numbers (security wait, gate idle) to feed the feedback
    # loop — see prompt_flight_log.py / log_flight.py.
    postflight_dt = dep_dt + timedelta(minutes=45)
    if postflight_dt > now_aware:
        term_for_log = ""
        try:
            from airport_timing import airline_code as _alc, resolve_terminal as _rt
            term_for_log = _rt(cfg, _alc(fn), orig)
        except Exception:
            pass
        try:
            _write_job({
                "id":             str(uuid.uuid4()),
                "name":           f"flightlog-{slug}-{date_slug}",
                "description":    f"Post-flight timing log prompt for {fn} {orig}→{dest} on {date_label}",
                "enabled":        True,
                "deleteAfterRun": True,
                "createdAtMs":    now_ms,
                "schedule":       {"kind": "at", "at": _to_utc_iso(postflight_dt)},
                "wakeMode":       "now",
                "payload":        {"kind": "command", "argv": [
                    "python3", PROMPT_FLIGHT_LOG,
                    "--flight", fn, "--origin", orig, "--dest", dest,
                    "--departure", dep_iso,
                    *_opt("--terminal", term_for_log),
                    *_opt("--confirmation", conf),
                ], "timeoutSeconds": 60},
                "delivery":       {"mode": "none", "channel": "telegram", "to": TG_CHAT_ID, "accountId": "default"},
                "state":          {},
            })
            _log("Cal", f"flight-log prompt scheduled: {fn} at {postflight_dt.strftime('%b %-d %-I:%M %p')}")
        except Exception as e:
            _log("Cal", f"flight-log job write error: {e}")

    # ── 5. Post-landing travel-detect re-run (landing + 30m) ──────────────────
    # hevy-travel-detect-545am only runs once a day, so a same-day flight
    # (leave the other home city in the morning, land home in the afternoon — exactly what
    # happened 2026-08-29) left every managed cron on the stale departure-city
    # timezone until the next 5:45am run: the 8pm evening briefing that night
    # fired on Pacific wall-clock time (10pm Central) instead of Central. This
    # one-shot re-run closes that gap same-day, right after each flight lands,
    # instead of waiting up to ~24h for the next scheduled check.
    #
    # Deliberately NOT in _cancel_stale_flight_reminders's confirmation-match
    # scan: unlike checkin-/dayof-/recheck-/flightlog-, this job's argv carries
    # no flight-specific content (just "run detect_travel.py" — no confirmation
    # number to embed without detect_travel.py's argparse erroring on an
    # unrecognized flag). A stale one left behind by a reschedule just re-runs
    # travel-detect once at a slightly-off time, which is harmless — the script
    # reads live calendar/config state fresh each run, so it can't send wrong
    # flight info the way the others could. It's still in keep_names below and
    # still delete_after_run, so it never accumulates.
    arr_iso = spec.get("to", "")
    try:
        arr_dt = datetime.fromisoformat(arr_iso) if arr_iso else dep_dt + timedelta(hours=3)
    except Exception:
        arr_dt = dep_dt + timedelta(hours=3)
    landing_dt = arr_dt + timedelta(minutes=30)  # deplane + get a signal
    if landing_dt > now_aware:
        try:
            _write_job({
                "id":             str(uuid.uuid4()),
                "name":           f"traveldetect-{slug}-{date_slug}",
                "description":    f"Post-landing travel/timezone re-check for {fn} {orig}→{dest} on {date_label}",
                "enabled":        True,
                "deleteAfterRun": True,
                "createdAtMs":    now_ms,
                "schedule":       {"kind": "at", "at": _to_utc_iso(landing_dt)},
                "wakeMode":       "now",
                "payload":        {"kind": "command", "argv": ["python3", RUN_AND_EXTRACT, DETECT_TRAVEL], "timeoutSeconds": 60},
                "delivery":       {"mode": "none", "channel": "telegram", "to": TG_CHAT_ID, "accountId": "default"},
                "state":          {},
            })
            _log("Cal", f"post-landing travel-detect job scheduled: {fn} at {landing_dt.strftime('%b %-d %-I:%M %p')}")
        except Exception as e:
            _log("Cal", f"post-landing travel-detect job write error: {e}")

    # Record this flight's scheduling state so a later-arriving hotel
    # confirmation can retroactively upgrade a tier-estimate reminder to a
    # real lodging address (see confirm_cal_event.py's hotel branch).
    if conf:
        try:
            state = _load()
            scheduled = state.get("scheduled_flights", {})
            # Pop before reassigning so an update to an existing confirmation
            # moves it to the end — otherwise Python dicts keep an existing
            # key at its original insertion position even after its value
            # changes, and the trim below would keep it ordered by first-seen
            # rather than most-recently-touched, potentially evicting a flight
            # updated moments ago in favor of an untouched older one.
            scheduled.pop(conf, None)
            scheduled[conf] = {
                "orig": orig, "dep_iso": dep_iso, "home_source": home_source,
                "leave_event_id": leave_event_id, "spec": spec,
            }
            state["scheduled_flights"] = dict(list(scheduled.items())[-200:])
            _save(state)
        except Exception as e:
            _log("Cal", f"scheduled_flights registry write error: {e}")


def _execute_cal_event(ev: dict, action_file: Path, reply_msg_id: Optional[int]) -> None:
    gog_events = ev.get("gog_events", [])
    created    = []
    for spec in gog_events:
        reminder_min = spec.get("reminder_minutes") or 15
        cmd = [
            "/opt/homebrew/bin/gog", "cal", "create", "owner@example.com",
            "--account", "owner@example.com",
            "--summary",        spec["summary"],
            "--from",           spec["from"],
            "--to",             spec["to"],
            "--start-timezone", spec.get("start_timezone", "America/Chicago"),
            "--end-timezone",   spec.get("end_timezone",   "America/Chicago"),
            "--reminder",       f"popup:{reminder_min}m",
        ]
        if spec.get("location"):
            cmd += ["--location", spec["location"]]
        if spec.get("description"):
            cmd += ["--description", spec["description"]]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=gog_env())
        if result.returncode != 0:
            raise RuntimeError(f"gog cal create failed for '{spec['summary']}': {result.stderr.strip()}")
        created.append(spec["summary"])
        _log("Cal", f"created: {spec['summary']}")
        if spec.get("type") == "flight":
            _schedule_flight_reminders(spec)
    action_file.unlink(missing_ok=True)
    if reply_msg_id is not None:
        if len(created) == 1:
            _tg_send(f"On the calendar, Master Wayne: {created[0]}. 🎩", reply_to=reply_msg_id)
        else:
            events_list = "\n".join(f"• {s}" for s in created)
            _tg_send(f"Added to your calendar, Master Wayne:\n{events_list} 🎩", reply_to=reply_msg_id)


# ── Claude ─────────────────────────────────────────────────────────────────────
CLASSIFY_PROMPT = """\
Classify this email for the user. He runs a small AI startup and is an active
investor and networker — AND he has an equally important personal life:
health care (doctors, coachs, trainers), family, home/apartment matters, personal
purchases and services he's actively engaged with. Both halves are "relevant." This is a
personal inbox, not a business-only one.

Tiers:
"action" — a real person is waiting on the user for a reply, decision, or next step (a question,
           a meeting request, a deal, an invite, a doctor/provider asking him something)
"fyi"    — directly relevant information the user should know: a security/access concern; a
           material update about his own account, money, investment, booking, health care,
           home, purchase, or active service; or a substantive personal message. No reply
           is needed, but the information affects something he owns, uses, or is doing.
"skip"   — anything that is not directly relevant: surveys and feedback requests, generic
           education, product announcements, promotions, rewards cross-sells, newsletters,
           cold outreach, engagement nudges, spam, or general company news with no concrete
           effect on the user.

A no-reply or automated notification is never "action", however urgent it reads — a
security alert, a verification code, or a "click here to verify" notice is information,
not a message waiting on the user's reply. Use "fyi" for those.

Having an account or relationship with the sender is not enough for FYI. A bank survey,
brokerage product promotion, provider newsletter, or "tell us what you think" request is
skip. A login alert, transaction, balance/funding change, trade/investment update tied to
The user, appointment change, reservation, bill, required document, or human note is relevant.
Personal life is as important as business, but it still must affect the user specifically.

Meeting requests:
"open"     — asking for availability generally
"specific" — proposing a specific time (extract as proposed_time)
null       — no meeting request

Write the summary as ONE line of at most 15 words, in plain words.
State only the fact that matters. Never restate the subject or name the sender.
Write to the user, never about him: no "The user", no "his account", no third person.
If he has to do something, lead with what it is and by when.
No preamble, no pleasantries, no closing.

JSON only — no markdown:
{"tier":"action"|"fyi"|"skip","meeting_request":"open"|"specific"|null,"proposed_time":"string or null","summary":"...","reason":"..."}"""

DRAFT_PROMPT = """\
Write a short email reply for the user. Voice: direct, warm, entrepreneurial. Under 100 words.
For meeting requests use [AVAILABILITY] as a placeholder where dates should go.
Keep the greeting line ("Sam,"), then go straight to the substance. No filler or
acknowledgement sentence between the greeting and the point.
End on the last sentence of the message. Never add a closing line or sign your name —
The user's email signature is appended after your text.
Return ONLY the reply text."""

AVAIL_PROMPT = """\
You are writing an email reply for the user. His voice is direct, warm, entrepreneurial.
Replace [AVAILABILITY] with the actual availability info provided. Under 120 words total.
Each opening is a window with a start and an end — offer it as a window ("Monday 9:00 AM–2:00 PM
CT"), never as a single start time, and never widen one past the end time given.
End on the last sentence of the message. Never add a closing line or sign your name —
The user's email signature is appended after your text.
Respond with ONLY the final reply text."""

# The user 2026-10-09: he told Archibald in Telegram to stop opening emails with filler
# ("not necessary: Thanks for the personal touch—I appreciate it"). remember.py filed it in
# standing-instructions.md, which Archibald reads before planning — and this daemon never read
# at all, so the next draft would have opened exactly the same way. Anything he tags [email]
# now reaches the writer that actually composes his mail. Re-read on mtime so a new rule
# applies without restarting the daemon.
STANDING_FILE = Path("~/.openclaw/workspace/memory/domains/standing-instructions.md")
_STANDING_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) \[([a-z]+)\] (.+)$")
STANDING_TOPICS = ("email",)
STANDING_MAX_CHARS = 1200
_standing_cache: dict = {"text": "", "mtime": None}


def _standing_email_rules() -> str:
    """His [email]-tagged standing instructions, as a prompt suffix ("" if none).

    Never raises: a missing or unreadable file means the baked-in voice rules in
    DRAFT_PROMPT still apply, which is the safe direction.
    """
    try:
        mtime = STANDING_FILE.stat().st_mtime
    except OSError:
        return _standing_cache["text"]
    if mtime == _standing_cache["mtime"]:
        return _standing_cache["text"]
    try:
        lines = STANDING_FILE.read_text().splitlines()
    except OSError:
        return _standing_cache["text"]
    rules = [m.group(3).strip() for m in (_STANDING_LINE.match(ln) for ln in lines)
             if m and m.group(2) in STANDING_TOPICS]
    text = ""
    if rules:
        body = "\n".join(f"- {r}" for r in rules)[:STANDING_MAX_CHARS]
        text = ("\n\nOwner's standing instructions for email, in his own words — "
                "follow them exactly:\n" + body)
    _standing_cache["text"], _standing_cache["mtime"] = text, mtime
    return text


GRAMMAR_PROMPT = """\
Fix grammar, spelling, and punctuation in this voice-dictated email draft. Preserve the exact
meaning, tone, and every detail. Do not rephrase or shorten. Return ONLY the corrected text."""

ITINERARY_CHECK_PROMPT = """\
Is this email a booking, itinerary confirmation, or calendar invitation containing specific calendar details?
Return JSON only: {"is_itinerary": true|false, "type": "flight"|"hotel"|"restaurant"|"event"|null}
flight=airline booking with departure/arrival times, hotel=hotel reservation with dates,
restaurant=dining reservation with date/time, event=ticket OR calendar invite/appointment with date and venue.
JSON only — no markdown:"""

ITINERARY_EXTRACT_PROMPT = """\
Extract all booking details from this email. Return JSON only — no markdown, no explanation.

For a ONE-WAY flight, return a single object:
{"type":"flight","airline":"Full Airline Name","flight_number":"WN 1234","origin_code":"DAL","dest_code":"LAX","origin_name":"Dallas Love Field","dest_name":"Los Angeles International Airport","departure_iso":"2026-06-30T06:05:00-05:00","arrival_iso":"2026-06-30T07:10:00-07:00","seat":"14A or null","cabin":"Economy|Business|First|null","confirmation":"CODE or null","origin_tz":"America/Chicago","dest_tz":"America/Los_Angeles"}

For a ROUND TRIP, return a JSON ARRAY with one object per leg — same schema per leg.

hotel:      {"type":"hotel","property_name":"Hotel Name","address":"full street address or null","checkin_date":"YYYY-MM-DD","checkout_date":"YYYY-MM-DD","checkin_time":"15:00 or null","checkout_time":"11:00 or null","confirmation":"CODE or null","timezone":"America/Chicago"}
restaurant: {"type":"restaurant","restaurant_name":"Name","address":"full street address or null","datetime_iso":"2026-06-24T19:00:00-05:00","party_size":2,"confirmation":"CODE or null","timezone":"America/Chicago"}
event:      {"type":"event","event_name":"Activity or Event Name (e.g. Men's haircut, Concert, Meeting)","venue_name":"Venue or Person Name","address":"full street address or null","datetime_iso":"2026-07-01T19:30:00-05:00","end_datetime_iso":"RFC3339 or null","section":"115 or null","row":"C or null","seat":"8 or null","order":"CODE or null","timezone":"America/Chicago","is_recurring_appointment":true|false}
Use type "event" for both tickets AND calendar invitations/appointments (Calendly, haircuts, etc.).
is_recurring_appointment=true for personal appointments with a specific recurring provider (coach, doctor, dentist, barber/salon, trainer, etc.) — false for one-off tickets, concerts, tours, or experiences.

Use America/Chicago for Texas/Dallas/DAL/DFW. Use America/Los_Angeles for LAX/SFO.
If the email states a timezone next to the time (e.g. "(US/Central)", "CDT", "PT"), that stated timezone sets both the ISO offset and the timezone field — even when the venue's address is in a different timezone (phone/telehealth providers quote the patient's timezone). Infer the timezone from the address only when the email states none.
If the email gives a month/day or weekday with no year, use the year that makes it the next occurrence on or after the "Today is" date above — never a past year.
JSON only — no markdown, no explanation after the JSON:"""

def _classify(sender, subject, body) -> dict:
    from bounded_model import classify_cached
    try:
        return classify_cached("email-tier", MODEL_FAST, CLASSIFY_PROMPT,
                               [sender, subject, body],
                               lambda: _classify_uncached(sender, subject, body),
                               lambda value: isinstance(value, dict)
                               and value.get("tier") in ("action", "fyi", "skip"))
    except Exception:
        return {"tier": "error", "meeting_request": None, "proposed_time": None,
                "summary": "", "reason": "classification unavailable; review required"}


def _classify_uncached(sender, subject, body) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    try:
        resp = client.messages.create(
            model=MODEL_FAST, max_tokens=300, temperature=0,
            system=CLASSIFY_PROMPT,
            messages=[{"role": "user",
                       "content": f"From: {sender}\nSubject: {subject}\n\n{body}"}],
        )
        log_usage("daemon.py:_classify", MODEL_FAST, resp.usage, response_id=getattr(resp, "id", None))
        raw  = resp.content[0].text.strip()
        m    = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            return {"tier": "error", "meeting_request": None, "proposed_time": None,
                    "summary": "", "reason": "no JSON"}
        data = json.loads(m.group(0))
        tier = data.get("tier", "error")
        if tier not in ("action", "fyi", "skip"):
            tier = "error"
        return {"tier": tier, "meeting_request": data.get("meeting_request"),
                "proposed_time": data.get("proposed_time"),
                "summary": data.get("summary", ""), "reason": data.get("reason", "")}
    except Exception as e:
        return {"tier": "error", "meeting_request": None, "proposed_time": None,
                "summary": "", "reason": f"classify error: {e}"}


class ClassificationUnavailable(RuntimeError):
    """The model did not classify a message; keep it queued and alert via health."""

def _write_draft(sender, subject, body, meeting_request) -> str:
    import anthropic
    client  = anthropic.Anthropic(api_key=API_KEY)
    mtg_note = ""
    if meeting_request == "open":
        mtg_note = "\nThis email asks for availability — use [AVAILABILITY] as a placeholder."
    elif meeting_request == "specific":
        mtg_note = "\nThis email proposes a specific meeting time — use [AVAILABILITY] if confirming."
    try:
        resp = client.messages.create(
            model=MODEL_FAST, max_tokens=300, temperature=0,
            system=DRAFT_PROMPT + _standing_email_rules(),
            messages=[{"role": "user",
                       "content": f"From: {sender}\nSubject: {subject}\n\n{body}{mtg_note}"}],
        )
        log_usage("daemon.py:_write_draft", MODEL_FAST, resp.usage, response_id=getattr(resp, "id", None))
        return resp.content[0].text.strip()
    except Exception as e:
        _log("Claude", f"draft error: {e}")
        return ""

def _parse_proposed_date(proposed_time_str: str):
    """Use Claude to extract an ISO date from a natural-language proposed time string."""
    import anthropic
    today = datetime.now(CT).date()
    client = anthropic.Anthropic(api_key=API_KEY)
    try:
        resp = client.messages.create(
            model=MODEL_FAST, max_tokens=20, temperature=0,
            system=(f"Today is {today.isoformat()} ({today.strftime('%A')}). "
                    "Extract the calendar date from the text. Return ONLY an ISO date like 2026-07-07. No other text."),
            messages=[{"role": "user", "content": proposed_time_str}],
        )
        log_usage("daemon.py:_parse_proposed_date", MODEL_FAST, resp.usage, response_id=getattr(resp, "id", None))
        return datetime.strptime(resp.content[0].text.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _get_day_slots(target_date, day_start=9, day_end=18, slot_min=30):
    """Return free windows on a specific date (9am–6pm CT by default)."""
    cal    = _calendar()
    params = dict(
        timeMin=datetime(target_date.year, target_date.month, target_date.day, 0, 0, tzinfo=CT).isoformat(),
        timeMax=datetime(target_date.year, target_date.month, target_date.day, 23, 59, tzinfo=CT).isoformat(),
        singleEvents=True, orderBy="startTime",
    )
    events = cal.events().list(calendarId="primary", **params).execute().get("items", [])
    for cal_id in EXTRA_CALENDARS:
        try:
            events += cal.events().list(calendarId=cal_id, **params).execute().get("items", [])
        except Exception:
            pass
    busy = sorted([
        (_parse_dt(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")).astimezone(CT),
         _parse_dt(e.get("end",   {}).get("dateTime") or e.get("end",   {}).get("date")).astimezone(CT))
        for e in events
        if _parse_dt(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
    ])
    cursor    = datetime(target_date.year, target_date.month, target_date.day, day_start, tzinfo=CT)
    day_close = datetime(target_date.year, target_date.month, target_date.day, day_end,   tzinfo=CT)
    free = []
    for bs, be in busy:
        gap_end = min(bs, day_close)
        if (gap_end - cursor) >= timedelta(minutes=slot_min):
            free.append((cursor, gap_end))
        cursor = max(cursor, be)
    if (day_close - cursor) >= timedelta(minutes=slot_min):
        free.append((cursor, day_close))
    return free


def _resolve_avail(draft_template, mtg_type, proposed_time) -> str:
    import anthropic
    if mtg_type == "open":
        slots = _find_free_slots()
        avail = ("I have the following times open:\n" + _fmt_slots(slots)) if slots \
                else "I'm pretty booked — let me check and get back to you."
    elif mtg_type == "specific" and proposed_time:
        proposed_date = _parse_proposed_date(proposed_time)
        if proposed_date:
            day_slots = _get_day_slots(proposed_date)
            day_label = proposed_date.strftime("%A, %B %-d")
            if day_slots:
                avail = (f"The proposed time ({proposed_time}) — {day_label} — IS available on my calendar.\n"
                         f"Free windows that day: {_fmt_slots(day_slots)}.")
            else:
                alt_slots = _find_free_slots(lookahead=14)
                avail = (f"The proposed time ({proposed_time}) — {day_label} — is NOT available (busy that day).\n"
                         f"My next free windows: {_fmt_slots(alt_slots) if alt_slots else 'let me check and follow up'}")
        else:
            # Fallback: original behavior if date can't be parsed
            slots = _find_free_slots(lookahead=14)
            avail = (f"The time they proposed: {proposed_time}.\n"
                     f"My upcoming free windows: {_fmt_slots(slots) if slots else 'checking...'}")
    else:
        return draft_template.replace("[AVAILABILITY]", "").strip()
    client = anthropic.Anthropic(api_key=API_KEY)
    resp   = client.messages.create(
        model=MODEL_FAST, max_tokens=300,
        system=AVAIL_PROMPT + _standing_email_rules(),
        messages=[{"role": "user",
                   "content": f"Draft template:\n{draft_template}\n\nAvailability info:\n{avail}"}],
    )
    log_usage("daemon.py:_resolve_avail", MODEL_FAST, resp.usage, response_id=getattr(resp, "id", None))
    return resp.content[0].text.strip()

def _fix_grammar(text: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    resp   = client.messages.create(
        model=MODEL_FAST, max_tokens=1000, temperature=0,
        system=GRAMMAR_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    log_usage("daemon.py:_fix_grammar", MODEL_FAST, resp.usage, response_id=getattr(resp, "id", None))
    return resp.content[0].text.strip()

# ── Telegram ───────────────────────────────────────────────────────────────────
TG_LIMIT = 4096

def _tg_answer_callback(callback_id: str, text: str = "") -> None:
    import requests as req
    try:
        payload: dict = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        req.post(f"https://api.telegram.org/bot{TG_TOKEN}/answerCallbackQuery",
                 json=payload, timeout=10)
    except Exception as e:
        _log("TG", f"answerCallback error: {e}")


def _tg_edit_message(message_id: int, text: str) -> None:
    import requests as req
    try:
        req.post(f"https://api.telegram.org/bot{TG_TOKEN}/editMessageText",
                 json={"chat_id": TG_CHAT_ID, "message_id": message_id,
                       "text": text, "reply_markup": {"inline_keyboard": []}},
                 timeout=10)
    except Exception as e:
        _log("TG", f"editMessage error: {e}")
        return
    # 2026-09-20 chat cleanup: the buttons are gone, so this prompt is answered --
    # archived + removed at the nightly sweep. The user's keep rule: a record of mail
    # SENT in his name, or a failure he still has to deal with, stays in the chat.
    try:
        from chat_cleanup import mark_answered
        mark_answered(message_id, text, skill="email-monitor",
                      keep=text.lstrip().startswith(("✅ Sent", "⚠️")))
    except Exception:
        pass


EXPIRED_DRAFT_TEXT = "⌛ Expired — nothing was sent."   # wording approved by the user 2026-09-20


def _expire_email_buttons() -> None:
    """An email draft's Send button is valid for 15 minutes (approval.py). After
    that a tap does nothing, but the message still looked live. Say so on it."""
    try:
        from approval import expire_due
        for message_id in expire_due("email"):
            _tg_edit_message(int(message_id), EXPIRED_DRAFT_TEXT)
            _log("TG", f"marked expired draft button message {message_id}")
    except Exception as e:
        _log("TG", f"expire-buttons error: {e}")


def handle_callback_query(cq: dict) -> None:
    # Calendar/email approvals belong to the gateway's owner-scoped dispatcher.
    # Legacy buttons must never resolve against whichever item is pending now.
    callback_kind = cq.get("data", "").split(":", 1)[-1]
    if callback_kind in {"email", "cal"} or callback_kind.startswith(("email-", "cal-")):
        _tg_answer_callback(cq.get("id", ""), "Use the current proposal in Archibald's private chat.")
        return
    cq_id      = cq.get("id", "")
    data       = cq.get("data", "")
    message_id = cq.get("message", {}).get("message_id")

    parts  = data.split(":", 1)
    action = parts[0] if parts else ""
    atype  = parts[1] if len(parts) > 1 else ""

    if atype == "email":
        state = _load()
        entry = state.get("tg_pending", {}).get(str(message_id)) if message_id else None

        if action == "skip":
            if entry:
                _remove_pending(state, entry.get("gmail_msg_id"))
                _save(state)
            _tg_answer_callback(cq_id, "Cancelled.")
            if message_id:
                _tg_edit_message(message_id, "Cancelled. 🎩")
            _log("TG", "callback skip: email")
            return

        if action == "confirm":
            if not entry:
                _tg_answer_callback(cq_id, "Nothing pending.")
                if message_id:
                    _tg_edit_message(message_id, "Nothing pending — draft may already be sent or expired. 🎩")
                return
            try:
                result = _send_email(entry)
                _remove_pending(state, entry.get("gmail_msg_id"))
                _save(state)
                _, detail = result.split(":", 1)
                _tg_answer_callback(cq_id, "Sent.")
                if message_id:
                    _tg_edit_message(message_id, f"✅ Sent {detail.strip()} 🎩")
                _log("TG", f"email sent: {result}")
            except Exception as e:
                _tg_answer_callback(cq_id, "Error.")
                if message_id:
                    _tg_edit_message(message_id, f"⚠️ Send failed: {e}")
                _log("TG", f"callback confirm error [email]: {e}")
            return

        _tg_answer_callback(cq_id)
        return

    action_file = PENDING_ACTIONS.get(atype)

    if action == "skip":
        if action_file:
            action_file.unlink(missing_ok=True)
        _tg_answer_callback(cq_id, "Skipped.")
        if message_id:
            _tg_edit_message(message_id, "Skipped. 🎩")
        _log("TG", f"callback skip: {atype}")
        return

    if action == "confirm":
        if not action_file or not action_file.exists():
            _tg_answer_callback(cq_id, "Nothing pending.")
            return
        try:
            ev = json.loads(action_file.read_text())
            if atype == "uv":
                _execute_uv(ev, action_file, None)
                confirmation = (f"You're on the calendar, Master Wayne — "
                                f"{ev.get('display_start','?')} to {ev.get('display_end','?')} CT. "
                                f"UV will be {ev.get('uv_index','?')} ({ev.get('uv_label','?')}) then. "
                                f"Get outside. 🎩")
            elif atype == "diary":
                _execute_diary(ev, action_file, None)
                confirmation = "Logged, Master Wayne. 🎩"
            elif atype == "cal":
                _execute_cal_event(ev, action_file, None)
                confirmation = "Added to your calendar. 🎩"
            else:
                confirmation = "Done. 🎩"
            _tg_answer_callback(cq_id, "Done.")
            if message_id:
                _tg_edit_message(message_id, confirmation)
        except Exception as e:
            _tg_answer_callback(cq_id, "Error.")
            _log("TG", f"callback confirm error [{atype}]: {e}")
        return

    _tg_answer_callback(cq_id)


def _tg_send_with_buttons(text: str, action_type: str,
                          confirm_label: str = "✅ Add to calendar",
                          skip_label: str = "❌ Skip", proposal=None) -> Optional[int]:
    import requests as req
    approval_id = None
    if action_type in ("email", "cal"):
        if proposal is None:
            raise ValueError("Email/calendar buttons require their exact proposal payload")
        from approval import issue
        target = proposal["gmail_msg_id"] if action_type == "email" else "pending_cal_event"
        # A proposal's approval must live exactly as long as the message holding its
        # buttons — a shorter TTL leaves a live-looking button that cannot fire.
        # Calendar: until the stale-drain drops it. Email: PENDING_TTL_S, the same
        # 48h the gmail_pending entry gets. 2026-10-09: email used approval.py's
        # 15-minute default while its card sat for two days, so a meeting reply's ✅
        # was already dead when the user opened it 40 minutes later — he had reviewed a
        # draft he could no longer send, with no way to tell from the card.
        ttl = {"ttl": STALE_CAL_PROPOSAL_MAX_AGE_DAYS * 86400} if action_type == "cal" \
              else {"ttl": PENDING_TTL_S}
        approval_id = issue(action_type, target, proposal, **ttl)
        proposal["approval_id"] = approval_id
        action_type = f"{action_type}-{approval_id}"
    payload = {
        "chat_id":    TG_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[
            {"text": confirm_label, "callback_data": f"confirm:{action_type}"},
            {"text": skip_label,    "callback_data": f"skip:{action_type}"},
        ]]},
    }
    try:
        r = req.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                     json=payload, timeout=15)
        if r.status_code == 200:
            message_id = r.json().get("result", {}).get("message_id")
            if approval_id and message_id:
                from approval import attach_message
                attach_message(approval_id, message_id)
            return message_id
        _log("TG", f"send_with_buttons error {r.status_code}: {r.text[:80]}")
    except Exception as e:
        _log("TG", f"send_with_buttons error: {e}")
    return None


def _tg_send(text: str, reply_to: Optional[int] = None) -> Optional[int]:
    import requests
    chunks, remaining = [], text
    while len(remaining) > TG_LIMIT:
        cut = remaining[:TG_LIMIT].rfind('\n\n')
        if cut == -1: cut = remaining[:TG_LIMIT].rfind('\n')
        if cut == -1: cut = TG_LIMIT
        chunks.append(remaining[:cut].strip()); remaining = remaining[cut:].strip()
    chunks.append(remaining)
    last_id = None
    for chunk in chunks:
        payload: dict = {"chat_id": TG_CHAT_ID, "text": chunk,
                         "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        import requests as req
        try:
            r = req.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                         json=payload, timeout=15)
        except Exception as e:
            _log("TG", f"send error: {e}")
            return None
        if r.status_code != 200:
            # A partly delivered message is an undelivered message.
            _log("TG", f"send error {r.status_code}: {r.text[:100]}")
            return None
        last_id = r.json().get("result", {}).get("message_id")
        time.sleep(0.2)
    return last_id

def _h(t: str) -> str:
    return html.escape(str(t))

def _display_name(sender: str) -> str:
    m = re.match(r'^(.+?)\s*<', sender)
    return m.group(1).strip() if m else sender


PENDING_FILTER = Path("~/.openclaw/pending_filter.json")
def _sender_cfg() -> dict:
    """Sender config, reloaded when senders.json / learned_senders.json change,
    so a sender the user just confirmed is recognised without a daemon restart."""
    return _ff.load_config_cached()
_SENDER_HISTORY_CACHE: dict[str, tuple[float, dict]] = {}

_AREA_EMOJI = {
    "finance": "🏦", "travel": "✈️", "packages": "📦", "documents": "📄",
    "health": "🩺", "home": "🏠", "golf": "⛳", "shopping": "🧾",
    "work": "💼", "notifications": "🔔", "other": "📥",
}


def _history_note(profile: dict) -> str:
    return (f"History: {profile.get('total', 0)} checked · "
            f"{profile.get('security', 0)} security · "
            f"{profile.get('material', 0)} records · "
            f"{profile.get('marketing', 0)} marketing · "
            f"{profile.get('unknown', 0)} unclear")


def _observe_sender(sender: str, subject: str, label_ids=None) -> None:
    """Tally one inbound message against its exact sender in the ledger.

    Skips VIPs and the user's own address — those must never be filtered anyway.
    """
    address = _ff.extract_address(sender)
    if not address or "@" not in address or address == MY_EMAIL or _is_vip(sender):
        return
    ledger = _ledger.load()
    _ledger.observe(ledger, address, subject, label_ids)
    _ledger.save(ledger)


def _sender_history(address: str, svc=None) -> Optional[dict]:
    cached = _SENDER_HISTORY_CACHE.get(address)
    if cached and time.time() - cached[0] < SENDER_SWEEP_INTERVAL_S:
        return cached[1]
    try:
        profile = _ff.profile_sender_history(svc or _gmail(), address)
        _SENDER_HISTORY_CACHE[address] = (time.time(), profile)
        return profile
    except Exception as e:
        _log("Gmail", f"sender history failed for {address}: {e}")
        return None


def _maybe_propose_sender_filter(sender: str, subject: str, svc=None) -> None:
    """Offer the user a one-tap "file every email from this sender forever" button
    the first time a recognised recurring sender (senders.json) lands in the
    inbox — a bank, airline, package carrier, e-sign service, property manager…

    Runs on every inbound message before the L1 verdict is acted on — this mail
    almost always carries List-Unsubscribe, so waiting for it to pass L1 would
    miss exactly the senders we want to file. Single-slot: only one
    proposal is outstanding at a time; a sender that emails again while another
    proposal is pending is simply re-checked on its next message. Every address
    is deduped forever via the state file (created / declined / proposed) and
    against the live Gmail filter list.
    """
    if PENDING_FILTER.exists():
        return
    address = _ff.extract_address(sender)
    if not address or "@" not in address:
        return

    state = _load()
    verdict = _ff.classify_sender(address, subject, _sender_cfg())
    if not verdict or verdict["source"] == "learned-domain":
        # Not a sender anyone taught us, or a domain we only learned from a
        # DIFFERENT address there: this exact address must establish its own
        # purpose from what it has actually been sending (sender_ledger). Still
        # has to clear every gate below, including the live 90-day profile.
        verdict = _ledger.learned_verdict(_ledger.load(), address, _sender_cfg())
    if not verdict:
        return

    if _ff.already_handled(address, state):
        return

    covered = _ff.cached_filter_addresses(state)
    if covered is None:
        try:
            covered = _ff.list_filter_coverage(svc or _ff.gmail_service())
        except Exception as e:
            # Unknown coverage: propose nothing rather than risk a duplicate.
            _log("Gmail", f"sender-filter: filter list unavailable, skipping: {e}")
            return
        state = _load()
        _ff.store_filter_cache(state, covered)
        _save(state)
    if _ff.existing_filter_covers(address, covered):
        return

    profile = _sender_history(address, svc)
    if profile is None:
        return
    policy = _ff.recommend_sender_policy(profile, known=True)
    if policy != "archive":
        _log("Gmail", f"sender-filter withheld [{policy}; {_history_note(profile)}]: {address}")
        return

    label = verdict["label"]
    emoji = _AREA_EMOJI.get(verdict.get("area", "other"), "📥")
    lines = [
        f"{emoji} New recurring sender: <b>{_h(verdict['name'])}</b>",
        f"<code>{_h(address)}</code>",
        "",
        f"File all mail from this address to <b>{_h(label)}</b> and skip the inbox?",
        "",
        _history_note(profile),
    ]
    if verdict["source"] == "heuristic":
        lines.append("\n⚠️ Guessed from the subject line — tap Never if that's wrong.")
    elif verdict["source"] == "learned":
        lines.append(f"\n🆕 New recurring sender — learned from "
                     f"{verdict.get('observations', 0)} messages. Confirming also "
                     f"files future senders at {_h(_ledger.domain_of(address))}.")
    text = "\n".join(lines)

    msg_id = _tg_send_with_buttons(
        text, "filter", confirm_label="✅ Create filter", skip_label="❌ Never")
    if not msg_id:
        return
    PENDING_FILTER.write_text(json.dumps({
        "address":     address,
        "label":       label,
        "institution": verdict["name"],
        "source":      verdict["source"],
        "area":        verdict.get("area", "other"),
        "domain":      _ledger.domain_of(address),
        "created_at":  time.time(),
        "source_tg_msg_id": msg_id,
    }))
    state = _load()
    _ff.mark_proposed(address, state)
    _save(state)
    _log("Gmail", f"sender-filter proposal sent: {address} → {label}")


PENDING_UNSUB = Path("~/.openclaw/pending_unsub.json")
_UNSUB_MIN_INBOX = 3   # only offer once a promo sender has piled up this many


def _bulk_cleanup_mode(hdrs: dict, label_ids) -> Optional[str]:
    """Return unsubscribe or filter-only mode for a safe bulk-mail candidate."""
    if hdrs.get("list-unsubscribe"):
        return "unsubscribe"
    if "CATEGORY_PROMOTIONS" in set(label_ids or []):
        return "filter"
    return None


def _maybe_propose_unsub(sender: str, subject: str, hdrs: dict,
                         label_ids=None, svc=None) -> None:
    """Sibling of _maybe_propose_sender_filter for the senders it deliberately
    ignores: UNKNOWN bulk senders (not in senders.json) that keep landing in the
    inbox. Offers a one-tap "unsubscribe + filter to Trash + clear the backlog".

    A List-Unsubscribe header enables unsubscribe + filtering. Gmail's
    Promotions classification is the conservative fallback for bulk senders
    that omit that header; those get a filter-only proposal. In both modes the
    exact sender must have _UNSUB_MIN_INBOX+ messages in the inbox."""
    if PENDING_UNSUB.exists():
        return
    lu = hdrs.get("list-unsubscribe", "")
    mode = _bulk_cleanup_mode(hdrs, label_ids)
    if not mode:
        return
    address = _ff.extract_address(sender)
    if not address or "@" not in address:
        return
    known = bool(_ff.classify_sender(address, subject, _sender_cfg()))

    state = _load()
    if _ff.unsub_already_handled(address, state):
        return

    covered = _ff.cached_filter_addresses(state)
    if covered is None:
        try:
            covered = _ff.list_filter_coverage(svc or _ff.gmail_service())
        except Exception as e:
            _log("Gmail", f"unsub: filter list unavailable, skipping: {e}")
            return
        state = _load()
        _ff.store_filter_cache(state, covered)
        _save(state)
    if _ff.existing_filter_covers(address, covered):
        return
    profile = _sender_history(address, svc)
    if profile is None:
        return
    # Trash only on at least two consistent marketing examples (policy 5) —
    # insufficient or unclear history stays under message-level triage.
    policy = _ff.recommend_sender_policy(profile, known=known)
    if policy != "marketing":
        _log("Gmail", f"bulk filter withheld [{policy}; {_history_note(profile)}]: {address}")
        return

    try:
        n = _ff.inbox_count_from(svc or _ff.gmail_service(), address,
                                 cap=_UNSUB_MIN_INBOX + 2)
    except Exception:
        return
    if n < _UNSUB_MIN_INBOX:
        return

    name = _display_name(sender)
    action_text = ("Unsubscribe and send future mail from this address straight to Trash?"
                   if mode == "unsubscribe" else
                   "Send future mail from this exact address straight to Trash?")
    confirm_label = "🗑 Unsubscribe & filter" if mode == "unsubscribe" else "🗑 Block sender"
    text = "\n".join([
        f"🗑 <b>{_h(name)}</b> keeps landing in your inbox",
        f"<code>{_h(address)}</code> — {n}+ messages classified as promotions.",
        "",
        action_text,
        "",
        _history_note(profile),
    ])
    msg_id = _tg_send_with_buttons(
        text, "unsub", confirm_label=confirm_label, skip_label="❌ Keep")
    if not msg_id:
        return
    PENDING_UNSUB.write_text(json.dumps({
        "address":          address,
        "name":             name,
        "list_unsubscribe": lu,
        "mode":              mode,
        "count":            n,
        "created_at":       time.time(),
        "source_tg_msg_id": msg_id,
    }))
    state = _load()
    _ff.unsub_mark_proposed(address, state)
    _save(state)
    _log("Gmail", f"unsub proposal sent: {address} ({n}+ in inbox)")


def _read_fresh_proposal(path: Path, kind: str = "offer") -> str:
    """Return its address when a pending proposal is valid and under 15m old.

    2026-09-26: a stale proposal's file is deleted here — and that file holds the
    ONLY record of which Telegram message these buttons belong to. Deleting it
    first left the prompt sitting in the chat with live-looking ✅/❌ buttons
    forever; tapping one hours later found no pending file and could only answer
    "No pending filter proposal." as a SECOND message under the still-buttoned
    prompt, with no id left to edit. That is exactly what the user reported. So
    retire the message FIRST, while we still know which one it is.
    """
    if not path.exists():
        return ""
    msg_id = None
    address = ""
    try:
        data = json.loads(path.read_text())
        msg_id = data.get("source_tg_msg_id")
        address = (data.get("address") or "").lower()
        if time.time() - float(data.get("created_at", 0)) < _ff.PROPOSAL_TTL_S:
            return address
    except Exception:
        pass
    if msg_id:
        who = f" for {address}" if address else ""
        _tg_edit_message(
            int(msg_id),
            f"\u23f3 This {kind} expired after 15 minutes \u2014 nothing was changed{who}. "
            f"I'll offer it again the next time they write. \U0001f3a9")
    path.unlink(missing_ok=True)
    return ""


def _expire_sender_proposals(state: dict) -> bool:
    """Expire dead buttons/files and unblock future sender-cleanup proposals."""
    active_filter = _read_fresh_proposal(PENDING_FILTER, "filter offer")
    active_unsub = _read_fresh_proposal(PENDING_UNSUB, "unsubscribe offer")
    cleared = _ff.expire_stale_proposals(
        state, ttl=_ff.PROPOSAL_TTL_S,
        active_filter=active_filter, active_unsub=active_unsub)
    if cleared:
        _log("Gmail", f"expired sender proposals: {', '.join(cleared)}")
    return bool(cleared)


def _sender_proposal_catchup(svc) -> None:
    """Recheck recent inbox mail even if its triage ID was already processed.

    This lets senders skipped while a proposal slot was occupied surface once
    that 15-minute proposal expires.
    """
    if PENDING_FILTER.exists() and PENDING_UNSUB.exists():
        return
    state = _load()
    now = time.time()
    if now - float(state.get("last_sender_proposal_sweep", 0)) < SENDER_SWEEP_INTERVAL_S:
        return
    state["last_sender_proposal_sweep"] = now
    _save(state)
    try:
        resp = svc.users().messages().list(
            userId="me", q="in:inbox newer_than:30d", maxResults=50).execute()
        for item in resp.get("messages", []):
            if PENDING_FILTER.exists() and PENDING_UNSUB.exists():
                break
            full = svc.users().messages().get(
                userId="me", id=item["id"], format="metadata",
                metadataHeaders=["From", "Subject", "List-Unsubscribe"],
            ).execute()
            hdrs = {h["name"].lower(): h["value"]
                    for h in full.get("payload", {}).get("headers", [])}
            sender = hdrs.get("from", "")
            subject = hdrs.get("subject", "(no subject)")
            _maybe_propose_sender_filter(sender, subject, svc=svc)
            _maybe_propose_unsub(sender, subject, hdrs, full.get("labelIds", []), svc=svc)
    except Exception as e:
        _log("Gmail", f"sender proposal catchup error: {e}")


AUTH_WARNING = ("⚠️ <b>This sender failed email authentication.</b> It may be phishing — "
                "don't use its links; check with the company directly.")


def _auth_failed(hdrs: dict) -> bool:
    """True when Gmail's own checks say the From domain wasn't verified.

    Used only to WARN on security mail, never to route it: a parsing slip must
    not be able to suppress a genuine alert. No header means no verdict.
    """
    results = (hdrs.get("authentication-results") or "").lower()
    if not results:
        return False
    if "dmarc=fail" in results:
        return True
    return "dkim=pass" not in results and "spf=pass" not in results


def _format_notification(tier, sender, subject, body, summary, reason, draft, msg_id) -> str:
    link  = f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"
    label = "<b>Action Needed</b>" if tier == "action" else "<b>FYI</b>"
    # The user 2026-10-05: the summary alone, never the email itself. This quoted up to 600
    # characters of body, which for most mail is the whole thing including the blank
    # lines and footer left by HTML-to-text. He asked not to see the whole body: a
    # summary is enough to tell whether something actually needs action. `body` stays in
    # the signature because the draft path and callers still pass it.
    parts = [
        f"📧 {label}", "",
        f"<b>{_h(_display_name(sender))}</b> — {_h(subject)}", "",
        _h(summary) if summary else _h(reason),
    ]
    if draft:
        parts.append(f"\nDraft reply:")
        parts.append(f"<blockquote>{_h(draft)}</blockquote>")
        if tier == "action":
            parts.append(f"\nReply <b>✅</b> to send · or reply with your own text")
            parts.append(f"<code>ref:{msg_id}</code>")
    parts.append(f"\n<a href=\"{link}\">Open in Gmail</a>")
    return "\n".join(parts)

# ── Inline email send ──────────────────────────────────────────────────────────
def _send_email(entry: dict) -> str:
    """Send the reply and return 'SENT: {id} → {email}'. Raises on failure."""
    to_email  = entry["to_email"]
    draft     = entry["draft"]
    subject   = entry["subject"]
    thread_id = entry["thread_id"]
    rfc_id    = entry.get("rfc_msg_id", "")

    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg             = MIMEText(draft, "plain")
    msg["To"]       = to_email
    msg["From"]     = MY_EMAIL
    msg["Subject"]  = subject
    if rfc_id:
        msg["In-Reply-To"] = rfc_id
        msg["References"]  = rfc_id

    raw    = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = _gmail().users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": thread_id},
    ).execute()
    return f"SENT: {result.get('id', 'unknown')} → {to_email}"

def _remove_pending(state: dict, gmail_msg_id: str) -> None:
    state.get("gmail_pending", {}).pop(gmail_msg_id, None)
    tp    = state.get("tg_pending", {})
    stale = [k for k, v in tp.items() if v.get("gmail_msg_id") == gmail_msg_id]
    for k in stale:
        del tp[k]

# ── FYI digest queue ──────────────────────────────────────────────────────────
_FYI_SUPPRESS_FILE = Path(__file__).resolve().parent.parent / "fyi_suppressed_senders.json"


def _load_fyi_suppressed() -> set:
    """Senders that are filed but never announced in the 6pm digest.

    The user 2026-10-14, on a 10-line digest: "there is a lot of useless clutter in
    this". Six of the ten were a booking he had already confirmed by tapping a
    Telegram button, and four TestFlight builds of his own apps. Nothing here is
    dropped — it is labelled and searchable; it just stops being read out.

    Deliberately NOT a tier change: security/action/realtime mail never reaches
    the digest, so this cannot silence anything urgent.
    """
    try:
        raw = json.loads(_FYI_SUPPRESS_FILE.read_text())
        return {str(e["sender"]).strip().lower()
                for e in raw.get("suppressed", []) if e.get("sender")}
    except FileNotFoundError:
        return set()
    except Exception as e:  # noqa: BLE001
        print(f"[Boot] fyi_suppressed_senders.json unreadable: {e}", flush=True)
        return set()


FYI_SUPPRESSED = _load_fyi_suppressed()
def _queue_fyi(sender: str, subject: str, summary: str, mid: str) -> None:
    """Append a low-priority FYI to the queue drained by send_fyi_digest.py at
    6pm CT. Deduped by Gmail message id; capped so a stuck cron can't grow it
    without bound."""
    if parseaddr(sender)[1].strip().lower() in FYI_SUPPRESSED:
        _log("Gmail", f"FYI suppressed [{parseaddr(sender)[1]}]: {subject[:50]}")
        return
    state = _load()
    q = state.setdefault("fyi_queue", [])
    if any(e.get("mid") == mid for e in q):
        return
    q.append({
        "mid":     mid,
        "from":    _display_name(sender),
        "subject": subject,
        "summary": summary or "",
        "ts":      time.time(),
    })
    if len(q) > 120:
        del q[:-120]
    _save(state)


# ── CRM logging ───────────────────────────────────────────────────────────────
CRM_SCRIPT = "~/.openclaw/workspace/Skills/crm/scripts/crm.py"

def _crm_log(sender: str, subject: str, tier: str, source_id: str = "",
             occurred_at: str = "") -> None:
    """Log an email interaction and verify the local CRM accepted it."""
    email = _extract_email(sender)
    name  = _display_name(sender)
    note  = f"{'Actioned' if tier == 'action' else 'FYI'} email: {subject[:80]}"
    try:
        cmd = ["python3", "-W", "ignore", CRM_SCRIPT, "log", email, note,
               "--name", name, "--type", "email", "--direction", "inbound"]
        if source_id:
            cmd += ["--source-id", source_id]
        if occurred_at:
            cmd += ["--occurred-at", occurred_at]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[:300] or f"CRM exited {result.returncode}")
    except Exception as e:
        _log("CRM", f"log error: {e}")

# ── Gmail check ────────────────────────────────────────────────────────────────
def _expire_pending(state: dict) -> bool:
    now     = time.time()
    gp      = state.get("gmail_pending", {})
    stale   = [k for k, v in gp.items()
               if now - v.get("created_at", now) > PENDING_TTL_S]
    changed = bool(stale)
    for k in stale:
        _log("Gmail", f"expired pending draft: {k}")
        _remove_pending(state, k)
    tf      = state.get("tg_fyi", {})
    stale2  = [k for k, v in tf.items()
               if now - v.get("created_at", now) > PENDING_TTL_S]
    for k in stale2:
        del tf[k]
    return changed or bool(stale2)

def run_gmail_check() -> None:
    state     = _load()
    _expire_pending(state)
    _expire_email_buttons()
    _expire_sender_proposals(state)
    _save(state)

    processed = set(state.get("processed_ids", []))
    since     = int(time.time()) - INBOX_LOOKBACK_S
    svc       = _gmail()
    sig       = _get_signature()

    q = f"in:inbox after:{since}"
    msgs, page_token = [], None
    while True:
        kw: dict = {"userId": "me", "q": q, "maxResults": 50}
        if page_token: kw["pageToken"] = page_token
        r = svc.users().messages().list(**kw).execute()
        msgs.extend(r.get("messages", []))
        page_token = r.get("nextPageToken")
        if not page_token: break

    new_ids: list = []
    for msg in msgs:
        mid = msg["id"]
        if mid in processed:
            new_ids.append(mid)
            continue

        try:
            full = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception as e:
            _log("Gmail", f"fetch error {mid}: {e}")
            continue

        hdrs_raw = full.get("payload", {}).get("headers", [])
        hdrs     = {h["name"].lower(): h["value"] for h in hdrs_raw}
        sender   = hdrs.get("from", "unknown")
        subject  = hdrs.get("subject", "(no subject)")
        vip      = _is_vip(sender)
        kind     = _policy.classify_subject(subject, full.get("labelIds", []))
        security = kind == "security"
        routine  = _is_routine_subject(sender, subject)
        material = kind == "material" and not routine
        finance  = _known_finance_sender(sender, subject)

        if _is_inactive_sender(sender) and not security:
            _log("Gmail", f"inactive sender skip [{_ff.extract_address(sender)}]: {subject[:50]}")
            new_ids.append(mid)
            continue

        passes, reason = _passes_filters(hdrs, subject)

        # Remember what this address sends, so an unknown but recurring sender
        # can earn a filing proposal later (sender_ledger). Cheap and local.
        try:
            _observe_sender(sender, subject, full.get("labelIds", []))
        except Exception as e:
            _log("Gmail", f"sender ledger error: {e}")

        # Recurring-sender auto-filing: the first time a recognised recurring
        # sender (senders.json — bank, airline, carrier, e-sign, property mgr…)
        # lands in the inbox, offer the user a one-tap button to file every future
        # email from it. Deliberately before the L1 bail — this mail almost
        # always carries List-Unsubscribe.
        try:
            _maybe_propose_sender_filter(sender, subject, svc=svc)
        except Exception as e:
            _log("Gmail", f"sender-filter check error: {e}")

        # …and for the bulk senders it ignores (unknown, List-Unsubscribe,
        # piling up in the inbox): offer one-tap unsubscribe + trash-filter.
        try:
            _maybe_propose_unsub(
                sender, subject, hdrs, full.get("labelIds", []), svc=svc)
        except Exception as e:
            _log("Gmail", f"unsub check error: {e}")

        # A finance watcher cron already announces this exact message.
        owner = _policy.dedicated_path_for(
            _ff.extract_address(sender), subject, message_id=mid)
        if owner and owner.id.startswith("cron.") and not security:
            if (owner.id == _policy.STATEMENT_WATCH_OWNER
                    and not _policy.statement_watch_handled(mid)):
                _log("Gmail", f"waiting for {owner.id}: {subject[:50]}")
                continue  # do not mark seen until delivery/receipt is acknowledged
            _log("Gmail", f"owned by {owner.id}: {subject[:50]}")
            new_ids.append(mid); continue

        # Bulk marketing is clutter even from a bank the user uses (policy 2/6), so
        # the known-finance override must not hand it to L2 as a possible FYI.
        if not passes and kind == "marketing" and not vip:
            _log("Gmail", f"L1 skip [marketing; {reason}]: {subject[:50]}")
            new_ids.append(mid); continue

        # Remember whether this sender is a human the user could actually reply to.
        # The overrides below exist to make automated mail VISIBLE, never to
        # make it repliable.
        repliable = passes
        if not passes and (vip or security or material or finance):
            why = ("VIP" if vip else "security" if security else
                   "material update" if material else "known finance")
            _log("Gmail", f"L1 override [{why} {_extract_email(sender)}]: {subject[:50]}")
            passes = True
        if not passes:
            _log("Gmail", f"L1 skip [{reason}]: {subject[:50]}")
            new_ids.append(mid); continue

        body   = _get_body(full)

        # Extract ICS content once — used by both paths below.
        ics_content = None
        if _has_ics_attachment(full):
            ics_content = _get_ics_content(full, mid)

        # ── TeleVisit appointment reminder — MUST run before generic ICS auto-accept ──
        # TeleVisit always attaches .ics with METHOD:REQUEST. If we let the
        # auto-accept block run first it accepts the practice-formatted event and
        # continues, so our '<> the user @' event never gets created.
        if TELEVISIT_SENDER_RE.search(sender) and TELEVISIT_SUBJECT_RE.search(subject):
            ics_uid = _ics_field(ics_content, "UID") if ics_content else ""
            try:
                handled = _handle_televisit(subject, body, ics_uid=ics_uid)
            except DeliveryFailed as e:
                _log("Gmail", f"delivery failed, will retry: {e}")
                continue
            if handled:
                new_ids.append(mid)
                continue
            # falls through to ICS auto-accept or itinerary path if parsing failed

        # ── Calendar invite proposal (METHOD:REQUEST .ics attachments) ───
        # Propose the reformatted '<> the user @' title to the user rather than
        # silently accepting the organiser's title.
        if ics_content and "METHOD:REQUEST" in ics_content.upper():
            try:
                _handle_invite_proposal(ics_content, sender)
            except DeliveryFailed as e:
                _log("Gmail", f"delivery failed, will retry: {e}")
                continue
            new_ids.append(mid)
            continue

        # ── Itinerary pre-check — booking confirmations and calendar invites ──
        # The body is checked too (2026-10-09): the subject gate catches machines,
        # whose subject lines are predictable, but not a person confirming a time in a
        # reply. A contact at a venue replied on a "Re: Come see us" thread with
        # "Let's do 3pm Tuesday" and a street address; nothing read the body and
        # it was filed FYI. The body test is a cheap regex needing BOTH a day and a
        # clock time, so a miss costs an appointment and a false hit costs one model
        # call that answers "not an itinerary".
        if (ITINERARY_SUBJECT_RE.search(subject) or ics_content is not None
                or _policy.body_looks_like_an_appointment(body)):
            itin = _check_itinerary(subject, body[:400])
            if itin["is_itinerary"]:
                _log("Gmail", f"L0 itinerary [{itin['type']}]: {subject[:50]}")
                try:
                    _handle_itinerary(subject, body, itin["type"], reference_dt=_received_dt(full))
                except DeliveryFailed as e:
                    _log("Gmail", f"delivery failed, will retry: {e}")
                    continue
                new_ids.append(mid)
                continue

        result = _classify(sender, subject, body[:500])
        tier   = result["tier"]

        if tier == "error":
            # A dead API key used to turn its exception text into one FYI per
            # email.  Retain the message and fail the poll with a stable,
            # credential-free reason; gmail_loop then emits one bounded health
            # notice after its consecutive-failure threshold.
            _log("Gmail", f"L2 unavailable; retaining message: {subject[:50]}")
            raise ClassificationUnavailable("Anthropic classification unavailable")

        if tier == "skip" and (vip or security or material):
            why = "VIP" if vip else ("security" if security else "material update")
            _log("Gmail", f"L2 override [{why} {_extract_email(sender)}]: skip→fyi — {subject[:50]}")
            tier = "fyi"

        if tier == "skip":
            _log("Gmail", f"L2 skip [{result['reason']}]: {subject[:50]}")
            new_ids.append(mid); continue

        # No-reply/bulk senders can be urgent, but nobody is waiting on a reply
        # from the user. L2 sometimes reads "click here to verify" as "action" and
        # we would then draft an email to a robot (an app's new-device alert and a
        # social site's verification code, 2026-09-16). Surface it, never offer a reply.
        if tier == "action" and not repliable and not vip:
            _log("Gmail", f"action→fyi [automated sender {_extract_email(sender)}]: "
                          f"{subject[:50]}")
            tier = "fyi"

        mtg    = result["meeting_request"]
        prop_t = result["proposed_time"]
        draft  = ""

        if tier == "action":
            _log("Gmail", "writing draft (Sonnet)…")
            draft = _write_draft(sender, subject, body[:1000], mtg)
            if mtg in ("open", "specific") and "[AVAILABILITY]" in draft:
                _log("Gmail", f"calendar lookup ({mtg})…")
                try:
                    draft = _resolve_avail(draft, mtg, prop_t)
                except Exception as e:
                    _log("Gmail", f"avail error: {e}")

        if draft:
            draft = _with_signature(draft, sig)

        notification = _format_notification(
            tier, sender, subject, body, result["summary"], result["reason"], draft, mid)
        if security and _auth_failed(hdrs):
            notification = f"{AUTH_WARNING}\n\n{notification}"

        if CANCEL_RE.search(subject):
            try:
                bullet = f"{_display_name(sender)} cancelled — reschedule pending"
                DIARY_FILE.write_text(json.dumps({
                    "date":         datetime.now(tz=CT).strftime("%A, %B %-d, %Y"),
                    "bullets":      [bullet],
                    "source":       "realtime",
                    "proposal_text": f"📓 {bullet}.\n\nWant to log this to your diary? Reply to this message.",
                }))
                subprocess.Popen(
                    ["python3", SEND_PROPOSAL, str(DIARY_FILE)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                _log("Gmail", f"diary proposal sent: {subject[:40]}")
            except Exception as e:
                _log("Gmail", f"diary proposal error: {e}")

        # 2026-10-06: a brokerage crypto statement pinged the user because its subject
        # matches the realtime-statement set. Mail an approved finance rule files
        # with `silent_ingest` is a feed the finance skill already records — it
        # behaves like a card's purchase mails: filed, counted, never announced.
        # The rules' own priority rule surfaces security and action mail first, so
        # this can never swallow a fraud notice.
        if _silently_ingested(sender, subject):
            _log("Gmail", f"SILENT [finance feed]: {subject[:50]}")
            _crm_log(sender, subject, tier, mid, _received_dt(full).isoformat())
            new_ids.append(mid)
            continue

        # FYI tier is low-priority by definition (real contact, no reply needed) —
        # batch it into the 6pm digest (send_fyi_digest.py) instead of pinging
        # now. Action, security and the realtime material set stay real-time.
        if tier == "fyi" and not _policy.is_realtime(kind, subject):
            _queue_fyi(sender, subject, result.get("summary", "") or result.get("reason", ""), mid)
            _log("Gmail", f"QUEUED [FYI]: {subject[:50]}")
            _remember_announced(mid)
            _crm_log(sender, subject, tier, mid, _received_dt(full).isoformat())
            new_ids.append(mid)
            time.sleep(0.3)
            continue

        tg_msg_id = _tg_send(notification)
        if not tg_msg_id:
            # Leave it unprocessed: the next poll retries. A duplicate ping after
            # an ambiguous send is far cheaper than a silently lost alert.
            _log("Gmail", f"DELIVERY FAILED, will retry [{tier.upper()}]: {subject[:50]}")
            continue
        _log("Gmail", f"NOTIFIED [{tier.upper()}]: {subject[:50]}")
        _remember_announced(mid)

        if tg_msg_id and tier == "action" and draft:
            entry = {
                "gmail_msg_id": mid,
                "thread_id":    full.get("threadId", ""),
                "to_email":     _extract_email(sender),
                "subject":      subject,
                "rfc_msg_id":   hdrs.get("message-id", ""),
                "draft":        draft,
                "created_at":   time.time(),
            }
            # Send compact button message so user can send with one tap
            sender_name = _display_name(sender)
            btn_text = f"✉️ <b>Draft ready</b> — reply to {_h(sender_name)}\nRe: {_h(subject[:80])}"
            btn_msg_id = _tg_send_with_buttons(btn_text, "email",
                                  confirm_label="✅ Send", skip_label="❌ Cancel", proposal=entry)
            if btn_msg_id:
                entry["source_tg_msg_id"] = btn_msg_id

            state = _load()
            tp    = state.setdefault("tg_pending", {})
            tp[str(tg_msg_id)] = entry
            if btn_msg_id:
                tp[str(btn_msg_id)] = entry
            if len(tp) > 50:
                for k in list(tp.keys())[:-50]: del tp[k]
            state.setdefault("gmail_pending", {})[mid] = entry
            _save(state)

        # (FYI tier now returns via the digest queue above and never reaches
        # here, so the old per-message tg_fyi reply-tracking block is gone. The
        # reply handler that read state["tg_fyi"] still exists and degrades to a
        # no-op on the now-absent map.)

        _crm_log(sender, subject, tier, mid, _received_dt(full).isoformat())
        new_ids.append(mid)
        time.sleep(0.5)

    state            = _load()
    existing         = set(state.get("processed_ids", []))
    state["processed_ids"] = list(existing | set(new_ids))[-2000:]
    _save(state)

# ── Telegram message handler ───────────────────────────────────────────────────
def handle_telegram_msg(msg: dict) -> None:
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text    = (msg.get("text") or "").strip()
    msg_id  = msg.get("message_id")

    if chat_id != TG_CHAT_ID or not text or text.startswith("/start"):
        return

    state         = _load()
    tg_pending    = state.get("tg_pending", {})
    reply_to_id   = str(msg.get("reply_to_message", {}).get("message_id", ""))
    pending_entry = tg_pending.get(reply_to_id) if reply_to_id else None

    _log("TG", f"recv: {repr(text[:60])}")

    # ── /pending — list all pending drafts ────────────────────────────────────
    if text.lower() in ("/pending", "pending", "what's pending", "whats pending"):
        gp = state.get("gmail_pending", {})
        if not gp:
            _tg_send("No pending drafts.", reply_to=msg_id)
        else:
            lines = []
            for i, (gid, e) in enumerate(gp.items(), 1):
                preview = e.get("draft", "")[:80].replace("\n", " ")
                lines.append(f"<b>{i}.</b> To: {_h(e['to_email'])}\n"
                             f"Re: {_h(e['subject'])}\n"
                             f"<i>{_h(preview)}…</i>")
            _tg_send("\n\n".join(lines), reply_to=msg_id)
        return

    # ── /cancel — discard latest pending draft ────────────────────────────────
    if text.lower() in ("/cancel", "cancel"):
        gp = state.get("gmail_pending", {})
        if not gp:
            _tg_send("No pending drafts to cancel.", reply_to=msg_id)
        else:
            last_id = list(gp.keys())[-1]
            entry   = gp[last_id]
            _remove_pending(state, last_id)
            _save(state)
            _tg_send(f"❌ Cancelled draft to {_h(entry['to_email'])}.", reply_to=msg_id)
            _log("TG", f"cancelled pending: {last_id}")
        return

    # ── Reply-to router — match reply against all pending action files ─────────
    if reply_to_id and not pending_entry:
        for action_type, action_file in PENDING_ACTIONS.items():
            if not action_file.exists():
                continue
            try:
                ev = json.loads(action_file.read_text())
                if str(ev.get("source_tg_msg_id", "")) == reply_to_id:
                    if action_type == "uv":
                        _execute_uv(ev, action_file, msg_id)
                    elif action_type == "diary":
                        _execute_diary(ev, action_file, msg_id)
                    elif action_type == "cal":
                        if text != "✅":
                            return
                        token = ev.get("approval_id")
                        if not token:
                            _tg_send("Please ask Archibald to show a fresh calendar proposal.", reply_to=msg_id)
                            return
                        r = subprocess.run([sys.executable, str(Path(__file__).with_name("confirm_cal_event.py")),
                                            "--approval-id", token], capture_output=True, text=True, timeout=60)
                        if r.returncode:
                            _tg_send("Calendar approval could not complete. Check its result before retrying.", reply_to=msg_id)
                    return
            except Exception as e:
                _log("TG", f"router error [{action_type}]: {e}")

    # ── ✅ — send pending email ───────────────────────────────────────────────
    if text == "✅":
        gp = state.get("gmail_pending", {})
        if pending_entry:
            gmail_msg_id = pending_entry.get("gmail_msg_id")
            entry        = gp.get(gmail_msg_id) if gmail_msg_id else None
        else:
            entry = None
            gmail_msg_id = None

        if not entry:
            _tg_send("Reply to the specific proposal or use its approval button.", reply_to=msg_id)
            return

        try:
            token = entry.get("approval_id")
            if not token:
                _tg_send("Ask Archibald to show a fresh email proposal before sending.", reply_to=msg_id)
                return
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("send_pending.py")),
                                     "--approval-id", token, "--from-callback"],
                                    capture_output=True, text=True, timeout=45)
            if result.returncode:
                raise RuntimeError("Proposal not sent; check its status before retrying")
            if result.stdout.strip() != "NO_REPLY":
                _tg_send(result.stdout.strip(), reply_to=msg_id)
        except Exception as e:
            _tg_send(f"⚠️ Send failed: {e}", reply_to=msg_id)
            _log("TG", f"send failed: {e}")
        return

    # ── Reply intent — user wants to reply to a FYI email ────────────────────
    if REPLY_INTENT_RE.search(text):
        tg_fyi    = state.get("tg_fyi", {})
        fyi_entry = tg_fyi.get(reply_to_id) if reply_to_id else None
        if fyi_entry and not pending_entry:
            _tg_send("Drafting a reply…", reply_to=msg_id)
            f_sender  = fyi_entry["sender"]
            f_subject = fyi_entry["subject"]
            f_body    = fyi_entry["body"]
            f_mtg     = fyi_entry.get("meeting_request")
            f_prop    = fyi_entry.get("proposed_time")
            f_gid     = fyi_entry["gmail_msg_id"]
            _log("TG", f"reply intent → drafting for FYI: {f_subject[:40]}")
            draft = _write_draft(f_sender, f_subject, f_body, f_mtg)
            if f_mtg in ("open", "specific") and "[AVAILABILITY]" in draft:
                try:
                    draft = _resolve_avail(draft, f_mtg, f_prop)
                except Exception as e:
                    _log("TG", f"avail error: {e}")
            draft = _with_signature(draft, _get_signature())
            out = (f"<b>Draft reply:</b>\n\n{_h(draft)}"
                   f"\n\nReply <b>✅</b> to send · or reply with your own text"
                   f"\n<code>ref:{f_gid}</code>")
            bot_id = _tg_send(out, reply_to=msg_id)
            if bot_id:
                new_entry = {
                    "gmail_msg_id": f_gid,
                    "thread_id":    fyi_entry["thread_id"],
                    "to_email":     fyi_entry["to_email"],
                    "subject":      f_subject,
                    "rfc_msg_id":   fyi_entry["rfc_msg_id"],
                    "draft":        draft,
                    "created_at":   time.time(),
                }
                state = _load()
                state.setdefault("tg_pending", {})[str(bot_id)] = new_entry
                state.setdefault("gmail_pending", {})[f_gid]    = new_entry
                _save(state)
            return
        if not pending_entry:
            _tg_send(
                "Reply directly to the email notification to draft a response.",
                reply_to=msg_id,
            )
            return

    # ── Morning journal (S4 character check + S6 gratitude) ──────────────────
    if text != "✅" and not text.startswith("/"):
        if GRATITUDE_FILE.exists():
            try:
                gv  = json.loads(GRATITUDE_FILE.read_text())
                exp = datetime.fromisoformat(gv.get("expires", "2000-01-01"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=CT)
                if exp > datetime.now(tz=CT):
                    try:
                        _execute_morning_gratitude(gv, GRATITUDE_FILE, msg_id, text)
                    except Exception as e:
                        _log("TG", f"gratitude exec error: {e}")
                        _tg_send(f"⚠️ Journal save failed: {e}", reply_to=msg_id)
                    return  # never fall through when gratitude is pending
            except Exception as e:
                _log("TG", f"gratitude parse error: {e}")

        if MORNING_FILE.exists():
            try:
                mj  = json.loads(MORNING_FILE.read_text())
                exp = datetime.fromisoformat(mj.get("expires", "2000-01-01"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=CT)
                if exp > datetime.now(tz=CT):
                    try:
                        _execute_morning_journal(mj, MORNING_FILE, msg_id, text)
                    except Exception as e:
                        _log("TG", f"morning journal exec error: {e}")
                        _tg_send(f"⚠️ Morning journal error: {e}", reply_to=msg_id)
                    return  # never fall through when journal is pending
            except Exception as e:
                _log("TG", f"morning journal parse error: {e}")

    # ── Text reply — grammar correction or relay to Archibald ────────────────
    # If no pending email draft, forward to Archibald — daemon only handles
    # grammar corrections when there is a draft in flight.
    if not pending_entry:
        _log("TG", f"relaying to Archibald: {text[:60]}")
        def _relay(msg_text: str) -> None:
            try:
                r = subprocess.run(
                    ["/opt/homebrew/bin/openclaw", "agent", "--message", msg_text],
                    capture_output=True, text=True, timeout=120,
                )
                response = r.stdout.strip()
                if response:
                    _tg_send(response)
                    _log("TG", f"Archibald replied ({len(response)} chars)")
                elif r.returncode != 0:
                    _log("TG", f"relay stderr: {r.stderr[:120]}")
            except Exception as e:
                _log("TG", f"relay error: {e}")
        threading.Thread(target=_relay, args=(text,), daemon=True).start()
        return

    words = text.split()
    if len(words) < 6:
        # Too short to need grammar correction — treat as plain acknowledgement
        if pending_entry:
            gmail_msg_id = pending_entry.get("gmail_msg_id", "")
            body  = f"<b>Draft saved:</b>\n\n{_h(text)}"
            body += (f"\n\nReply <b>✅</b> to send"
                     f"\n<code>ref:{gmail_msg_id}</code>")
            bot_msg_id = _tg_send(body, reply_to=msg_id)
            if bot_msg_id:
                updated           = dict(pending_entry)
                updated["draft"]  = text
                state = _load()
                state.setdefault("tg_pending", {})[str(bot_msg_id)] = updated
                state.setdefault("gmail_pending", {})[gmail_msg_id]  = updated
                _save(state)
        return

    try:
        corrected   = _fix_grammar(text)
        has_changes = corrected != text
    except Exception as e:
        _log("TG", f"grammar error: {e}")
        return

    body = f"<b>Corrected draft:</b>\n\n{_h(corrected)}" if has_changes else "✓ No corrections needed."

    if pending_entry:
        gid  = pending_entry.get("gmail_msg_id", "")
        body += (f"\n\nReply <b>✅</b> to send"
                 f"\n<code>ref:{gid}</code>")

    bot_msg_id = _tg_send(body, reply_to=msg_id)

    if pending_entry and bot_msg_id:
        updated          = dict(pending_entry)
        updated["draft"] = corrected if has_changes else pending_entry.get("draft", text)
        state = _load()
        state.setdefault("tg_pending", {})[str(bot_msg_id)] = updated
        state.setdefault("gmail_pending", {})[updated["gmail_msg_id"]] = updated
        _save(state)
        _log("TG", f"{'corrected' if has_changes else 'no change'} — pending updated")

# ── Pending action executors ───────────────────────────────────────────────────

def _execute_uv(ev: dict, action_file: Path, reply_msg_id: int) -> None:
    expires = datetime.fromisoformat(ev.get("expires", "2000-01-01"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=CT)
    if expires < datetime.now(tz=CT):
        action_file.unlink(missing_ok=True)
        _tg_send("That sun block has expired — it'll be back in tomorrow's briefing. 🎩", reply_to=reply_msg_id)
        return
    result = subprocess.run(
        [
            # Absolute, like every other call site: the daemon's launchd PATH is
            # /usr/bin:/bin:/usr/sbin:/sbin, so a bare "gog" is never resolvable.
            "/opt/homebrew/bin/gog", "cal", "create", "owner@example.com",
            "--account", "owner@example.com",
            "--summary", ev["summary"],
            "--from", ev["start"],
            "--to", ev["end"],
            "--description", f"Peak UV {ev['uv_index']} ({ev['uv_label']})",
            "--transparency", "transparent",
        ],
        capture_output=True, text=True, timeout=30,
        env=gog_env(),   # gog's file keyring (2026-10-02) needs GOG_KEYRING_PASSWORD; launchd has no env.vars
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gog cal create failed")
    action_file.unlink(missing_ok=True)
    if reply_msg_id is not None:
        _tg_send(
            f"You're on the calendar, Master Wayne — {ev['display_start']} to {ev['display_end']} CT. "
            f"UV will be {ev['uv_index']} ({ev['uv_label']}) then. Get outside. 🎩",
            reply_to=reply_msg_id,
        )
    _log("TG", f"UV event created: {ev['display_start']}-{ev['display_end']}")


def _execute_diary(ev: dict, action_file: Path, reply_msg_id: Optional[int]) -> None:
    header  = ev.get("date", datetime.now(tz=CT).strftime("%A, %B %-d, %Y"))
    bullets = ev.get("bullets", [])
    if not bullets:
        action_file.unlink(missing_ok=True)
        if reply_msg_id is not None:
            _tg_send("No diary bullets to log. 🎩", reply_to=reply_msg_id)
        return
    bullet_text = "\n".join(f"- {b}" for b in bullets)
    entry       = f"\n{header}\nToday I:\n{bullet_text}\n"
    with open(JS_LOG_FILE, "a") as f:
        f.write(entry)
    action_file.unlink(missing_ok=True)
    if reply_msg_id is not None:
        _tg_send("Logged, Master Wayne. 🎩", reply_to=reply_msg_id)
    _log("TG", f"diary logged: {header}")


def _execute_morning_journal(ev: dict, action_file: Path, reply_msg_id: Optional[int], response_text: str) -> None:
    """Capture the S4 character check response; write gratitude pending and ask S6."""
    date_str = ev.get("date", datetime.now(tz=CT).strftime("%Y-%m-%d"))
    action_file.unlink(missing_ok=True)
    GRATITUDE_FILE.write_text(json.dumps({
        "date":        date_str,
        "s4_response": response_text,
        "expires":     (datetime.now(tz=CT) + timedelta(hours=2)).isoformat(),
    }))
    _tg_send(
        "Good work! Now for some gratitude.\n\nWhat three things are you truly grateful for today?",
        reply_to=reply_msg_id,
    )
    _log("TG", f"morning journal S4 captured: {date_str}")


def _execute_morning_gratitude(ev: dict, action_file: Path, reply_msg_id: Optional[int], response_text: str) -> None:
    """Parse S6 gratitude items and save S4+S6 to the morning journal."""
    date_str = ev.get("date", datetime.now(tz=CT).strftime("%Y-%m-%d"))
    s4       = ev.get("s4_response", "")

    lines = [ln.strip() for ln in response_text.splitlines() if ln.strip()]
    items = []
    for ln in lines:
        for prefix in ("1.", "2.", "3.", "1)", "2)", "3)", "-", "•"):
            if ln.startswith(prefix):
                ln = ln[len(prefix):].strip()
                break
        if ln:
            items.append(ln)
    if not items:
        items = [response_text.strip()]
    items = items[:3]

    cmd = ["python3", "-W", "ignore", MORNING_SCRIPT,
           "--save-journal", "--response", s4, "--date", date_str]
    for item in items:
        cmd += ["--gratitude", item]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    action_file.unlink(missing_ok=True)

    if result.returncode != 0:
        _tg_send(f"⚠️ Journal save failed: {result.stderr[:200]}", reply_to=reply_msg_id)
        _log("TG", f"morning gratitude save failed: {result.stderr[:100]}")
        return

    _tg_send("Documented. Have a great day, Master Wayne. Keep Hammering! 🪓⛏️", reply_to=reply_msg_id)
    _log("TG", f"morning journal saved (S4+S6): {date_str}")

    # Send any pending UV proposal now that the morning reflection is complete
    if UV_FILE.exists():
        try:
            uv = json.loads(UV_FILE.read_text())
            exp = datetime.fromisoformat(uv.get("expires", "2000-01-01"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=CT)
            if exp > datetime.now(tz=CT):
                bot_msg_id = _tg_send_with_buttons(uv.get("proposal_text", ""), "uv")
                if bot_msg_id:
                    uv["source_tg_msg_id"] = bot_msg_id
                    UV_FILE.write_text(json.dumps(uv))
                _log("TG", "UV proposal sent after morning reflection")
        except Exception as e:
            _log("TG", f"UV proposal error: {e}")


# ── Threads ────────────────────────────────────────────────────────────────────
_running    = True
_clean_exit = False   # set True on SIGTERM so crash alert doesn't fire

def telegram_loop() -> None:
    import requests
    _log("TG", "thread started — long-polling")
    while _running:
        try:
            state   = _load()
            offset  = state.get("tg_last_update_id")
            params: dict = {"timeout": TG_POLL_TIMEOUT, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset
            r       = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                                   params=params, timeout=TG_POLL_TIMEOUT + 5)
            updates = r.json().get("result", [])
            if updates:
                new_last = updates[-1]["update_id"] + 1
                for upd in updates:
                    try:
                        if "callback_query" in upd:
                            handle_callback_query(upd["callback_query"])
                        else:
                            handle_telegram_msg(upd.get("message", {}))
                    except Exception as e:
                        _log("TG", f"handler error: {e}")
                state = _load()
                state["tg_last_update_id"] = new_last
                _save(state)
        except Exception as e:
            _log("TG", f"poll error: {e}")
            time.sleep(5)

GMAIL_FAIL_ALERT_THRESHOLD = 5     # consecutive failures (~5 min) before first alert
GMAIL_FAIL_ALERT_INTERVAL  = 3600  # re-alert at most once per hour while still broken


def _save_forwarding_watch(watch: dict) -> None:
    state = _load()
    state[_fwd.STATE_KEY] = watch
    _save(state)


def _forwarding_watch() -> None:
    """Every 15 min: alert at once if Gmail forwarding or auto-forwarding appears.

    A report that doesn't reach Telegram is not recorded, so the next cycle
    retries it. Repeated read failures alert too — a silent alarm is no alarm.
    """
    watch = _load().get(_fwd.STATE_KEY, {})
    now = time.time()
    if not _fwd.due(watch, now):
        return
    try:
        snap = _fwd.read_settings(_gmail())
    except Exception as e:
        _MONITOR.record_source(_fwd.SOURCE, "unavailable", detail=str(e)[:300])
        message, new = _fwd.record_failure(watch, str(e), now)
        if message and not _tg_send(message):
            new.pop("failure_alerted_at", None)
            if "failure_alerted_at" in watch:
                new["failure_alerted_at"] = watch["failure_alerted_at"]
        _save_forwarding_watch(new)
        raise
    message, new = _fwd.evaluate(snap, watch, now)
    if message and not _tg_send(message):
        _MONITOR.record_source(_fwd.SOURCE, "partial", detail="alert not delivered; retrying next cycle")
        raise DeliveryFailed("forwarding alert was not delivered")
    _save_forwarding_watch(new)
    _MONITOR.record_source(_fwd.SOURCE, "complete", coverage={
        "forwarding_addresses": len(snap["addresses"]), "auto_forwarding": snap["auto_enabled"]})
    if message:
        _log("Gmail", "forwarding alarm: reported a change")

ARRIVAL_SEEN_MAX = 1500
ARRIVAL_THREAD_INTERVAL_S = 6 * 3600
_ARRIVAL_HEADERS = ["From", "To", "Cc", "Subject", "List-Unsubscribe", "In-Reply-To", "Auto-Submitted", "X-Autoreply"]
ARRIVAL_LOGIC_VERSION = 3     # bump when arrival decisions change, so mail already in the inbox is re-decided


def _classify_type(msg: dict) -> str:
    """One constrained question to the fast model: what kind of email is this?"""
    import anthropic
    system, user = _arrive.ai_prompt(f"{msg.get('name', '')} <{msg.get('addr', '')}>",
                                     msg.get("subject", ""), msg.get("snippet", ""))
    try:
        resp = anthropic.Anthropic(api_key=API_KEY).messages.create(
            model=MODEL_FAST, max_tokens=8, temperature=0, system=system,
            messages=[{"role": "user", "content": user}])
        log_usage("daemon.py:_classify_type", MODEL_FAST, resp.usage, response_id=getattr(resp, "id", None))
        return _arrive.parse_ai_type(resp.content[0].text)
    except Exception as e:
        _log("Filing", f"type classify error: {e}")
        return "unsure"


def _hdrs(message: dict) -> dict:
    return {h["name"]: h["value"] for h in message.get("payload", {}).get("headers", [])}


def _arrival_message(svc, mid: str, names: dict) -> dict:
    """Metadata E1 decides on, including whether the user corresponds with this sender."""
    full = svc.users().messages().get(userId="me", id=mid, format="metadata",
                                      metadataHeaders=_ARRIVAL_HEADERS).execute()
    hdr = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
    frm = hdr.get("From", "")
    name = frm.split("<")[0].strip().strip('"')
    addr = _extract_email(frm).lower()
    ids = full.get("labelIds", [])
    automated = _refile.is_automated(addr, ids, "List-Unsubscribe" in hdr)
    thread = svc.users().threads().get(userId="me", id=full["threadId"], format="metadata",
                                       metadataHeaders=_ARRIVAL_HEADERS).execute()
    replied = any(_arrive.is_real_sent(m.get("labelIds", []), _hdrs(m)) for m in thread.get("messages", []))
    written = False
    if not replied and not automated and addr:
        for ref in svc.users().messages().list(userId="me", q=f"from:me to:{addr}",
                                               maxResults=5).execute().get("messages", []):
            m = svc.users().messages().get(userId="me", id=ref["id"], format="metadata",
                                           metadataHeaders=_ARRIVAL_HEADERS).execute()
            if _arrive.is_real_sent(m.get("labelIds", []), _hdrs(m)):
                written = True
                break
    conversation = _arrive.corresponds(automated, replied, written)
    return {"id": mid, "name": name, "addr": addr, "subject": hdr.get("Subject", ""),
            "snippet": full.get("snippet", ""), "label_ids": ids,
            "labels": [names.get(i, i) for i in ids], "starred": "STARRED" in ids,
            "automated": automated, "conversation": conversation}


def _arrival_apply(svc, mid: str, change: dict, names: dict) -> None:
    by_name = {v: k for k, v in names.items()}
    add = []
    for label in change["add"]:
        label_id = by_name.get(label) or _ff.resolve_label_id(svc, label)
        if not label_id:
            raise RuntimeError(f"could not find or create label {label!r}")
        names[label_id] = label
        add.append(label_id)
    remove = [by_name[n] if n != "INBOX" else "INBOX" for n in change["remove"] if n == "INBOX" or n in by_name]
    svc.users().messages().modify(userId="me", id=mid,
                                  body={"addLabelIds": add, "removeLabelIds": remove}).execute()


def _arrival_pass(svc, message_ids: list, live: bool, use_ai: bool = True) -> list:
    """Decide (and in live mode apply) filing for these inbox messages."""
    names = {l["id"]: l["name"] for l in svc.users().labels().list(userId="me").execute().get("labels", [])}
    rules = _refile.load_rules()
    out = []
    for mid in message_ids:
        msg = _arrival_message(svc, mid, names)
        kind = _policy.classify_subject(msg["subject"], msg["label_ids"])
        finance_route = _finance_mail.classify(msg["addr"], msg["subject"])
        if finance_route["action"] == "file":
            change = {"add": [finance_route["folder"]], "remove": ["INBOX"],
                      "why": f"finance rule {finance_route['rule_id']}"}
        elif finance_route["action"] == "surface":
            change = None
        else:
            change = _arrive.decide_message(msg, rules, kind, _classify_type if use_ai else None)
        entry = {"id": mid, "from": msg["addr"], "subject": msg["subject"][:90], "kind": kind,
                 "decision": ({"add": change["add"], "remove": change["remove"], "why": change["why"]}
                              if change else None)}
        if change and live:
            _arrival_apply(svc, mid, change, names)
            entry["applied"] = True
        out.append(entry)
    return out


def _arrival_thread(svc, tid: str, names: dict) -> Optional[dict]:
    """A person-to-person inbox thread, summarised for decide_thread(); None for automated mail."""
    th = svc.users().threads().get(userId="me", id=tid, format="metadata",
                                   metadataHeaders=_ARRIVAL_HEADERS).execute()
    msgs = th.get("messages", [])
    if not msgs:
        return None
    hdrs = _hdrs
    # The user's vacation auto-replies are not part of the conversation.
    msgs = [m for m in msgs if "SENT" not in m.get("labelIds", [])
            or _arrive.is_real_sent(m.get("labelIds", []), hdrs(m))]
    if not msgs:
        return None
    others, people = [], False
    for m in msgs:
        if "SENT" in m.get("labelIds", []):
            continue
        h = hdrs(m)
        addr = _extract_email(h.get("From", "")).lower()
        others.append(addr)
        if not _refile.is_automated(addr, m.get("labelIds", []), "List-Unsubscribe" in h):
            people = True
    if not people:
        return None                       # automated mail is the message pass's job
    last, first = msgs[-1], msgs[0]
    return {"thread_id": tid, "last_ts": int(last.get("internalDate", "0")) / 1000,
            "owner_last": "SENT" in last.get("labelIds", []), "last_snippet": last.get("snippet", ""),
            "starred": any("STARRED" in m.get("labelIds", []) for m in msgs),
            "addrs": sorted(set(others)), "subject": hdrs(first).get("Subject", "")[:90],
            "message_ids": [m["id"] for m in msgs],
            "outreach": "SENT" in first.get("labelIds", []) and _networking.is_outreach(hdrs(first), first.get("snippet", ""))}


def _arrival_threads_pass(svc, live: bool) -> list:
    names = {l["id"]: l["name"] for l in svc.users().labels().list(userId="me").execute().get("labels", [])}
    out, token = [], None
    while True:
        page = svc.users().threads().list(userId="me", q="in:inbox", maxResults=100, pageToken=token).execute()
        for ref in page.get("threads", []):
            thread = _arrival_thread(svc, ref["id"], names)
            if not thread:
                continue
            verdict = _arrive.decide_thread(thread)
            if not verdict:
                continue
            entry = {"thread": ref["id"], "with": ", ".join(thread["addrs"])[:80], "subject": thread["subject"],
                     "decision": verdict}
            if live and verdict["action"] == "archive":
                change = {"add": [verdict["label"]] if verdict["label"] else [], "remove": ["INBOX"]}
                for mid in thread["message_ids"]:
                    _arrival_apply(svc, mid, change, names)
                entry["applied"] = True
            out.append(entry)     # a "nudge" is only reported: the thread stays put
        token = page.get("nextPageToken")
        if not token:
            return out


def _arrival_filing() -> None:
    """E1: file finished inbox mail by the user's rules; let quiet conversations leave.

    Only messages the inbox loop has fully handled (processed_ids — any alert
    was delivered) are considered, each once. "watch" mode logs decisions to
    logs/arrival_filing.jsonl and changes nothing.
    """
    live = _arrive.mode() == "live"
    state = _load()
    # Re-decide what is sitting in the inbox whenever the mode, the decision
    # logic or the rulebook changes — otherwise a fix never reaches old mail.
    try:
        rulebook = int(_refile.SPEC.stat().st_mtime)
    except OSError:
        rulebook = 0
    signature = f"{'live' if live else 'watch'}:{ARRIVAL_LOGIC_VERSION}:{rulebook}"
    if state.get("arrival_mode") != signature:
        state["arrival_mode"], state["arrival_seen"] = signature, []
        state.pop("arrival_threads_at", None)
    seen = set(state.get("arrival_seen", []))
    pending_drafts = set(state.get("gmail_pending", {}))
    handled = set(state.get("processed_ids", []))
    _save(state)
    svc = _gmail()
    inbox = [m["id"] for m in svc.users().messages().list(
        userId="me", q="in:inbox", maxResults=200).execute().get("messages", [])]
    todo = [mid for mid in inbox if mid in handled and mid not in seen and mid not in pending_drafts]
    results = _arrival_pass(svc, todo, live) if todo else []
    threads_due = time.time() - float(state.get("arrival_threads_at", 0)) >= ARRIVAL_THREAD_INTERVAL_S
    threads = _arrival_threads_pass(svc, live) if threads_due else []
    state = _load()
    state["arrival_seen"] = (state.get("arrival_seen", []) + todo)[-ARRIVAL_SEEN_MAX:]
    if threads_due:
        state["arrival_threads_at"] = time.time()
    _save(state)
    for entry in results + threads:
        if entry.get("decision"):
            _arrive.record({"mode": "live" if live else "watch", **entry})
    filed = sum(1 for e in results if e.get("decision"))
    if filed or threads:
        _log("Filing", f"[{'live' if live else 'watch'}] {filed}/{len(results)} messages, "
                       f"{len(threads)} conversations decided")
    _MONITOR.record_source("email:arrival-filing", "complete",
                           coverage={"mode": "live" if live else "watch", "messages": len(results),
                                     "filed": filed, "threads": len(threads)})


def _run_poll_cycle() -> None:
    """One Gmail poll: the inbox, then every catch-up, then the backstop.

    TeleVisit reminders never carry the INBOX label (routed straight to a
    Gmail filter/label), so the inbox query can never see them — the catch-up is
    the only thing that catches them, every cycle.

    Each step runs even if an earlier one failed: a transient error in one
    catch-up must never skip the labeled-out backstop, which is the security
    net. Failures are collected and re-raised afterwards so they still count
    toward the consecutive-failure alert.
    """
    errors = []
    for name, step in (
            ("inbox", run_gmail_check),
            ("sender proposals", lambda: _sender_proposal_catchup(_gmail())),
            ("televisit", _televisit_catchup),
            ("travel", _travel_label_catchup),
            ("reservations", _reservations_label_catchup),
            ("backstop", _labeled_out_backstop),
            ("forwarding alarm", _forwarding_watch),
            ("arrival filing", _arrival_filing),
            ("cal sweep", _sweep_stale_cal_proposals)):
        try:
            step()
        except Exception as e:
            _log("Gmail", f"{name} step error: {e}")
            errors.append(f"{name}: {e}")
    if errors:
        raise RuntimeError("; ".join(errors))


def gmail_loop() -> None:
    _log("Gmail", "thread started — polling every 60 s")
    fail_streak = 0
    last_alert_ts = 0.0
    while _running:
        try:
            _run_poll_cycle()
            if fail_streak >= GMAIL_FAIL_ALERT_THRESHOLD:
                _tg_send(f"✅ Email daemon recovered after {fail_streak} failed checks in a row.")
            fail_streak = 0
        except Exception as e:
            _log("Gmail", f"check error: {e}")
            fail_streak += 1
            if fail_streak == GMAIL_FAIL_ALERT_THRESHOLD or (
                fail_streak > GMAIL_FAIL_ALERT_THRESHOLD
                and time.time() - last_alert_ts >= GMAIL_FAIL_ALERT_INTERVAL
            ):
                try:
                    _tg_send(f"⚠️ Email daemon: {fail_streak} consecutive Gmail check failures. "
                             f"Latest error: {e}")
                except Exception:
                    pass
                last_alert_ts = time.time()
        time.sleep(GMAIL_INTERVAL)

# ── Crash alert ────────────────────────────────────────────────────────────────
def _crash_alert() -> None:
    if _clean_exit:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID,
                  "text": "⚠️ Email daemon crashed unexpectedly. launchd is restarting it. 🎩"},
            timeout=10,
        )
    except Exception:
        pass

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TG_TOKEN or not API_KEY:
        # Fail loudly, not silently: TG_TOKEN/API_KEY resolve to "" from
        # _get_key() on any lookup failure (missing env var, unreadable or
        # restructured openclaw.json, renamed key) rather than raising, and
        # every call site that uses them just catches its own exception and
        # logs/skips — so a resolution failure here would otherwise start the
        # daemon looking healthy while every Telegram send and Anthropic call
        # fails silently underneath it.
        missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", TG_TOKEN), ("ANTHROPIC_API_KEY", API_KEY)) if not v]
        _log("daemon", f"FATAL: missing required credential(s): {', '.join(missing)} "
                        f"— check env vars or openclaw.json env.vars")
        sys.exit(1)
    atexit.register(_crash_alert)
    _log("daemon", f"starting  pid={__import__('os').getpid()}")

    # Telegram long-polling is handled exclusively by the OpenClaw gateway
    # (Archibald). The daemon only sends notifications via sendMessage — it
    # does NOT poll getUpdates to avoid a 409 conflict.
    t_gmail = threading.Thread(target=gmail_loop, name="gmail", daemon=True)
    t_gmail.start()

    def _shutdown(*_):
        global _running, _clean_exit
        _running    = False
        _clean_exit = True
        _log("daemon", "shutdown signal received")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    t_gmail.join()
