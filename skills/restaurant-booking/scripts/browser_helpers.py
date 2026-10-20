#!/usr/bin/env python3
"""
Shared helpers for driving OpenTable via OpenClaw's browser tool.

Why the browser tool and not an API: OpenTable has no public reservation
API. Its internal dapi/fe/gql endpoint can be read unauthenticated for
availability, but booking requires a logged-in session and a multi-step
checkout wizard with anti-bot measures. OpenClaw's browser tool attaches
over CDP to a real, persistent Chrome profile ("openclaw"), so it books
exactly as the user would in his own browser. Recon 2026-08-26 confirmed
opentable.com loads with no Cloudflare challenge in this profile.

Auth model: The user logs into OpenTable ONCE, by hand, in the OpenClaw
browser window. This module never automates the login (OpenTable uses
OTP / magic-link / OAuth flows that are fragile to script and risk the
account). ensure_logged_in() only DETECTS the session and fails loudly
if it is gone, so a stale session surfaces as a clear error instead of a
silent mis-book.

Status: LIVE. Full search -> book -> cancel flow mapped in a supervised
walkthrough 2026-08-26 (Restaurant A, Dallas) and tested
end-to-end. Selectors live in config/restaurant_booking_config.json ->
"selectors"; re-map with scripts/map_flow.py if OpenTable changes its UI.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

OPENCLAW_JSON = Path("~/.openclaw/openclaw.json")
HOME_URL = "https://www.opentable.com/"
BROWSER_TIMEOUT = 120
# `openclaw browser navigate` pins its gateway wait at 20s inside the CLI itself
# (dist/browser-cli-actions-input passes timeoutMs: 2e4, which takes precedence over
# the --timeout flag), so the wait cannot be raised from here. After one of those
# timeouts the page is still loading; this is how long we let it finish before asking
# the browser where it actually ended up.
NAVIGATE_SETTLE_MS = 25000


class BrowserError(RuntimeError):
    pass


def _run(*args: str, timeout: int = BROWSER_TIMEOUT) -> str:
    r = subprocess.run(
        ["openclaw", "browser", *args], capture_output=True, text=True, timeout=timeout
    )
    if r.returncode != 0:
        raise BrowserError(
            f"openclaw browser {' '.join(args)} failed: "
            f"{(r.stderr or r.stdout).strip()}"
        )
    return r.stdout.strip()


def snapshot() -> str:
    return _run("snapshot")


def evaluate(fn: str) -> str:
    """Run a JS function against the page. Read-only unless the function says otherwise."""
    return _run("evaluate", "--fn", fn)


# The booking widget is found by its own slot links, then everything that is not an
# ancestor of them (and not a form control, and not the card-required notice) is
# removed. A blacklist of review/photo/footer selectors was tried first and was
# venue-dependent: it rescued Restaurant B and still lost Restaurant C, whose page is
# heavy in different places. Anchoring on what must be KEPT does not vary by venue.
_SLOT_ANCHOR = '[aria-label*="Reserve table at"]'

_PRUNE_JS = """() => {
  const anchors = [...document.querySelectorAll('%s')];
  if (!anchors.length) return JSON.stringify({pruned: 0, slots: 0, reason: 'no slot links'});
  // EVERY anchor, not the first: a nearly-full restaurant renders its one remaining
  // slot twice (one copy hidden), and anchoring on querySelector kept the hidden
  // copy's branch and pruned the visible one — Restaurant C read as empty with a
  // 9:30 PM table on offer.
  const keep = new Set();
  anchors.forEach(a => { for (let n = a; n; n = n.parentElement) keep.add(n); });
  const anchor = anchors[0];
  // the card-required notice lives outside the widget and read_slots looks for it
  document.querySelectorAll('form, input, select').forEach(e => {
    for (let n = e; n; n = n.parentElement) keep.add(n);
  });
  let pruned = 0;
  for (let n = anchors[anchors.length - 1]; n && n.parentElement; n = n.parentElement) {
    [...n.parentElement.children].forEach(sib => {
      if (keep.has(sib) || anchors.some(a => sib.contains(a))) return;
      if (sib.querySelector('%s')) return;
      if (/credit card|card to hold|card to book|card to guarantee/i
          .test(sib.textContent || '')) return;
      sib.remove(); pruned++;
    });
  }
  return JSON.stringify({pruned,
    slots: document.querySelectorAll('%s').length});
}""" % (_SLOT_ANCHOR, _SLOT_ANCHOR, _SLOT_ANCHOR)


SLOT_WAIT_SECONDS = 15


def wait_for_slots(timeout: int = SLOT_WAIT_SECONDS) -> int:
    """Poll until the booking widget has rendered, or the wait runs out.

    WHY (2026-10-09): the page was read after a flat `sleep(5)`, and the widget is
    client-rendered. Same restaurant, adjacent dates: 15 Oct returned five tables and
    14 Oct returned none, because on the slower load the slot links did not exist yet.
    That is indistinguishable from a full restaurant, and it fails toward "no table
    tonight" silently.

    Returns the number of slot links found; 0 after the full wait means genuinely none.
    """
    deadline = time.time() + timeout
    seen = 0
    while time.time() < deadline:
        try:
            seen = int(json.loads(evaluate(
                "() => JSON.stringify(document.querySelectorAll('%s').length)"
                % _SLOT_ANCHOR)))
        except Exception:  # noqa: BLE001
            seen = 0
        if seen:
            return seen
        time.sleep(1)
    return seen


def prune_for_snapshot() -> str:
    """Drop reviews, photo carousels and the footer so the booking widget fits.

    WHY (2026-10-09): `openclaw browser snapshot` truncates at ~40,000 characters with
    `[...TRUNCATED - page too large]`, and on a restaurant page the reviews come first
    in DOM order. Restaurant B has 309 of them, so the reservation widget fell past the
    cut and read_slots() scored a page with five open tables as having none — for every
    venue, on every date, silently. A screenshot of the same tab showed 5:45, 6:45,
    7:00, 7:15 and 7:30 PM on offer.

    The selector was never wrong: the slot links carry exactly the configured
    "Reserve table at <name> at <time> ... for a party of <n>". They were simply never
    in the text being searched.

    Nothing the booking needs is removed: the slot links' own ancestry, every form
    control and any credit-card notice are all kept, and the page is re-navigated for
    each operation so this is never cumulative. Pruning cannot cause a wrong booking -
    confirm_booking still re-verifies restaurant, date, time and party against the page
    before it clicks.
    """
    try:
        return evaluate(_PRUNE_JS)
    except BrowserError:
        return ""   # worth trying without; a full page is what it used to read


def _canon(url: str) -> str:
    """A URL stripped to scheme+host+path, for comparing where we asked to go with
    where we landed (OpenTable rewrites query strings during a load)."""
    return url.split("?")[0].split("#")[0].rstrip("/")


def _eval_url(raw: str) -> str:
    """`openclaw browser evaluate` prints its return value JSON-encoded, so a URL comes
    back wrapped in quotes."""
    text = (raw or "").strip()
    if not text:
        return ""
    line = text.splitlines()[-1].strip()
    try:
        value = json.loads(line)
    except ValueError:
        value = line.strip('"')
    return value if isinstance(value, str) else ""


def current_tab_url() -> str:
    """The URL of the tab the browser is actually acting on, or '' if it can't be read.

    Not from `tabs`: that lists every target and marks none of them current (verified in
    OpenClaw's own renderer, which prints title/url/id and no active flag), so matching
    against it can report "we arrived" while snapshot() and click() are aimed at a
    different tab. location.href is read from the one tab that matters.
    """
    try:
        return _eval_url(_run("evaluate", "--fn", "return location.href"))
    except BrowserError:
        return ""


def at_url(url: str) -> bool:
    """Is the current tab sitting at this URL, ignoring query and fragment?"""
    here = current_tab_url()
    return bool(here) and _canon(here) == _canon(url)


def navigate(url: str) -> None:
    """Navigate, treating the CLI's fixed 20s gateway wait as "stopped watching",
    not "failed".

    2026-10-06: a Restaurant B booking died on its very first step with
    `gateway timeout after 20000ms` navigating to the OpenTable home page. The page
    had loaded fine; the CLI had simply stopped waiting, and because the timeout
    surfaced as a BrowserError the whole confirm aborted. The 20s is hardcoded in
    OpenClaw's navigate command and --timeout does not move it, so the fix has to be
    in how the timeout is read.

    A transport timeout leaves the destination unknown, and an unknown is resolved by
    looking, not by assuming either way: let the load finish on the browser's own
    clock, then ask the browser which URL it is on. Nothing is re-sent, so a half-done
    navigation is never duplicated.
    """
    try:
        _run("navigate", url)
        return
    except BrowserError as exc:
        if "timeout" not in str(exc).lower():
            raise
        timed_out = exc
    _run("wait", "--time", str(NAVIGATE_SETTLE_MS),
         "--timeout-ms", str(NAVIGATE_SETTLE_MS + 15000))
    if not at_url(url):
        raise BrowserError(f"{timed_out}\nThe tab never reached {url}.")


def focus_matching_tab(url: str) -> bool:
    """Make the tab showing `url` the one snapshot() will read.

    WHY (2026-10-09): snapshot() reads whichever tab is active, and this browser is
    shared. The flight tracker leaves southwest.com open, so read_slots() was parsing
    Southwest's page while the OpenTable tab sat loaded and logged in one tab over -
    zero slots, no error, no clue. Verified from `openclaw browser tabs`.
    """
    want = _canon(url)
    try:
        listing = _run("tabs")
    except BrowserError:
        return False
    ref = None
    for line in listing.splitlines():
        if "[use:" in line:
            ref = re.search(r"\[use:\s*(\S+?)\s", line)
            ref = ref.group(1) if ref else None
        elif ref and _canon(line.strip()) == want:
            try:
                _run("focus", ref)
                return True
            except BrowserError:
                return False
    return False


def current_url(kind: str = "") -> str:
    """A URL from the open tabs. If `kind` is given (e.g. '/booking/
    confirmation'), return the last tab URL containing it; else the last
    opentable.com URL. 'last' because a fresh navigation appends."""
    out = _run("tabs")
    urls = re.findall(r"https://www\.opentable\.com/\S+", out)
    if kind:
        urls = [u for u in urls if kind in u]
    return urls[-1] if urls else ""


def confirmation_number(url: str = "") -> str:
    """Read confirmationNumber from the confirmation URL. Prefer a
    /booking/confirmation tab; fall back to any /booking/ tab."""
    src = url or current_url("/booking/confirmation") or current_url("/booking/")
    m = re.search(r"conf(?:irmation)?[Nn]umber=(\d+)", src)
    return m.group(1) if m else ""


_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])")


def norm_time(s: str) -> str:
    """Canonical 'H:MM AM/PM' from any of '5:30PM', '5:30 pm', 'at 5:30 PM'.
    Returns '' if no time is present."""
    m = _TIME_RE.search(s or "")
    if not m:
        return ""
    return f"{int(m.group(1))}:{m.group(2)} {m.group(3).upper()}"


def click(ref: str) -> None:
    _run("click", ref)


def fill(fields: list[dict]) -> None:
    _run("fill", "--fields", json.dumps(fields))


def select_option(ref: str, value: str) -> None:
    _run("select", ref, "--values", value)


def screenshot(path: str | None = None) -> str:
    """`openclaw browser screenshot` takes no args and prints the saved
    path. If `path` is given, copy the capture there and return it."""
    raw = _run("screenshot").strip().splitlines()
    out = raw[-1].strip() if raw else ""
    src = str(Path(out.split()[-1]).expanduser()) if out else ""
    if path and src and Path(src).exists():
        import shutil
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, path)
        return path
    return src


# Accessible-name element matcher: `role "Label" ... [ref=eXX]`. Elements
# without a ref (disabled/non-interactive) simply won't match, which is the
# behavior we want - "not found" == "not currently actionable".
_ELEMENT_RE = re.compile(r'(?P<role>[\w-]+)\s+"(?P<label>[^"]*)".*?\[ref=(?P<ref>[^\]\s]+)\]')


def find_ref(snap: str, label_pattern: str, role: str | None = None, nth: int = 0) -> str | None:
    """First ref whose accessible label matches label_pattern (regex,
    case-insensitive). Label is whitespace-stripped before matching."""
    pattern = re.compile(label_pattern, re.IGNORECASE)
    matches = []
    for line in snap.splitlines():
        m = _ELEMENT_RE.search(line)
        if not m:
            continue
        if role and m.group("role") != role:
            continue
        if pattern.search(m.group("label").strip()):
            matches.append(m.group("ref"))
    if nth >= len(matches):
        return None
    return matches[nth]


def find_all_refs(snap: str, label_pattern: str, role: str | None = None) -> list[tuple[str, str]]:
    """All (ref, label) pairs whose label matches. Used to enumerate the
    time-slot buttons on a restaurant page."""
    pattern = re.compile(label_pattern, re.IGNORECASE)
    out = []
    for line in snap.splitlines():
        m = _ELEMENT_RE.search(line)
        if not m:
            continue
        if role and m.group("role") != role:
            continue
        label = m.group("label").strip()
        if pattern.search(label):
            out.append((m.group("ref"), label))
    return out


# --------------------------------------------------------------------------
# Session detection
# --------------------------------------------------------------------------

def is_logged_in(snap: str) -> bool:
    """Logged-out OpenTable shows `button "Sign in"` and `button "Join
    rewards"` in the header. Logged-in shows `button "User account
    dropdown"` + `button "Upcoming reservations dropdown"` instead (recon
    2026-08-26, account "The user")."""
    low = snap.lower()
    if re.search(r'button "sign in"', low):
        return False
    return bool(
        re.search(r'button "user account dropdown"', low)
        or re.search(r'button "upcoming reservations dropdown"', low)
    )


def _logged_out(snap: str) -> bool:
    return bool(re.search(r'button "sign in"', snap.lower()))


def ensure_logged_in() -> None:
    """Navigate home and confirm a live session. The header renders a beat
    after navigation, so poll: a positive login marker returns immediately;
    an explicit 'Sign in' button fails fast; neither-yet just retries."""
    navigate(HOME_URL)
    last = ""
    for _ in range(6):
        time.sleep(2)
        last = snapshot()
        if is_logged_in(last):
            return
        if _logged_out(last):
            raise BrowserError(
                "OpenTable session is not logged in. Open the OpenClaw browser "
                "window and sign into opentable.com by hand (one time), then "
                "retry. This module never automates the OpenTable login."
            )
    raise BrowserError(
        "Could not confirm OpenTable login state (header never settled). "
        "Check the OpenClaw browser manually. Snapshot tail:\n" + last[-600:]
    )


# --------------------------------------------------------------------------
# Search + availability  (implemented against recon; verify on first run)
# --------------------------------------------------------------------------

def search_url(term: str, dt_iso: str, covers: int, metro_id: int | None = None) -> str:
    from urllib.parse import quote
    url = (
        f"https://www.opentable.com/s?term={quote(term)}"
        f"&dateTime={quote(dt_iso)}&covers={covers}"
    )
    if metro_id is not None:
        url += f"&metroid={int(metro_id)}"
    return url


# Recon 2026-08-26: each result card carries
#   link "View <NAME> restaurant details"  /url: https://www.opentable.com/<path>
# where <path> is either "<slug>" or "r/<slug>". "Restaurants you may also
# like" cards use the same shape, so callers must filter by name.
_RESULT_CARD_RE = re.compile(
    r'link "View (?P<name>.+?) restaurant details"[^\n]*\n\s*-?\s*/url:\s*'
    r'(?P<url>https://www\.opentable\.com/\S+)',
    re.IGNORECASE,
)

# OpenTable otherwise geolocates search from the browser profile/session,
# not the term. Pass config.metro_id so ambiguous names are ranked inside
# the configured metro even when the persistent browser profile was last
# used in another city.


def search_restaurants(term: str, dt_iso: str, covers: int,
                       metro_id: int | None = None) -> list[dict]:
    """Return [{name, slug, url}] from the OpenTable search results page.
    `term` should include the metro for ranking, e.g. 'Uchi Dallas'."""
    navigate(search_url(term, dt_iso, covers, metro_id))
    time.sleep(5)
    snap = snapshot()
    results = []
    seen = set()
    for m in _RESULT_CARD_RE.finditer(snap):
        url = m.group("url").split("?")[0].rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        slug = url.split("opentable.com/")[-1]
        if slug.startswith("r/"):
            slug = slug[2:]
        results.append({"name": m.group("name").strip(), "slug": slug, "url": url})
    return results


def restaurant_url(slug_or_url: str, dt_iso: str, covers: int) -> str:
    from urllib.parse import quote
    if slug_or_url.startswith("http"):
        base = slug_or_url.split("?")[0]
    else:
        s = slug_or_url.strip("/")
        base = f"https://www.opentable.com/{s if s.startswith('r/') else 'r/' + s}"
    return f"{base}?dateTime={quote(dt_iso)}&covers={covers}"


# Time-slot buttons on a restaurant page render their time as the accessible
# label, e.g. `button "7:00 PM"` / `button "Table for 2 at 7:00 PM"`.
_SLOT_LABEL_RE = r'\b\d{1,2}:\d{2}\s*[AP]M\b'
_SLOT_TIME_RE = re.compile(r'(\d{1,2}:\d{2}\s*[AP]M)', re.IGNORECASE)


def read_slots(slug_or_url: str, dt_iso: str, covers: int,
                slot_pattern: str = r"reserve table at") -> dict:
    """Load a restaurant page and read its bookable time slots.
    Returns {"slots": [{"time": "7:00 PM", "ref": "eNN", "label": ...}],
             "card_required": bool, "snapshot_tail": str}.
    Slot buttons render as `button "7:00 PM Reserve table at <name>
    restaurant"` (recon 2026-08-26). First ref per distinct time wins.

    `slot_pattern` defaults to that recon pattern but should be passed as
    config/restaurant_booking_config.json's selectors.slot_button — the same
    value verify_booking_terms() uses — so a re-map via map_flow.py (after
    OpenTable changes its wording) fixes slot-reading here too instead of
    only the live re-verification step.
    """
    url = restaurant_url(slug_or_url, dt_iso, covers)
    navigate(url)
    time.sleep(5)
    focus_matching_tab(url)     # this browser is shared; read OUR tab
    wait_for_slots()            # client-rendered; a flat sleep raced it
    prune_for_snapshot()        # or the widget falls past the snapshot's size limit
    snap = snapshot()
    low = snap.lower()

    slots = []
    seen_times = set()
    for ref, label in find_all_refs(snap, slot_pattern, role="button"):
        norm = norm_time(label)
        if not norm or norm in seen_times:
            continue
        seen_times.add(norm)
        slots.append({"time": norm, "ref": ref, "label": label})

    card_required = bool(
        re.search(r"credit card (is )?required|card to (hold|book|guarantee)", low)
    )
    return {"slots": slots, "card_required": card_required,
            "snapshot_tail": snap[-2000:]}


# --------------------------------------------------------------------------
# Charge / deposit safety scan  (used by confirm_booking.py)
# --------------------------------------------------------------------------

def pick_seating(options: list[tuple[str, str]], prefer=(), avoid=()) -> tuple[str, str] | None:
    """Which seating option to take when a restaurant offers more than one.

    2026-10-06: Restaurant B's patio got booked because OpenTable listed it first and
    find_ref() took the first match. Nothing was choosing - there was nothing to choose
    with. The user's default is indoors.

    `prefer` is ordered best-first and matched against the option's accessible label.
    Anything matching `avoid` is set aside and used only when it is all that is on
    offer, so a patio-only restaurant still books. Returns (ref, label), or None when
    there is nothing to pick from (a restaurant with a single implicit seating shows
    no options at all).
    """
    if not options:
        return None
    avoid_re = re.compile("|".join(avoid), re.IGNORECASE) if avoid else None
    pool = [o for o in options if not (avoid_re and avoid_re.search(o[1]))] or options
    for want in prefer:
        wanted = re.compile(want, re.IGNORECASE)
        for option in pool:
            if wanted.search(option[1]):
                return option
    return pool[0]


def scan_patterns(snap: str, patterns: list[str]) -> list[str]:
    """Return the matched phrases for any pattern that hits the snapshot."""
    low = snap.lower()
    hits = []
    for p in patterns:
        m = re.search(p, low)
        if m:
            hits.append(m.group(0))
    return hits


def dialog_subtree(snap: str) -> str:
    """Return just the lines belonging to the first `dialog "..."` in the
    snapshot (by indentation), so scans don't pick up page boilerplate."""
    lines = snap.splitlines()
    for i, ln in enumerate(lines):
        if re.search(r'dialog "', ln):
            indent = len(ln) - len(ln.lstrip())
            out = [ln]
            for nxt in lines[i + 1:]:
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                out.append(nxt)
            return "\n".join(out)
    return ""


def card_required(snap: str) -> bool:
    return bool(re.search(r'heading "credit card required"', snap.lower())
               or "requires a credit card to secure" in snap.lower())


# "$35.00 per person", "$25 per person", "$35 per guest". The cents are the point: the
# original `charge of \$(\d+) per person` matched Restaurant A's "$25 per person" and silently
# missed Restaurant B's "charge of $35.00 per person" on 2026-10-06, so the proposal told
# The user "a fee" where it meant "$35/person". Both halves stay anchored to charge/fee
# wording so a prix-fixe menu price can never be read as a penalty.
_FEE_RE = re.compile(
    r"(?:charge|fee)\s+of\s+\$(\d+(?:\.\d{1,2})?)\s+per\s+(?:person|guest)"
    r"|\$(\d+(?:\.\d{1,2})?)\s+per\s+(?:person|guest)\s+(?:charge|fee)",
    re.IGNORECASE,
)

# WHEN the fee bites is restaurant-specific. Restaurant A's card hold was "less than 1 day in
# advance"; Restaurant B's is "after 12:00 pm on the day of the reservation". The proposal
# used to assert Restaurant A's wording for every restaurant, so on 2026-10-06 it told the user a
# Restaurant B cancellation stayed free until a day out when it was really free only until
# noon that day. Quote the restaurant, or say nothing.
_FEE_WHEN_RE = re.compile(
    r"no-?shows?\s+(?:or|and|&)\s+cancellations?\s+"
    r"(?P<when>(?:after|less than|within|prior to|more than)[^.]{0,80}?)"
    r"\s+(?:will be|are)\s+subject to",
    re.IGNORECASE,
)


def fee_per_person(snap: str) -> float | None:
    """The no-show / late-cancel penalty per head, or None if the page states none."""
    m = _FEE_RE.search(snap)
    if not m:
        return None
    return float(m.group(1) or m.group(2))


def fee_text(amount: float | int | None) -> str:
    """'$35' / '$35.50'. A trailing '.00' reads like a typo in a one-line alert."""
    if amount is None:
        return ""
    value = float(amount)
    return f"${int(value)}" if value.is_integer() else f"${value:.2f}"


def fee_condition(snap: str) -> str:
    """The restaurant's own words for when the fee applies, or '' if it doesn't say."""
    m = _FEE_WHEN_RE.search(snap)
    return re.sub(r"\s+", " ", m.group("when")).strip() if m else ""


# --------------------------------------------------------------------------
# Checkboxes
# --------------------------------------------------------------------------
# OpenTable renders an agreement checkbox as a visually-hidden <input> inside a styled
# label, so its snapshot line carries NO ref of its own - the clickable ref belongs to
# the generic wrapping it:
#
#     - generic [ref=e149]:
#       - checkbox "I agree to the restaurant's terms and conditions"
#       - img [ref=e151]:
#
# find_ref() only matches lines that carry a ref, so it never sees these at all. That is
# why confirm_booking.py walked straight past Restaurant B's required agreement on
# 2026-10-06 and clicked a Complete button the form then refused.
_CHECKBOX_RE = re.compile(r'^(?P<indent>\s*)-\s+checkbox\s+"(?P<label>[^"]*)"(?P<rest>.*)$')
_REF_RE = re.compile(r"\[ref=([^\]\s]+)\]")


def checkboxes(snap: str) -> list[dict]:
    """Every checkbox as {label, checked, ref}. `ref` is the checkbox's own when it has
    one, else its nearest ref-bearing ancestor - the element a click has to land on."""
    lines = snap.splitlines()
    found = []
    for i, line in enumerate(lines):
        m = _CHECKBOX_RE.match(line)
        if not m:
            continue
        own = _REF_RE.search(m.group("rest"))
        ref = own.group(1) if own else None
        if ref is None:
            depth = len(m.group("indent"))
            for prev in reversed(lines[:i]):
                if not prev.strip():
                    continue
                prev_depth = len(prev) - len(prev.lstrip())
                if prev_depth >= depth:
                    continue
                depth = prev_depth
                ancestor = _REF_RE.search(prev)
                if ancestor:
                    ref = ancestor.group(1)
                    break
        found.append({"label": m.group("label").strip(),
                      "checked": "[checked]" in m.group("rest"),
                      "ref": ref})
    return found


def required_agreements(snap: str, agree_pattern: str, exclude_pattern: str = "") -> list[dict]:
    """Unticked boxes the form will not submit without.

    `exclude_pattern` is the guard that keeps marketing opt-ins out ("Sign me up to
    receive dining offers", "Collect 100 points"). Ticking a box as the user is only ever
    acceptable for a term the reservation cannot proceed without; an exclusion always
    beats a match.
    """
    agree = re.compile(agree_pattern, re.IGNORECASE)
    skip = re.compile(exclude_pattern, re.IGNORECASE) if exclude_pattern else None
    return [c for c in checkboxes(snap)
            if not c["checked"]
            and agree.search(c["label"])
            and not (skip and skip.search(c["label"]))]


_ALERT_RE = re.compile(r'^\s*-\s+alert\b[^:]*:\s*(?P<text>.+)$')


def alerts(snap: str) -> list[str]:
    """Every alert the page is currently showing, in order."""
    matches = (_ALERT_RE.match(line) for line in snap.splitlines())
    return [m.group("text").strip() for m in matches if m]


def submit_refused(snap: str, marker: str) -> list[str]:
    """The alerts by which OpenTable states it refused the submit. When these are
    present the answer to "did it book?" is a plain no, not an unknown - 2026-10-06 the
    page said "Cannot complete reservation. Please fix the errors in the form." and the user
    was still told to go check OpenTable by hand."""
    pattern = re.compile(marker, re.IGNORECASE)
    return [a for a in alerts(snap) if pattern.search(a)]


def open_reservations_dropdown() -> str:
    """Open the header 'Upcoming reservations' dropdown and return the
    snapshot. Mapped 2026-08-26: each entry is
      link "<Restaurant>"  (/url /r/<slug>)
      text "<H:MM AM/PM>"   text "<Mon DD, YYYY>"
      link "View" / "Modify" / "Invite guests" / "Cancel"
    """
    navigate(HOME_URL)
    time.sleep(4)
    snap = snapshot()
    btn = find_ref(snap, r"upcoming reservations dropdown", role="button")
    if not btn:
        raise BrowserError("No 'Upcoming reservations' control in the header "
                           "(not logged in, or layout changed).")
    click(btn)
    time.sleep(2)
    return snapshot()


def reservation_blocks(snap: str) -> list[dict]:
    """Split the open dropdown into per-reservation blocks. Returns
    [{name, when_text, cancel_ref, view_ref}]."""
    # Scope to the dropdown's "Upcoming reservations" section, which ends
    # at "View all reservations" - beyond it is the recommendations
    # carousel, whose cards also link to /r/.
    lo = snap.find("Upcoming reservations")
    hi = snap.find("View all reservations")
    if lo != -1:
        snap = snap[lo: hi if hi != -1 else len(snap)]
    lines = snap.splitlines()
    # indices of reservation-title links pointing at /r/
    starts = []
    for i, ln in enumerate(lines):
        m = _ELEMENT_RE.search(ln)
        if m and m.group("role") == "link":
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if "/url:" in nxt and "/r/" in nxt:
                starts.append((i, m.group("label").strip(), m.group("ref")))
    blocks = []
    for idx, (i, name, _ref) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[i:end])
        when = ""
        wm = re.search(r'text:\s*([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})', chunk)
        tm = re.search(r'text:\s*(\d{1,2}:\d{2}\s*[AP]M)', chunk)
        when = " ".join(x.group(1) for x in (tm, wm) if x)
        blocks.append({
            "name": name,
            "when_text": when,
            "cancel_ref": find_ref(chunk, r"^cancel$", role="link"),
            "view_ref": find_ref(chunk, r"^view$", role="link"),
        })
    return blocks


def verify_booking_terms(slug_or_url: str, dt_iso: str, covers: int, time_label: str,
                         selectors: dict, charge_now_patterns: list[str] | None = None,
                         seating: dict | None = None) -> dict:
    """Click through from the restaurant page to the reservation-details
    page for the given slot, read the authoritative terms (the restaurant
    page itself does NOT surface card-required - only the booking flow
    does), then navigate away without booking. OpenTable does not hold the
    table until 'Complete reservation', so backing out here is harmless.

    Returns {"reached": bool, "card_required": bool, "fee_per_person": float|None,
             "fee_condition": str, "charge_now": [str], "seating": str, "note": str}.
    """
    want = norm_time(time_label)
    if not want:
        return {"reached": False, "card_required": False, "fee_per_person": None,
                "fee_condition": "", "charge_now": [], "seating": "",
                "note": f"unparseable time {time_label!r}"}
    url = restaurant_url(slug_or_url, dt_iso, covers)
    navigate(url)
    time.sleep(5)
    focus_matching_tab(url)     # this browser is shared; read OUR tab
    wait_for_slots()            # client-rendered; a flat sleep raced it
    prune_for_snapshot()        # or the widget falls past the snapshot's size limit
    snap = snapshot()
    slot_ref = None
    for ref, label in find_all_refs(snap, selectors["slot_button"], role="button"):
        if norm_time(label) == want:
            slot_ref = ref
            break
    if not slot_ref:
        return {"reached": False, "card_required": False, "fee_per_person": None,
                "fee_condition": "", "charge_now": [], "seating": "",
                "note": "slot not found"}
    click(slot_ref)
    time.sleep(4)
    snap = snapshot()
    seat = pick_seating(find_all_refs(snap, selectors["seating_option_button"], role="button"),
                        (seating or {}).get("prefer", ()), (seating or {}).get("avoid", ()))
    if seat:
        click(seat[0])
        time.sleep(4)
        snap = snapshot()

    reached = bool(re.search(selectors["details_page_marker"], snap.lower())
                   or find_ref(snap, selectors["complete_button"], role="button"))
    result = {
        "reached": reached,
        "card_required": card_required(snap),
        "fee_per_person": fee_per_person(snap),
        "fee_condition": fee_condition(snap),
        "charge_now": scan_patterns(snap, charge_now_patterns or []),
        "seating": seat[1] if seat else "",
        "note": "" if reached else "did not reach details page",
    }
    # Back out of the booking flow onto the restaurant page (lighter than
    # the homepage, and a valid URL). Best-effort - OpenTable does not hold
    # the table until "Complete reservation", so a failed nav is harmless.
    try:
        navigate(restaurant_url(slug_or_url, dt_iso, covers))
    except BrowserError:
        pass
    return result
