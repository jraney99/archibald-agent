#!/usr/bin/env python3
"""
Email monitor — polls Gmail inbox for new emails, classifies importance with Claude,
sends Telegram notifications with auto-drafted replies for important emails.

State: ~/.openclaw/email_monitor_state.json
Runs every 15 minutes via system crontab.
"""
import argparse, base64, html, json, os, re, sys, time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TOKEN_FILE  = os.path.expanduser("~/.openclaw/gmail_monitor_token.json")
STATE_FILE  = os.path.expanduser("~/.openclaw/email_monitor_state.json")
SCOPES      = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar.readonly",
]
MY_EMAIL    = "owner@example.com"
TG_TOKEN    = __import__("runpy").run_path(os.path.expanduser("~/.openclaw/workspace/Skills/shared/runtime_secrets.py"))["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID  = "1000000001"
API_KEY     = "REDACTED"
MODEL       = "claude-sonnet-4-6"
LOOKBACK_S  = 7200   # 2-hour lookback; dedup via processed_ids prevents re-notifications
CT          = ZoneInfo("America/Chicago")

# Addresses/local-part patterns that are definitively automated
AUTOSEND_RE = re.compile(
    r'^(no.?reply|donotreply|do.not.reply|noreply|mailer.daemon|'
    r'postmaster|bounce|bounces|unsubscribe|newsletter)$',
    re.IGNORECASE,
)

# ── Gmail helpers ──────────────────────────────────────────────────────────────

def get_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    path = Path(TOKEN_FILE)
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    return creds

def get_service():
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)

def get_calendar():
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)

def get_signature() -> str:
    """Fetch the user's Gmail signature and return as plain text."""
    from googleapiclient.discovery import build
    svc = build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)
    entries = svc.users().settings().sendAs().list(userId="me").execute().get("sendAs", [])
    sig_html = ""
    for e in entries:
        if e.get("isDefault"):
            sig_html = e.get("signature", "")
            break
    # HTML → plain text
    sig = re.sub(r'<br\s*/?>', '\n', sig_html, flags=re.IGNORECASE)
    sig = re.sub(r'<[^>]+>', '', sig)
    sig = re.sub(r'\n{3,}', '\n\n', sig)
    return sig.strip()


# ── Calendar helpers ───────────────────────────────────────────────────────────

def _parse_event_dt(val) -> datetime:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
        return dt if dt.tzinfo else dt.replace(tzinfo=CT)
    except Exception:
        return None


def find_free_slots(lookahead_days: int = 7, slot_minutes: int = 30,
                    day_start_h: int = 9, day_end_h: int = 18,
                    max_slots: int = 4) -> list[tuple[datetime, datetime]]:
    """Return up to max_slots free windows across the next lookahead_days business days."""
    cal = get_calendar()
    now = datetime.now(CT)
    window_end = now + timedelta(days=lookahead_days)

    events_raw = cal.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=window_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute().get("items", [])

    # Build a list of busy intervals
    busy: list[tuple[datetime, datetime]] = []
    for e in events_raw:
        s = _parse_event_dt(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"))
        en = _parse_event_dt(e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"))
        if s and en:
            busy.append((s.astimezone(CT), en.astimezone(CT)))
    busy.sort()

    free: list[tuple[datetime, datetime]] = []
    day = now.date() + timedelta(days=1)  # start tomorrow

    while day <= window_end.date() and len(free) < max_slots:
        if day.weekday() >= 5:  # skip weekends
            day += timedelta(days=1)
            continue

        cursor = datetime(day.year, day.month, day.day, day_start_h, 0, tzinfo=CT)
        day_close = datetime(day.year, day.month, day.day, day_end_h, 0, tzinfo=CT)

        day_busy = [(s, e) for s, e in busy if s.date() == day]

        for b_start, b_end in day_busy:
            gap_end = min(b_start, day_close)
            if (gap_end - cursor) >= timedelta(minutes=slot_minutes):
                free.append((cursor, gap_end))
                if len(free) >= max_slots:
                    break
            cursor = max(cursor, b_end)

        # Gap after last event
        if len(free) < max_slots and (day_close - cursor) >= timedelta(minutes=slot_minutes):
            free.append((cursor, day_close))

        day += timedelta(days=1)

    return free[:max_slots]


def check_specific_slot(proposed_iso, duration_minutes=30):
    """
    Check whether a specific ISO datetime string is free.
    Returns (is_free, conflict_title_or_empty).
    """
    cal = get_calendar()
    try:
        start = datetime.fromisoformat(proposed_iso).astimezone(CT)
    except Exception:
        return True, ""  # can't parse → assume free, let Claude handle it

    end = start + timedelta(minutes=duration_minutes)

    events_raw = cal.events().list(
        calendarId="primary",
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
    ).execute().get("items", [])

    for e in events_raw:
        return False, e.get("summary", "another meeting")

    return True, ""


def format_slots(slots: list[tuple[datetime, datetime]]) -> str:
    """Human-readable slot list for CT."""
    lines = []
    for s, _ in slots:
        lines.append(s.strftime("• %A %b %-d at %-I:%M %p CT"))
    return "\n".join(lines)


def get_body(msg_full: dict) -> str:
    def decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""

    def extract(part: dict) -> str:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return decode(data)
        for sub in part.get("parts", []):
            r = extract(sub)
            if r:
                return r
        return ""

    raw = extract(msg_full.get("payload", {}))
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    raw = re.sub(r'[ \t]+', ' ', raw)
    return strip_quoted(raw.strip())


def strip_quoted(text: str) -> str:
    """Return only the newest reply, dropping quoted thread history."""
    lines = text.splitlines()
    cut = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        # "> quoted text" (standard mbox style)
        if s.startswith('>'):
            cut = i; break
        # "On Mon, Jun 15 ... wrote:" (Gmail style)
        if re.match(r'^On .{10,120} wrote:$', s):
            cut = i; break
        # Outlook-style block: "From: X\nSent: Y\nTo: Z\nSubject: W"
        if s.startswith('From:') and i + 2 < len(lines):
            next_lines = ' '.join(l.strip() for l in lines[i+1:i+4])
            if re.search(r'Sent:|To:|Subject:', next_lines):
                cut = i; break
        # Email signature separator
        if s == '--':
            cut = i; break
        # Security banners like "EXTERNAL EMAIL -"
        if s.startswith('EXTERNAL EMAIL'):
            cut = i; break

    result = '\n'.join(lines[:cut]).strip()
    # If stripping left us with nothing (e.g. forwarded-only email), return original
    return result if result else text


def fetch_new_messages(service, since_epoch: int):
    q = f"in:inbox after:{since_epoch}"
    msgs, page_token = [], None
    while True:
        kw = {"userId": "me", "q": q, "maxResults": 50}
        if page_token:
            kw["pageToken"] = page_token
        r = service.users().messages().list(**kw).execute()
        msgs.extend(r.get("messages", []))
        page_token = r.get("nextPageToken")
        if not page_token:
            break
    return msgs


# ── Layer 1: header-based filter ───────────────────────────────────────────────

def layer1_passes(hdrs: dict) -> tuple[bool, str]:
    # Bulk/list markers
    if hdrs.get("list-unsubscribe"):
        return False, "List-Unsubscribe present"
    prec = hdrs.get("precedence", "").lower()
    if prec in ("bulk", "list", "junk"):
        return False, f"Precedence: {prec}"
    auto = hdrs.get("auto-submitted", "").lower()
    if auto and auto != "no":
        return False, f"Auto-Submitted: {auto}"

    # No-reply sender
    from_raw = hdrs.get("from", "")
    m = re.search(r'<([^>]+)>', from_raw)
    addr = m.group(1).lower() if m else from_raw.strip().lower()
    local = addr.split("@")[0] if "@" in addr else addr
    if AUTOSEND_RE.match(local):
        return False, f"no-reply sender: {local}"

    return True, "ok"


# ── Layer 2: Claude classification ────────────────────────────────────────────

CLASSIFY_PROMPT = """\
You are classifying emails for the user — an entrepreneur and investor.
He is networking actively.

Also write a brief summary (1-2 sentences, plain English, no filler words) of what the email
actually says — what the person is doing, asking, or offering. Be specific and factual.

Classify each email into exactly one of three tiers:

"action" — Requires a reply. Intros, questions, deal discussions, follow-ups where silence
  would be rude or costly. Also includes meeting requests, tour invitations, visit scheduling,
  or any email where the sender is waiting on the user to confirm, respond, or take a next step.

"fyi" — From a real human contact, no reply needed right now. Acknowledgments, confirmations,
  updates from people the user has a relationship with. He wants to see these.

"skip" — Automated, bulk, or irrelevant. Receipts, alerts, newsletters, system notifications,
  anything not from a specific person the user knows or is building a relationship with.

Also detect meeting requests:
- If someone is asking for availability generally ("when are you free?", "find a time this week"),
  set meeting_request to "open".
- If someone is proposing a SPECIFIC time ("are you free Tuesday at 2pm?", "how about Thursday
  morning?", "I can do June 25 at 1pm"), set meeting_request to "specific" and extract the
  proposed time as a plain English string in proposed_time.
- Otherwise set meeting_request to null.

If "action": write a draft reply placeholder — use [AVAILABILITY] where availability info
  should be inserted for meeting requests. For non-meeting emails, write the full draft.
  Keep drafts under 100 words. The user's voice: direct, warm, entrepreneurial.
If "fyi" or "skip": leave draft empty.

Respond ONLY with valid JSON — no markdown, no extra text:
{
  "tier": "action"|"fyi"|"skip",
  "meeting_request": "open"|"specific"|null,
  "proposed_time": "string or null",
  "summary": "1-2 sentence factual summary of what the email says",
  "reason": "one sentence explaining the tier",
  "draft": "reply with [AVAILABILITY] placeholder or empty"
}"""

DRAFT_WITH_SLOTS_PROMPT = """\
You are writing an email reply for the user. He is an entrepreneur and investor.
His voice is direct, warm, and entrepreneurial.

Replace the [AVAILABILITY] placeholder in the draft with the actual availability information
provided. Keep the reply natural and concise — under 120 words total.

Respond with ONLY the final reply text, no JSON, no markdown."""


def classify(sender: str, subject: str, body: str) -> dict:
    """Returns dict with tier, meeting_request, proposed_time, reason, draft."""
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            temperature=0,
            system=CLASSIFY_PROMPT,
            messages=[{"role": "user",
                        "content": f"From: {sender}\nSubject: {subject}\n\nLatest message:\n{body}"}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            return {"tier": "skip", "meeting_request": None, "proposed_time": None,
                    "reason": f"no JSON: {raw[:60]}", "draft": ""}
        data = json.loads(m.group(0))
        tier = data.get("tier", "skip")
        if tier not in ("action", "fyi", "skip"):
            tier = "skip"
        return {
            "tier": tier,
            "meeting_request": data.get("meeting_request"),
            "proposed_time": data.get("proposed_time"),
            "summary": data.get("summary", ""),
            "reason": data.get("reason", ""),
            "draft": data.get("draft", ""),
        }
    except Exception as e:
        return {"tier": "skip", "meeting_request": None, "proposed_time": None,
                "summary": "", "reason": f"classify error: {e}", "draft": ""}


def resolve_meeting_draft(draft_template, meeting_type,
                           proposed_time):
    """Replace [AVAILABILITY] in the draft with real calendar-based info."""
    import anthropic

    if meeting_type == "open":
        slots = find_free_slots()
        if slots:
            avail_text = "I have the following times open:\n" + format_slots(slots)
        else:
            avail_text = "I'm pretty booked this week — let me check and get back to you."

    elif meeting_type == "specific" and proposed_time:
        # Try to parse proposed time for exact check — fall back to text if unparseable
        is_free, conflict = True, ""
        # Attempt a loose parse using the free slots approach as heuristic
        slots = find_free_slots(lookahead_days=14)
        # Check if proposed time text matches any free window — simple heuristic:
        # pass to Claude to generate availability-aware response
        avail_text = (
            f"The time they proposed: {proposed_time}.\n"
            f"My upcoming free windows: {format_slots(slots) if slots else 'checking calendar...'}"
        )
    else:
        return draft_template.replace("[AVAILABILITY]", "").strip()

    client = anthropic.Anthropic(api_key=API_KEY)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=DRAFT_WITH_SLOTS_PROMPT,
        messages=[{"role": "user",
                   "content": f"Draft template:\n{draft_template}\n\nAvailability info:\n{avail_text}"}],
    )
    return resp.content[0].text.strip()


# ── Telegram ────────────────────────────────────────────────────────────────────

TG_LIMIT = 4096

def send_telegram(text: str):
    """Send a Telegram message. Returns (status_code, last_message_id)."""
    import requests
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    chunks = []
    while len(text) > TG_LIMIT:
        cut = text[:TG_LIMIT].rfind('\n\n')
        if cut == -1:
            cut = text[:TG_LIMIT].rfind('\n')
        if cut == -1:
            cut = TG_LIMIT
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    chunks.append(text)

    status = 200
    last_msg_id = None
    for chunk in chunks:
        r = requests.post(url, json={
            "chat_id": TG_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        status = r.status_code
        if status != 200:
            print(f"  TG error: {r.text[:200]}", flush=True)
            break
        last_msg_id = r.json().get("result", {}).get("message_id")
        time.sleep(0.3)
    return status, last_msg_id


def _h(text: str) -> str:
    """Escape text for Telegram HTML."""
    return html.escape(str(text))


def _display_name(sender: str) -> str:
    """Extract 'First Last' from 'First Last <email@domain.com>'."""
    m = re.match(r'^(.+?)\s*<', sender)
    return m.group(1).strip() if m else sender


def _extract_email(sender: str) -> str:
    """Extract a valid email address from a From: header. Falls back to regex scan."""
    m = re.search(r'<([^>]+)>', sender)
    addr = m.group(1).strip() if m else sender.strip()
    if '@' in addr and '.' in addr.split('@')[-1]:
        return addr
    # Angle brackets present but no domain — scan raw string for a real email
    m2 = re.search(r'[\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,}', sender)
    return m2.group(0) if m2 else addr


def format_notification(tier, sender, subject, body, summary, reason, draft, msg_id):
    link  = f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"
    icon  = "📧"
    label = "<b>Action Needed</b>" if tier == "action" else "<b>FYI</b>"
    name  = _h(_display_name(sender))
    subj  = _h(subject)
    summ  = _h(summary) if summary else _h(reason)

    parts = [
        f"{icon} {label}",
        f"",
        f"<b>{name}</b> — {subj}",
        f"",
        f"Summary: {summ}",
    ]

    if body:
        body_preview = body if len(body) <= 600 else body[:600].rsplit(' ', 1)[0] + "…"
        parts.append(f"\n<blockquote>{_h(body_preview)}</blockquote>")

    if draft:
        parts.append(f"\nDraft reply:")
        parts.append(f"<blockquote>{_h(draft)}</blockquote>")
        if tier == "action":
            parts.append(f"\nReply <b>✅</b> to send · or reply with your own text")
            parts.append(f"<code>ref:{msg_id}</code>")

    parts.append(f"\n<a href=\"{link}\">Open in Gmail</a>")
    return "\n".join(parts)


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify emails but don't send Telegram or update state")
    parser.add_argument("--hours", type=int, default=None,
                        help="Look back this many hours (overrides state file)")
    args = parser.parse_args()

    dry_run = args.dry_run

    # Load state
    state_path = Path(STATE_FILE)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    if args.hours:
        since = int(time.time()) - args.hours * 3600
        print(f"Looking back {args.hours} hours (manual override)", flush=True)
    else:
        since = int(time.time()) - LOOKBACK_S

    now            = int(time.time())
    processed_ids  = set(state.get("processed_ids", []))
    signature      = get_signature()

    service = get_service()
    msgs    = fetch_new_messages(service, since)
    mode    = "[DRY RUN] " if dry_run else ""
    print(f"{mode}{datetime.now(timezone.utc).isoformat()} — {len(msgs)} new inbox messages", flush=True)

    notified     = 0
    new_ids: list[str] = []
    for msg in msgs:
        msg_id = msg["id"]

        if msg_id in processed_ids:
            print(f"  already notified, skipping: {msg_id}", flush=True)
            continue

        full = service.users().messages().get(
            userId="me", id=msg_id, format="full",
        ).execute()

        hdrs_raw = full.get("payload", {}).get("headers", [])
        hdrs = {h["name"].lower(): h["value"] for h in hdrs_raw}

        sender  = hdrs.get("from", "unknown")
        subject = hdrs.get("subject", "(no subject)")

        # Layer 1
        passes, reason = layer1_passes(hdrs)
        if not passes:
            print(f"  L1 skip [{reason}]: {subject[:60]}", flush=True)
            new_ids.append(msg_id)
            continue

        body = get_body(full)

        # Layer 2 — classify and detect meeting requests
        result = classify(sender, subject, body[:2000])
        tier        = result["tier"]
        cl_reason   = result["reason"]
        summary     = result["summary"]
        draft       = result["draft"]
        mtg_type    = result["meeting_request"]
        proposed_t  = result["proposed_time"]

        if tier == "skip":
            print(f"  L2 skip [{cl_reason}]: {subject[:60]}", flush=True)
            new_ids.append(msg_id)
            continue

        # Resolve meeting availability into the draft
        if tier == "action" and mtg_type in ("open", "specific") and "[AVAILABILITY]" in draft:
            print(f"  Calendar lookup ({mtg_type} request)...", flush=True)
            draft = resolve_meeting_draft(draft, mtg_type, proposed_t)

        # Notify (or just print in dry-run)
        if draft:
            draft = draft.rstrip() + f"\n\n{signature}"
        notification = format_notification(tier, sender, subject, body, summary, cl_reason, draft, msg_id)
        tag = "ACTION" if tier == "action" else "FYI"
        if dry_run:
            print(f"\n  *** WOULD NOTIFY [{tag}] ***", flush=True)
            print(f"  From:    {sender}", flush=True)
            print(f"  Subject: {subject}", flush=True)
            print(f"  Why:     {cl_reason}", flush=True)
            if draft:
                print(f"  Draft:   {draft}", flush=True)
        else:
            status, tg_msg_id = send_telegram(notification)
            print(f"  NOTIFIED [{tag}] [TG {status}]: {subject[:60]}", flush=True)
            if tg_msg_id and tier == "action" and draft:
                entry = {
                    "gmail_msg_id": msg_id,
                    "thread_id": full.get("threadId", ""),
                    "to_email": _extract_email(sender),
                    "subject": subject,
                    "rfc_msg_id": hdrs.get("message-id", ""),
                    "draft": draft,
                }
                # tg_pending: keyed by Telegram msg ID (for grammar bot reply chaining)
                tg_pending = state.setdefault("tg_pending", {})
                tg_pending[str(tg_msg_id)] = entry
                if len(tg_pending) > 50:
                    for k in list(tg_pending.keys())[:-50]:
                        del tg_pending[k]
                # gmail_pending: keyed by Gmail msg ID (for main agent via send_pending.py)
                state.setdefault("gmail_pending", {})[msg_id] = entry
            time.sleep(0.5)
        notified += 1
        new_ids.append(msg_id)

    # Update state only on a real run; cap processed_ids at 2000 to avoid unbounded growth
    if not dry_run:
        updated_ids = list(processed_ids) + [i for i in new_ids if i not in processed_ids]
        state["processed_ids"] = updated_ids[-2000:]
        state_path.write_text(json.dumps(state, indent=2))

    print(f"\n{mode}Done — {'would notify' if dry_run else 'notified'} {notified}/{len(msgs)} "
          f"(total processed IDs tracked: {len(state.get('processed_ids', []))})", flush=True)


if __name__ == "__main__":
    main()
