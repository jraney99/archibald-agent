#!/usr/bin/env python3
"""Record the user's typed answer to "what's off?" after a 👌 / 👎 fit tap.

  python3 fit_detail.py --id <12hex> --text "<his words>"

Appends his words to the purchase's notes and to the brand's sizing entry in
body_fit_profile.yaml (evidence line, through pending → confirm), then closes the
open question. Prints one line for the reply. Exits 1 if no such question is open,
so the agent never claims to have recorded something it did not.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
from data_paths import path as data_path  # noqa: E402
from _store import load_json, save_json  # noqa: E402

PENDING_DETAIL = data_path("pending_fit_detail.json")
LIFECYCLE = ROOT / "profile" / "purchase_lifecycle.json"
PENDING_FIT = ROOT / "profile" / "pending_body_fit_update.json"


def record(pid: str, text: str, run=subprocess.run) -> str:
    q = load_json(PENDING_DETAIL, {}) if PENDING_DETAIL.exists() else {}
    if not q or q.get("id") != pid:
        sys.exit(f"No open fit question with id {pid}. Nothing recorded.")
    text = " ".join(text.split())[:300]
    rows = load_json(LIFECYCLE, [])
    for r in rows:
        if r["id"] == q["purchase_id"]:
            r.setdefault("notes", []).append({"date": date.today().isoformat(), "note": f"fit detail ({q['verdict']}): {text}"})
    save_json(LIFECYCLE, rows)
    if q.get("category") and not (PENDING_FIT.exists() and load_json(PENDING_FIT, {})):
        note = f"{q['verdict']} fit, his words: {text}"
        save_json(PENDING_FIT, {"review_summary": f"Fit detail from the user for {q['retailer']} {q['category']}: {text[:80]}",
                                "add": {"brand_sizing": [{"brand": q["retailer"], "category": q["category"], "size": q.get("size") or "unknown",
                                                          "notes": note, "confidence": 90,
                                                          "evidence": [{"date": date.today().isoformat(), "note": note[:160]}]}]}})
        run([sys.executable, str(SCRIPTS / "confirm_body_fit_update.py")], capture_output=True, text=True)
    PENDING_DETAIL.unlink(missing_ok=True)
    return f"Recorded for {q['item']}: \"{text}\" — {q['retailer']} sizing updated. 🎩"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True); ap.add_argument("--text", required=True)
    a = ap.parse_args(argv)
    print(record(a.id, a.text))


if __name__ == "__main__":
    main()
