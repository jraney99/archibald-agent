#!/usr/bin/env python3
"""
Loop closer — forces explicit resolution of critical (🚨) items in
MEMORY.md's "## Pending Actions" section instead of letting them get
silently re-stated or moved to "Archived/Stale" without ever being closed.

Why this exists: an enrollment-deadline item sat flagged
"🚨 CRITICAL/OVERDUE" in MEMORY.md from June through September 2026,
re-stated across multiple "Last updated" passes, never confirmed done
or not-done. That's the failure mode this closes.

State: ~/.openclaw/loop_closer_state.json
Source: ~/.openclaw/workspace/MEMORY.md ("## Pending Actions" section)

Commands:
  scan                          Sync state from MEMORY.md's 🚨 bullets. Prints JSON.
  escalate                      Print JSON list of items due for a ping today.
  mark-pinged --id ID           Record that an escalation message was sent today.
  close --id ID --resolution {done,not-doing} [--note TEXT]
                                 Remove the bullet from Pending Actions, log it
                                 under "## Resolved (Loop Closer)", drop from state.
                                 Prints a plain confirmation line (no trailing hat —
                                 the callback-dispatch plugin adds it).
  keep-open --id ID [--note TEXT]
                                 Leave the bullet in place, append an inline
                                 "confirmed still open" marker, snooze pinging for
                                 3 days, bump the ping count. Prints a plain
                                 confirmation line.

Only 🚨-marked bullets are managed here. Plain pending items are untouched —
this skill is for the ones already important enough to be flagged critical.
"""
import os
import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from pending_proposal import edit_or_fallback  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "to-do" / "scripts"))
import todo as commitments_store  # noqa: E402

MEMORY_PATH = Path(os.path.expanduser("~/.openclaw/workspace/MEMORY.md")).expanduser()
STATE_PATH = Path(os.path.expanduser("~/.openclaw/loop_closer_state.json")).expanduser()

SECTION_HEADER = "## Pending Actions"
RESOLVED_HEADER = "## Resolved (Loop Closer)"
BULLET_RE = re.compile(r"^- \*\*(🚨[^:]*:)\*\*\s*(.+)$")
SNOOZE_DAYS = 3
STILL_OPEN_TAG_RE = re.compile(r"\s*_\(confirmed still open [\d-]+\)_\s*$")


# ── State I/O ────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 1, "items": {}}
    return json.loads(STATE_PATH.read_text())


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _item_id(text: str) -> str:
    return hashlib.md5(text.strip().encode()).hexdigest()[:8]


# ── MEMORY.md parsing ────────────────────────────────────────────────────────

def _read_memory() -> list:
    if not MEMORY_PATH.exists():
        sys.exit(f"MEMORY.md not found at {MEMORY_PATH}")
    return MEMORY_PATH.read_text().splitlines()


def _write_memory(lines: list) -> None:
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    MEMORY_PATH.write_text(text)


def _section_bounds(lines: list, header: str):
    """Return (start, end) exclusive line indices for the section body, or
    (None, None) if the header doesn't exist."""
    try:
        start = lines.index(header) + 1
    except ValueError:
        return None, None
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## ") or lines[i].strip() == "---":
            end = i
            break
    return start, end


def _pending_bullets() -> list:
    """Return [(line_index, full_line, bullet_body)] for 🚨 bullets in
    Pending Actions."""
    lines = _read_memory()
    start, end = _section_bounds(lines, SECTION_HEADER)
    if start is None:
        return []
    out = []
    for i in range(start, end):
        m = BULLET_RE.match(lines[i])
        if m:
            body = STILL_OPEN_TAG_RE.sub("", m.group(2)).strip()
            out.append((i, lines[i], body))
    return out


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_scan() -> dict:
    """Sync state with current 🚨 bullets: add new ones, drop ones no longer
    present (closed by hand or otherwise removed from the file)."""
    state = _load_state()
    items = state.setdefault("items", {})
    today = date.today().isoformat()
    current_ids = set()
    new_ids = set()

    for _, full_line, body in _pending_bullets():
        commitments_store.ensure_memory_commitment(full_line[2:].strip())
        item_id = _item_id(body)
        current_ids.add(item_id)
        if item_id not in items:
            new_ids.add(item_id)
            # Bullets already worded OVERDUE/CRITICAL are treated as already
            # 10 days old so they escalate hard on the very first scan
            # instead of getting a fresh 3-day grace period. Checked against
            # the whole line (label included) since "OVERDUE" usually lives
            # in the bold label, e.g. "**🚨 CRITICAL/OVERDUE:**", not the body.
            backdate = 10 if "overdue" in full_line.lower() else 0
            first_flagged = (date.today() - timedelta(days=backdate)).isoformat()
            items[item_id] = {
                "text": body,
                "first_flagged": first_flagged,
                "last_pinged": None,
                "snooze_until": None,
                "ping_count": 0,
                "status": "open",
            }

    # Prune items whose bullet is gone from the file (closed by hand, or by
    # the `close` command already updating both file and state).
    removed = [i for i in items if i not in current_ids]
    for i in removed:
        del items[i]

    _save_state(state)
    return {"scanned": len(current_ids), "new": len(new_ids), "removed": len(removed), "as_of": today}


def cmd_escalate() -> list:
    """Return items due for a ping today: open 3+ days (or already worded
    overdue), not snoozed, not already pinged today."""
    cmd_scan()
    state = _load_state()
    today = date.today()
    due = []
    for item_id, item in state["items"].items():
        if item.get("status") != "open":
            continue
        if item.get("last_pinged") == today.isoformat():
            continue
        snooze = item.get("snooze_until")
        if snooze and date.fromisoformat(snooze) >= today:
            continue
        days_open = (today - date.fromisoformat(item["first_flagged"])).days
        if days_open < 3:
            continue
        if days_open >= 14:
            tier = 3
        elif days_open >= 7:
            tier = 2
        else:
            tier = 1
        due.append({
            "id": item_id,
            "text": item["text"],
            "days_open": days_open,
            "tier": tier,
            "ping_count": item.get("ping_count", 0),
        })
    due.sort(key=lambda x: -x["days_open"])
    return due


def cmd_mark_pinged(item_id: str, msg_id=None) -> dict:
    state = _load_state()
    item = state["items"].get(item_id)
    if not item:
        sys.exit(f"No open item with id '{item_id}'.")
    today = date.today().isoformat()
    item["last_pinged"] = today
    item["ping_count"] = item.get("ping_count", 0) + 1
    if msg_id:
        item["source_tg_msg_id"] = msg_id
    _save_state(state)
    return {"id": item_id, "last_pinged": today, "ping_count": item["ping_count"]}


def cmd_close(item_id: str, resolution: str, note: str) -> str:
    state = _load_state()
    item = state["items"].get(item_id)
    if not item:
        return "No matching open item found — it may already be closed."
    msg_id = item.get("source_tg_msg_id")

    lines = _read_memory()
    p_start, p_end = _section_bounds(lines, SECTION_HEADER)
    target_idx = None
    if p_start is not None:
        for i in range(p_start, p_end):
            m = BULLET_RE.match(lines[i])
            if m and _item_id(STILL_OPEN_TAG_RE.sub("", m.group(2)).strip()) == item_id:
                target_idx = i
                break

    today = date.today().isoformat()
    if target_idx is not None:
        # Close the canonical commitment before removing its MEMORY.md view.
        # A database failure leaves the visible bullet intact for retry.
        memory_title = lines[target_idx][2:].strip()
        commitments_store.ensure_memory_commitment(memory_title)
        commitment_status = "completed" if resolution == "done" else "cancelled"
        evidence = f"User resolved through Loop Closer on {today}: {resolution}"
        if note:
            evidence += f"; {note}"
        commitments_store.update_by_source_key(
            commitments_store.memory_source_key(memory_title),
            status=commitment_status,
            evidence=evidence,
        )
        del lines[target_idx]

    resolved_line = f"- ~~{item['text']}~~ — closed {today} ({resolution}"
    resolved_line += f", note: {note})" if note else ")"

    r_start, r_end = _section_bounds(lines, RESOLVED_HEADER)
    if r_start is None:
        # Create the section at the end of the file.
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(RESOLVED_HEADER)
        lines.append(resolved_line)
    else:
        lines.insert(r_end, resolved_line)

    _write_memory(lines)

    del state["items"][item_id]
    _save_state(state)

    return edit_or_fallback(msg_id, f"✅ Closed ({resolution}): {item['text'][:80]}")


def cmd_keep_open(item_id: str, note: str) -> str:
    state = _load_state()
    item = state["items"].get(item_id)
    if not item:
        return "No matching open item found."
    msg_id = item.get("source_tg_msg_id")

    today = date.today()
    item["snooze_until"] = (today + timedelta(days=SNOOZE_DAYS)).isoformat()
    item["ping_count"] = item.get("ping_count", 0) + 1
    _save_state(state)

    lines = _read_memory()
    p_start, p_end = _section_bounds(lines, SECTION_HEADER)
    if p_start is not None:
        for i in range(p_start, p_end):
            m = BULLET_RE.match(lines[i])
            if m and _item_id(STILL_OPEN_TAG_RE.sub("", m.group(2)).strip()) == item_id:
                base = STILL_OPEN_TAG_RE.sub("", lines[i]).rstrip()
                lines[i] = f"{base} _(confirmed still open {today.isoformat()})_"
                break
    _write_memory(lines)

    days_open = (today - date.fromisoformat(item["first_flagged"])).days
    return edit_or_fallback(
        msg_id, f"🔴 Noted — still open ({days_open}d). Will ask again in {SNOOZE_DAYS} days.")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan")
    sub.add_parser("escalate")

    mp = sub.add_parser("mark-pinged")
    mp.add_argument("--id", required=True)

    cl = sub.add_parser("close")
    cl.add_argument("--id", required=True)
    cl.add_argument("--resolution", required=True, choices=["done", "not-doing"])
    cl.add_argument("--note", default="")

    ko = sub.add_parser("keep-open")
    ko.add_argument("--id", required=True)
    ko.add_argument("--note", default="")

    args = p.parse_args()

    if args.cmd == "scan":
        print(json.dumps(cmd_scan()))
    elif args.cmd == "escalate":
        print(json.dumps(cmd_escalate()))
    elif args.cmd == "mark-pinged":
        print(json.dumps(cmd_mark_pinged(args.id)))
    elif args.cmd == "close":
        print(cmd_close(args.id, args.resolution, args.note))
    elif args.cmd == "keep-open":
        print(cmd_keep_open(args.id, args.note))


if __name__ == "__main__":
    main()
