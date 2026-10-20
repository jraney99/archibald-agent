#!/usr/bin/env python3
"""Track retailer-source success separately from product availability.

I/O goes through scripts/_store.py: atomic writes, and a corrupt file fails loudly
with the scripted repair named instead of a bare traceback inside a scan. Found
corrupt on 2026-10-05 (valid object + the tail of an older, longer version); the
cause was the watchlist scan running several small writers in parallel while each
used a plain truncate-and-write. The same thing hit product_history.json on
2026-10-08, which is why the fix now lives in one shared helper.

A corrupt file is NOT auto-repaired and NOT overwritten: the failure counts in it are
the signal for a source that keeps blocking us, so silently resetting to {} would
destroy the evidence this file exists to hold. Repair: scripts/repair_json.py.
"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / 'profile/source_health.json'
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _store import load_json, save_json  # noqa: E402
from _stores import canonical  # noqa: E402


def _load() -> dict:
    # load_json exits on corruption without touching the file; the failure counts
    # are the record of which sources keep blocking us, so nothing resets them.
    return load_json(PATH, {})


def _save(data: dict) -> None:
    save_json(PATH, data)


def record(source: str, status: str, note: str = "", data: dict = None) -> dict:
    """Update one store's row under its canonical slug (config/stores.json aliases)."""
    d = _load() if data is None else data
    slug = canonical(source)
    row = d.setdefault(slug, {"failures": 0})
    row.update({"last_checked": datetime.now().astimezone().isoformat(), "last_status": status, "note": note})
    row['failures'] = 0 if status == 'ok' else row.get('failures', 0) + 1
    if data is None:
        _save(d)
    return row


BLOCKED_AFTER = 3        # consecutive failures before a page-only store is not worth a search


def blocked(data: dict = None, threshold: int = BLOCKED_AFTER) -> list:
    """Stores that keep refusing us, so the scan stops spending its capped web searches there.

    2026-10-13: these counts existed for a year and NOTHING read them — the file's whole
    purpose is "which sources keep blocking us" and no caller ever asked. Peter Millar had
    been 403ing on every run and the scan kept trying.
    """
    data = _load() if data is None else data
    out = [{"source": k, "failures": v.get("failures", 0), "last_status": v.get("last_status"),
            "note": v.get("note", ""), "last_checked": v.get("last_checked", "")}
           for k, v in data.items() if v.get("failures", 0) >= threshold]
    return sorted(out, key=lambda x: -x["failures"])


def report(data: dict = None) -> str:
    rows = blocked(data)
    if not rows:
        return "No source is persistently blocked."
    return "Do not spend a web search on: " + "; ".join(
        f"{r['source']} ({r['failures']} failures" + (f", {r['note'][:40]}" if r['note'] else "") + ")" for r in rows)


def merge_rows(data: dict) -> dict:
    """Fold every alias of a store into its canonical slug.

    Before 2026-10-12 the scan typed a fresh name each run, so Peter Millar sat under
    four keys and its failure counter could never pass 1. Merged row: failures summed
    (each was a real blocked read), everything else from the most recently checked.
    """
    out = {}
    for name, row in data.items():
        slug = canonical(name)
        if slug not in out:
            out[slug] = dict(row)
            continue
        cur = out[slug]
        newer = row if (row.get("last_checked") or "") > (cur.get("last_checked") or "") else cur
        merged = dict(newer)
        merged["failures"] = int(cur.get("failures", 0)) + int(row.get("failures", 0))
        if newer.get("last_status") == "ok":
            merged["failures"] = 0
        out[slug] = merged
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source')
    ap.add_argument('--status', choices=['ok', 'blocked', 'timeout', 'parse-error'])
    ap.add_argument('--note', default='')
    ap.add_argument('--merge-aliases', action='store_true', help='one-off: fold alias keys into canonical slugs')
    ap.add_argument('--blocked', action='store_true', help='stores that keep refusing us, so a scan can skip them')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if a.blocked:
        print(json.dumps(blocked(), indent=2) if a.json else report()); return
    if a.merge_aliases:
        before = _load(); after = merge_rows(before)
        _save(after)
        print(json.dumps({"before": sorted(before), "after": sorted(after)}, indent=2)); return
    if not (a.source and a.status):
        ap.error("--source and --status are required (or --merge-aliases)")
    print(json.dumps(record(a.source, a.status, a.note), indent=2))


if __name__ == "__main__":
    main()
