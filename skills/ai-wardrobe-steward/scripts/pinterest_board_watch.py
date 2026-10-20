#!/usr/bin/env python3
"""Import new pins from the user's public Pinterest boards as wardrobe inspiration.

  python3 pinterest_board_watch.py [--dry-run] [--json]

Reads each enabled board in config/pinterest_boards.json through its public .rss
feed (undocumented but long-standing; newest ~25 pins, public boards only), saves
each new pin's full-size image to profile/photos/inspiration/pinterest-<pin id>.jpg
and records it in data/inspiration_pins.json (pin id, board, source link, pinned
date, image hash). Two pins of the same image share one file. Silent: it never
messages anyone and never calls a model; describing pins and proposing what they say
about his taste is a separate, approved step. Feed failures are reported and leave
the record untouched; a feed that stops working is a reason to move to the API.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json, save_json  # noqa: E402

BOARDS_PATH = data_path("pinterest_boards.json")
STATE_PATH = data_path("pinterest_board_watch.json")
PINS_PATH = data_path("inspiration_pins.json")
INSPIRATION_DIR = ROOT / "profile" / "photos" / "inspiration"
FEED_FETCHES = 4        # Pinterest serves a different ~25-pin slice of the board per request; merging a few
FEED_PAUSE_S = 2        # fetches reaches older pins too (3 fetches showed 46 distinct pins on 2026-10-12)
# 2026-10-14: the board held 149 pins and 100 were imported. The feed ignores every parameter
# (page, limit, offset, count all return the same 26 items) and the board page embeds FEWER
# pins than the feed does, so there is no way to ask for more at once. What does change is
# WHICH slice is served, and it changes over hours, not seconds — 6 fetches two seconds apart
# saw 34 distinct pins, while fetches a day apart had seen 46. Hence running often, not harder.
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"


def feed_url(board_url: str) -> str:
    return board_url.rstrip("/") + ".rss"


def fetch_bytes(url: str) -> bytes:
    curl = shutil.which("curl") or "/usr/bin/curl"
    r = subprocess.run([curl, "-sSL", "--max-time", "30", "-A", UA, "-w", "\n%{http_code}", url], capture_output=True)
    body, _, code = r.stdout.rpartition(b"\n")
    if r.returncode != 0 or code != b"200":
        raise RuntimeError(f"{url}: HTTP {code.decode() or r.returncode} {r.stderr.decode()[:80]}")
    return body


def parse_feed(xml_bytes: bytes) -> list:
    """Pins in a board feed: [{pin_id, link, title, pinned_at, image}] newest first."""
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        m = re.search(r"/pin/(\d+)/?$", link)   # share links like /pin/8rSnAwWm/ carry no image and are skipped
        if not m:
            continue
        desc = html.unescape(it.findtext("description") or "")
        img = re.search(r'src="([^"]+)"', desc)
        image = re.sub(r"/\d+x/", "/originals/", img.group(1)) if img else ""
        out.append({"pin_id": m.group(1), "link": link, "title": (it.findtext("title") or "").strip(),
                    "pinned_at": (it.findtext("pubDate") or "").strip(), "image": image})
    return out


def import_boards(boards: list, state: dict, pins: list, fetch=fetch_bytes, dry_run: bool = False) -> dict:
    seen = set(state.get("seen", []))
    by_hash = {p["image_sha256"]: p for p in pins if p.get("image_sha256")}
    report = {"boards": [], "new": [], "errors": []}
    for b in boards:
        if not b.get("enabled", True):
            continue
        items, got = [], set()
        try:
            for n in range(FEED_FETCHES):
                if n:
                    time.sleep(FEED_PAUSE_S)
                for i in parse_feed(fetch(feed_url(b["url"]))):
                    if i["pin_id"] not in got:
                        got.add(i["pin_id"]); items.append(i)
        except (RuntimeError, ET.ParseError) as exc:
            if not items:
                report["errors"].append({"board": b["id"], "error": str(exc)[:160]})
                continue
        # a pin whose image never downloaded is retried on the next run
        done = {p["pin_id"] for p in pins if p.get("file")}
        fresh = [i for i in items if i["pin_id"] not in seen or i["pin_id"] not in done]
        pins[:] = [p for p in pins if p.get("file") or p["pin_id"] not in {f["pin_id"] for f in fresh}]
        report["boards"].append({"board": b["id"], "in_feed": len(items), "new": len(fresh)})
        for i in fresh:
            rec = {"pin_id": i["pin_id"], "board": b["id"], "link": i["link"], "title": i["title"],
                   "pinned_at": i["pinned_at"], "source_image": i["image"], "file": None, "image_sha256": None,
                   "imported_at": datetime.now(timezone.utc).isoformat(), "description": None, "duplicate_of": None}
            if not dry_run and i["image"]:
                data = b""
                # some pins have no /originals/ copy (403); fall back to the 736px rendition
                for url in (i["image"], i["image"].replace("/originals/", "/736x/")):
                    try:
                        data = fetch(url)
                        break
                    except RuntimeError as exc:
                        err = str(exc)[:120]
                if not data:
                    report["errors"].append({"pin": i["pin_id"], "error": err})
                if data:
                    digest = hashlib.sha256(data).hexdigest()
                    rec["image_sha256"] = digest
                    if digest in by_hash:
                        rec["file"] = by_hash[digest]["file"]
                        rec["duplicate_of"] = by_hash[digest]["pin_id"]
                    else:
                        INSPIRATION_DIR.mkdir(parents=True, exist_ok=True)
                        f = INSPIRATION_DIR / f"pinterest-{i['pin_id']}.jpg"
                        f.write_bytes(data)
                        rec["file"] = f"profile/photos/inspiration/{f.name}"
                        by_hash[digest] = rec
            report["new"].append(rec)
            if not dry_run:
                pins.append(rec)
                seen.add(i["pin_id"])
    if not dry_run:
        state["seen"] = sorted(seen)
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        state["last_errors"] = report["errors"]
    return report


def board_pin_count(board_url: str, fetch=None) -> int:
    """How many pins the board page says it holds, so coverage can be reported honestly."""
    import re as _re
    try:
        html = (fetch or fetch_bytes)(board_url).decode("utf-8", "replace")
        m = _re.search(r'"pin_count":(\d+)', html)
        return int(m.group(1)) if m else 0
    except Exception:  # noqa: BLE001
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list what would be imported; write nothing")
    ap.add_argument("--coverage", action="store_true", help="how many of the board's pins have been imported")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    boards = load_json(BOARDS_PATH, {}).get("boards", [])
    state = load_json(STATE_PATH, {})
    pins = load_json(PINS_PATH, [])
    if a.coverage:
        rows = []
        for b in boards:
            total = board_pin_count(b["url"])
            mine = sum(1 for p in pins if p.get("board") == b["id"])
            rows.append({"board": b["id"], "on_the_board": total, "imported": mine,
                         "missing": max(0, total - mine),
                         "note": "the feed shows a rotating ~25; running often is the only way to reach more"})
        print(json.dumps(rows, indent=2) if a.json else
              "\n".join(f"{r['board']}: {r['imported']} of {r['on_the_board']} imported, {r['missing']} not yet seen" for r in rows))
        return 0
    report = import_boards(boards, state, pins, dry_run=a.dry_run)
    if not a.dry_run:
        save_json(PINS_PATH, pins)
        save_json(STATE_PATH, state)
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        for b in report["boards"]:
            print(f"{b['board']}: {b['in_feed']} pins in feed, {b['new']} new")
        for r in report["new"]:
            dup = f" (same image as pin {r['duplicate_of']})" if r.get("duplicate_of") else ""
            print(f"  + {r['pin_id']} {r['title'] or '(untitled)'} -> {r['file'] or 'not downloaded'}{dup}")
        for e in report["errors"]:
            print(f"  ! {e}")
    return 1 if report["errors"] and not report["boards"] else 0


if __name__ == "__main__":
    sys.exit(main())
