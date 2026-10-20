#!/usr/bin/env python3
"""
Supervised-walkthrough helper for mapping OpenTable's booking wizard.

How the mapping session works (mirrors how playbypoint-golf was built):
  1. The user signs into opentable.com in the OpenClaw browser window by hand.
  2. The user (or the agent) navigates to a real restaurant page with a
     dateTime/covers query and starts an actual booking he is willing to
     complete (or cancel right after).
  3. At EACH step - restaurant page, reservation-details page, any
     card/occasion screen, the final review, the confirmation screen -
     run:  python3 map_flow.py <step-name>
     It saves a screenshot + a filtered snapshot (buttons, textboxes,
     comboboxes, headings, and any $ / charge / deposit text) to
     scripts/flowmap/<step-name>.{png,txt}
  4. Those files give the exact refs/labels to fill into the `# MAP:`
     anchors in confirm_booking.py. Then set MAPPED=True there.

Usage:
    python3 map_flow.py details
    python3 map_flow.py review
    python3 map_flow.py confirmation
"""
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
import browser_helpers as bh  # noqa: E402

CFG = json.loads((SCRIPT_DIR.parent / "config" / "restaurant_booking_config.json").read_text())
SAFETY_PATTERNS = CFG["safety"]["charge_now_patterns"] + CFG["safety"]["no_show_fee_patterns"]

OUT = SCRIPT_DIR / "flowmap"
KEEP_ROLES = ("button", "link", "textbox", "combobox", "checkbox", "radio", "heading")
INTEREST_RE = re.compile(
    r'\$|charge|deposit|prepay|prepaid|non-?refundable|cancellation|ticket|per person|policy',
    re.IGNORECASE,
)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    step = re.sub(r"[^a-z0-9_-]", "-", sys.argv[1].lower())
    OUT.mkdir(exist_ok=True)

    snap = bh.snapshot()
    bh.screenshot(str(OUT / f"{step}.png"))

    lines_out = []
    for line in snap.splitlines():
        m = bh._ELEMENT_RE.search(line)
        if m and m.group("role") in KEEP_ROLES:
            lines_out.append(line.strip())
        elif INTEREST_RE.search(line):
            lines_out.append("  # TEXT: " + line.strip())

    dest = OUT / f"{step}.txt"
    dest.write_text("\n".join(lines_out) + "\n")
    print(f"{step}: {len(lines_out)} interesting lines -> {dest}")
    print(f"screenshot -> {OUT / f'{step}.png'}")
    charge_hits = bh.scan_patterns(snap, SAFETY_PATTERNS)
    if charge_hits:
        print("  ⚠️ charge/deposit language on this step: " + ", ".join(sorted(set(charge_hits))))


if __name__ == "__main__":
    main()
