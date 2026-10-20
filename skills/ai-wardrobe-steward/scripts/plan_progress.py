#!/usr/bin/env python3
"""Progress against the wardrobe plan: target set, wear ceilings, what's next.

  python3 plan_progress.py [--json] [--message]

Reads references/wardrobe-plan.md (the §2 target table and the §2a ceiling table)
and counts active closet items per plan row using the category map below. Prints
filled / open / over-ceiling rows and the next P1 gaps. --message renders the
quarterly note (wording pending the user's approval; nothing is sent from here).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _store import load_json  # noqa: E402

PLAN = ROOT / "references" / "wardrobe-plan.md"
CLOSET = ROOT / "profile" / "closet.json"

# plan row (lower-cased start of the Category cell) -> closet filter (category, optional notes/color regex)
# Order matters: the first row that claims an item owns it, so the most specific rows
# ("quarter-zips", "overshirt") must come before the general ones ("layering sweaters").
ROW_MAP = [
    ("suits", lambda i: i["category"] == "suit"),
    ("odd jackets", lambda i: i["category"] == "blazer"),
    ("dress shirts", lambda i: i["category"] == "shirt" and re.search(r"dress|formal|tailored fit", _t(i, "notes"), re.I)),
    ("casual button-ups", lambda i: i["category"] == "shirt" and not re.search(r"dress|formal", _t(i, "notes"), re.I)),
    ("layering sweaters", lambda i: i["category"] == "sweater" and re.search(r"fine|merino|crewneck|v-neck|layer", _t(i, "notes", "material"), re.I)),
    ("knit polos", lambda i: i["category"] == "polo" and re.search(r"knit|ribbed|sweater polo", _t(i, "notes", "material"), re.I)),
    ("golf / casual polos", lambda i: i["category"] == "polo"),
    ("tees", lambda i: i["category"] == "t-shirt"),
    ("heavier knits", lambda i: i["category"] == "sweater" and re.search(r"shawl|rollneck|cable|heavy|chunky", _t(i, "notes"), re.I)),
    ("quarter-zips", lambda i: i["category"] in ("sweater", "jacket") and re.search(r"quarter|1/4|half.?zip|pullover|hoodie", _t(i, "notes", "model"), re.I)),
    ("overshirt", lambda i: re.search(r"overshirt|shirt.?jacket|shacket", _t(i, "notes", "model"), re.I)),
    ("field jacket", lambda i: i["category"] == "jacket" and re.search(r"field|waxed|m-65|safari", _t(i, "notes"), re.I)),
    ("harrington", lambda i: i["category"] == "jacket" and re.search(r"harrington|blouson", _t(i, "notes"), re.I)),
    ("denim / trucker", lambda i: i["category"] == "jacket" and re.search(r"denim|trucker", _t(i, "notes", "material"), re.I)),
    ("trousers", lambda i: i["category"] == "pants"),
    ("jeans", lambda i: i["category"] == "jeans"),
    ("casual chino shorts", lambda i: i["category"] == "shorts" and not re.search(r"training|athletic|lined|running|gym", _t(i, "notes"), re.I)),
    ("loafers", lambda i: i["category"] == "loafers"),
    ("white sneakers", lambda i: i["category"] == "sneakers" and "white" in _t(i, "color").lower()),
    ("dress shoes", lambda i: i["category"] == "dress_shoes"),
    ("boots", lambda i: i["category"] == "boots"),
    ("belts", lambda i: i["category"] == "belt"),
    ("vests", lambda i: i["category"] == "vest"),
]


def _t(i: dict, *keys) -> str:
    return " ".join(str(i.get(k) or "") for k in keys)


def parse_target_table(md: str) -> list:
    rows = []
    sec = md[md.index("## 2. The finished wardrobe"):md.index("### Rotation caps")]
    for line in sec.splitlines():
        if not line.startswith("|") or line.startswith("|---") or "Category" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        m = re.search(r"\d+", cells[1])
        rows.append({"category": cells[0].strip("*"), "target": int(m.group()) if m else None, "have_plan": cells[2],
                     "action": cells[3], "priority": cells[4].strip("*")})
    return rows


def parse_ceilings(md: str) -> dict:
    out = {}
    sec = md[md.index("## 2a. Wear capacity"):md.index("## 3. Build sequence")]
    for line in sec.splitlines():
        if not line.startswith("|") or line.startswith("|---") or "Category" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        m = re.search(r"\d+", cells[3].replace("~", ""))
        if m:
            out[cells[0].lower()] = int(m.group())
    return out


def _first_number(text):
    """'~13' -> 13, '1 (glen-plaid)' -> 1, '✓' or '~0' -> None/0 as written."""
    m = re.search(r"\d+", str(text or ""))
    return int(m.group()) if m else None


def progress(md: str, closet: list) -> dict:
    active = [i for i in closet if i.get("lifecycle_state", "active") == "active"]
    # Each garment belongs to exactly ONE plan row. Until 2026-10-13 the rows were counted
    # independently, so a DryLuxe quarter-zip was a quarter-zip AND a layering sweater, and
    # the check reported "layering sweaters: done" for his single biggest gap.
    claimed, counts = set(), {}
    for key, fn in ROW_MAP:
        counts[key] = 0
        for n, i in enumerate(active):
            if n in claimed or not fn(i):
                continue
            claimed.add(n); counts[key] += 1
    rows = []
    for r in parse_target_table(md):
        key = r["category"].lower()
        match = next((k for k, _ in ROW_MAP if key.startswith(k)), None)
        have = counts.get(match) if match else None
        tgt = r["target"]
        # His stylist's own "Have" column is a considered human read of a blurry taxonomy
        # (is a DryLuxe quarter-zip a quarter-zip or a layering sweater?). It decides what
        # he is told; the computed count only flags drift worth a look. 2026-10-13: counting
        # from categories alone reported "layering sweaters: done" for his biggest gap.
        plan_have = _first_number(r.get("have_plan"))
        basis = plan_have if plan_have is not None else have
        state = "unmapped" if basis is None else ("done" if tgt is None or basis >= tgt else "open")
        if "cull" in r["priority"].lower() and basis is not None and tgt and basis > tgt:
            state = "over"
        gap = (tgt - basis) if (tgt is not None and basis is not None) else None
        rows.append({**r, "have": have, "plan_have": plan_have, "state": state, "gap": gap,
                     "drift": (abs(have - plan_have) if (have is not None and plan_have is not None) else None)})
    done = [r for r in rows if r["state"] == "done"]; open_ = [r for r in rows if r["state"] == "open"]; over = [r for r in rows if r["state"] == "over"]
    next_up = sorted(open_, key=lambda r: ({"P1": 0, "P2": 1, "P3": 2}.get(r["priority"][:2], 3), -(r["gap"] or 0)))
    drifted = [r for r in rows if (r.get("drift") or 0) >= 3]
    return {"rows": rows, "done": len(done), "open": len(open_), "over": over, "next": next_up[:4], "drifted": drifted}


def message(res: dict) -> str:
    total = res["done"] + res["open"] + len(res["over"])
    lines = [f"📋 Wardrobe plan check: {res['done']} of {total} rows filled", ""]
    if res["next"]:
        lines.append("🎯 Next gaps")
        for r in res["next"]:
            basis = r.get("plan_have") if r.get("plan_have") is not None else r.get("have")
            lines.append(f"• {r['category']}: {basis} of {r['target']} ({r['priority']})")
        lines.append("")
    # No "over the cap" section: the ceiling gates buying, not culling, and listing it in a
    # scheduled message is the nagging the user rejected on 2026-10-13 (wardrobe-plan.md §2b).
    return "\n".join(lines).rstrip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true"); ap.add_argument("--message", action="store_true")
    ap.add_argument("--send", action="store_true", help="send the quarterly message to Telegram (approved 2026-10-12)")
    ap.add_argument("--quarter-only", action="store_true", help="exit 0 silently unless today is in the first 7 days of a quarter")
    a = ap.parse_args(argv)
    if a.quarter_only:
        from datetime import date as _d
        today = _d.today()
        if today.month not in (1, 4, 7, 10) or today.day > 7:
            print("NOTHING_TO_SEND (not the start of a quarter)"); return 0
    res = progress(PLAN.read_text(), load_json(CLOSET, []))
    if a.json:
        print(json.dumps(res, indent=2)); return 0
    if a.send:
        import sys as _s
        _s.path.insert(0, str(ROOT.parent / "shared"))
        try:
            from current_location import is_away_from_home
            if is_away_from_home():
                print("HELD (The user is away)"); return 0
        except Exception:  # noqa: BLE001
            pass
        from send_telegram import send_chunk
        send_chunk(message(res))
        print("sent"); return 0
    if a.message:
        print(message(res)); return 0
    for r in res["rows"]:
        print(f"{r['state']:<9} {r['category'][:40]:<40} have {str(r['have']):>4}  target {str(r['target']):>4}  {r['priority']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
