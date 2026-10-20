#!/usr/bin/env python3
"""Driving GolfNow in the user's OpenClaw browser (CDP-attached persistent Chrome).

Same mechanism as restaurant-booking and playbypoint-golf, for the same reason: the
booking half needs his login and his saved card, and OpenClaw's browser is the one
place that session lives. Two things learnt from those skills are built in from the
start:

  - THIS BROWSER IS SHARED. snapshot() reads whichever tab is active, and other skills
    leave tabs open (2026-10-11: a snapshot meant for a Example Creek tee time read the
    Maple Park page one tab over). So every operation here opens ITS OWN tab with
    `open`, works in it, and closes it; nothing is read from a tab it did not open.
  - LOGIN IS NEVER AUTOMATED. The user signs into golfnow.com once, by hand, in the OpenClaw
    browser window. logged_out() only detects the state and the caller fails loudly.

Pre-login tee-time page mapped live 2026-10-11 (flowmap/teetime_prelogin_examplecreek_*.txt):
    heading "Sat., Oct 17, 2026"  heading "3:30 PM"  heading "18"  heading "Prepaid - Online Rate"
    heading "$60.95"  paragraph "Number of Golfers"  generic "1" "2" "3" "4" (clickable)
    term "Green Fees (1 Golfer x $60.95)"  term "Total" / definition "$69.22"
    button "Continue to Book"
The checkout behind the login is NOT mapped yet; see confirm_booking.py.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

BROWSER_TIMEOUT = 120
SETTLE_SECONDS = 8


class BrowserError(RuntimeError):
    pass


def _run(*args: str, timeout: int = BROWSER_TIMEOUT) -> str:
    r = subprocess.run(["openclaw", "browser", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise BrowserError(f"openclaw browser {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def open_tab(url: str) -> str:
    """Open `url` in a NEW tab, make it active, and return its tab ref (e.g. 't43')."""
    out = _run("open", url)
    m = re.search(r"tab:\s*(\S+)", out)
    if not m:
        raise BrowserError(f"could not read the new tab's ref from: {out[:200]}")
    ref = m.group(1)
    time.sleep(SETTLE_SECONDS)
    _run("focus", ref)
    return ref


def close_tab(ref: str) -> None:
    try:
        _run("close", ref)
    except BrowserError:
        pass


def snapshot() -> str:
    return _run("snapshot")


def click(ref: str) -> None:
    _run("click", ref)


def current_url() -> str:
    try:
        out = _run("evaluate", "--fn", "return location.href")
    except BrowserError:
        return ""
    line = out.splitlines()[-1].strip() if out else ""
    try:
        v = json.loads(line)
    except ValueError:
        v = line.strip('"')
    return v if isinstance(v, str) else ""


def screenshot(path: str) -> str:
    """`openclaw browser screenshot` takes no options and prints the saved path; copy it."""
    raw = _run("screenshot").splitlines()
    out = raw[-1].strip() if raw else ""
    src = str(Path(out.split()[-1]).expanduser()) if out else ""
    if src and Path(src).exists():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, path)
        return path
    return src


# ----------------------------------------------------------------------------------
# Parsing the accessibility snapshot (pure functions; tested offline against flowmap/)
# ----------------------------------------------------------------------------------

_ELEMENT_RE = re.compile(r'(?P<role>[\w-]+)\s+"(?P<label>[^"]*)".*?\[ref=(?P<ref>[^\]\s]+)\]')
_REF_RE = re.compile(r"\[ref=([^\]\s]+)\]")


def find_ref(snap: str, label_pattern: str, role: str = None, nth: int = 0):
    """First ref whose accessible label matches (regex, case-insensitive)."""
    pattern = re.compile(label_pattern, re.IGNORECASE)
    hits = []
    for line in snap.splitlines():
        m = _ELEMENT_RE.search(line)
        if not m or (role and m.group("role") != role):
            continue
        if pattern.search(m.group("label").strip()):
            hits.append(m.group("ref"))
    return hits[nth] if nth < len(hits) else None


def headings(snap: str) -> list:
    return [m.group("label").strip() for m in
            (_ELEMENT_RE.search(l) for l in snap.splitlines()) if m and m.group("role") == "heading"]


def logged_out(snap: str, marker: str = r"^log in$") -> bool:
    """GolfNow's header shows `menuitem "Log in"` + `menuitem "Sign Up"` when signed out
    (seen 2026-10-11). Signed in, those are gone; the positive marker is still to be
    recorded on the first supervised run, so absence of "Log in" is what we have."""
    return find_ref(snap, marker, role="menuitem") is not None or find_ref(snap, marker, role="link") is not None


def logged_in(snap: str, marker: str = "/my/profile") -> bool:
    """Positive signal, recorded 2026-10-12 once the user had an account: the header carries
    `link "AB"` whose /url is /my/profile (flowmap/home_loggedin_header_*.txt). confirm
    requires this, not merely the absence of "Log in"."""
    return marker.lower() in snap.lower() and not logged_out(snap)


_DATE_RE = re.compile(r"^(?:[A-Z][a-z]{2,8}\.?,?\s+)?([A-Z][a-z]{2})\.?\s+(\d{1,2}),?\s+(\d{4})$")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*[AP]M$", re.IGNORECASE)
_MONEY_RE = re.compile(r"^\$(\d[\d,]*\.?\d*)$")


def read_tee_time_page(snap: str, selectors: dict) -> dict:
    """What the tee-time page says: {date 'YYYY-MM-DD', time '3:30 PM', price, holes, rate,
    course, golfers_selected, total, golfer_refs {'1': ref,...}, continue_ref}."""
    from datetime import datetime
    out = {"date": "", "time": "", "price": None, "holes": None, "rate": "", "course": "",
           "golfers_selected": None, "total": None, "golfer_refs": {}, "continue_ref": None}
    for h in headings(snap):
        if not out["date"]:
            m = _DATE_RE.match(h)
            if m:
                try:
                    out["date"] = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").strftime("%Y-%m-%d")
                    continue
                except ValueError:
                    pass
        if not out["time"] and _TIME_RE.match(h):
            out["time"] = h.upper().replace("  ", " ")
            continue
        if out["price"] is None:
            m = _MONEY_RE.match(h)
            if m:
                out["price"] = float(m.group(1).replace(",", ""))
                continue
        if out["holes"] is None and h.isdigit() and int(h) in (9, 18, 27, 36):
            out["holes"] = int(h)
            continue
        if not out["rate"] and re.search(r"rate|deal|twilight|prepaid", h, re.I):
            out["rate"] = h
            continue
        if not out["course"] and re.search(r"golf|club|course|links", h, re.I) and "deal" not in h.lower():
            out["course"] = h
    # Golfer count controls: the clickable generics right after "Number of Golfers".
    lines = snap.splitlines()
    for i, line in enumerate(lines):
        if re.search(selectors.get("golfers_label", "number of golfers"), line, re.I):
            for nxt in lines[i + 1: i + 12]:
                m = re.search(r'generic \[ref=([^\]]+)\][^:]*:\s*"?(\d)"?\s*$', nxt)
                if m:
                    out["golfer_refs"][m.group(2)] = m.group(1)
            break
    gf = re.search(selectors.get("green_fees_label", r"green fees \((\d) golfers? x \$([\d.]+)\)"), snap, re.I)
    if gf:
        out["golfers_selected"] = int(gf.group(1))
    tot = re.search(r'term \[ref=[^\]]+\]:\s*Total\s*\n\s*-\s*definition \[ref=[^\]]+\]:\s*\$([\d,]+\.?\d*)', snap, re.I)
    if tot:
        out["total"] = float(tot.group(1).replace(",", ""))
    out["continue_ref"] = find_ref(snap, selectors.get("continue_button", r"^continue to book$"), role="button")
    return out


def card_last4(snap: str, marker: str) -> list:
    """Every card last-4 the checkout page shows ('ending in 0000', '****0000', '•••• 0000')."""
    return sorted({m.group(2) for m in re.finditer(marker, snap, re.I)})


def money_after(snap: str, label_pattern: str):
    """The first $ amount on or after the line whose text matches label_pattern."""
    lines = snap.splitlines()
    pat = re.compile(label_pattern, re.I)
    for i, line in enumerate(lines):
        if pat.search(line):
            for nxt in lines[i: i + 4]:
                m = re.search(r"\$([\d,]+\.\d{2})", nxt)
                if m:
                    return float(m.group(1).replace(",", ""))
    return None
