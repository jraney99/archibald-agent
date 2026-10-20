#!/usr/bin/env python3
"""
Demo: simulates a real email going through the triage pipeline.
Uses the live Claude classifier and notification formatter — no real Gmail touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from email_monitor import classify, format_notification

FAKE_SENDER  = "Sarah Mitchell <contact@firm.example>"
FAKE_SUBJECT = "Introduction — potential partnership opportunity"
FAKE_BODY    = """\
Hi,

Got your name from James Whitfield — he thought there might be a good fit between \
what you're building and what we're looking for on the distribution side.

We're a mid-market private equity firm focused on industrial services. We've been \
evaluating a few operators in your space and your name keeps coming up.

Would love to set up a quick 20-minute intro call. Are you available Thursday or \
Friday afternoon this week?

Best,
Sarah Mitchell
Managing Director, Clearwater Equity
"""
FAKE_MSG_ID  = "demo_abc123"

print("Running email through classifier...\n")

result = classify(FAKE_SENDER, FAKE_SUBJECT, FAKE_BODY)

print(f"Tier:            {result['tier']}")
print(f"Meeting type:    {result['meeting_request']}")
print(f"Summary:         {result['summary']}")
print(f"Reason:          {result['reason']}")
print(f"\nDraft reply:\n{result['draft']}")
print("\n" + "─" * 60)
print("TELEGRAM NOTIFICATION PREVIEW:")
print("─" * 60)

notification = format_notification(
    tier    = result["tier"],
    sender  = FAKE_SENDER,
    subject = FAKE_SUBJECT,
    body    = FAKE_BODY,
    summary = result["summary"],
    reason  = result["reason"],
    draft   = result["draft"],
    msg_id  = FAKE_MSG_ID,
)

# Strip HTML tags for terminal readability
import re, html as html_mod
plain = re.sub(r'<[^>]+>', '', notification)
plain = html_mod.unescape(plain)
print(plain)
