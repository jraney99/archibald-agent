#!/usr/bin/env python3
"""Turn refile_spec.json's "filters" section into a plan for apply_filter_migration.py.

Reads Gmail, changes nothing. For every new filter it shows how many existing
messages the query matches, so a query that matches nothing (a typo, or a
name-based `from:"…"` Gmail doesn't honour) is visible before anything is applied.

    python3 refile_filter_plan.py
"""
from __future__ import annotations

import os
import json
import sys
import time
from pathlib import Path

import refile_rules as rr

SNAPSHOTS = Path(__file__).resolve().parent.parent / "filter_snapshots"


def criteria_text(criteria: dict) -> str:
    return criteria.get("from") or criteria.get("query") or json.dumps(criteria, sort_keys=True)


def generated_criteria(entry: dict, filters_spec: dict) -> dict:
    """The Gmail criteria for one rulebook filter definition."""
    senders = entry["from"]
    query = f"from:({' OR '.join(senders)})" if len(senders) > 1 else f"from:{senders[0]}"
    if entry.get("extra"):
        query += " " + entry["extra"]
    types = entry.get("types", "exclude")
    if types not in ("exclude", "keep", "all"):
        raise ValueError(f"filter {entry['id']}: unknown types {types!r}")
    excluded = [] if types == "all" else list(filters_spec["exclude_security"])
    if types == "exclude":
        excluded += filters_spec["exclude_types"]
    criteria = {"query": query}
    if excluded:
        criteria["negatedQuery"] = "subject:(" + " OR ".join(excluded) + ")"
    return criteria


def action_for(entry: dict) -> dict:
    action = {}
    if entry.get("label"):
        action["addLabelNames"] = [entry["label"]]
    if entry.get("archive"):
        action["removeLabelIds"] = ["INBOX"]
    if not action:
        raise ValueError(f"filter {entry['id']}: neither label nor archive")
    return action


def is_junk_filter(filt: dict, label_names: dict) -> bool:
    added = [label_names.get(i, i) for i in filt.get("action", {}).get("addLabelIds", [])]
    return "TRASH" in added or LOW_PRIORITY in added


def _same_filter(live: dict, criteria: dict, action: dict, label_names: dict) -> bool:
    have = live.get("action", {})
    have_names = sorted(label_names.get(i, i) for i in have.get("addLabelIds", []))
    return (live.get("criteria", {}) == criteria
            and have_names == sorted(action.get("addLabelNames", []))
            and sorted(have.get("removeLabelIds", [])) == sorted(action.get("removeLabelIds", [])))


STATE_FILE = Path(os.path.expanduser("~/.openclaw/email_monitor_state.json")).expanduser()


def approved_filter_ids(state_file: Path = None) -> set:
    """Filters the user approved one tap at a time ("file this sender?"). They are his
    decisions, recorded in the daemon's state, so the generator never removes them."""
    try:
        state = json.loads(Path(state_file or STATE_FILE).read_text())
    except (OSError, ValueError):
        return set()
    return {fid for block in ("finance_filter", "unsub_filter")
            for fid in (state.get(block, {}).get("created", {}) or {}).values() if fid}


def build(filters_spec: dict, live: list, label_names: dict, protected: set = frozenset()) -> dict:
    """Make the live non-junk filters equal the rulebook's: delete strays, create what is missing."""
    wanted = [(e, generated_criteria(e, filters_spec), action_for(e)) for e in filters_spec["create"]]
    ids = [e["id"] for e, _, _ in wanted]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate filter ids in the rulebook")
    rules, kept = [], []
    for f in live:
        if is_junk_filter(f, label_names) or f.get("id") in protected:
            continue
        if any(_same_filter(f, c, a, label_names) for _, c, a in wanted):
            kept.append(f["id"])
            continue
        rules.append({"filter_id": f["id"],
                      "domain": ", ".join(label_names.get(i, i) for i in f["action"].get("addLabelIds", [])) or "archive",
                      "criteria": f.get("criteria", {}), "current_action": f.get("action", {}),
                      "senders": [], "reason": "not in the rulebook (refile_spec.json filters)"})
    for entry, criteria, action in wanted:
        if any(_same_filter(f, criteria, action, label_names) for f in live):
            continue
        rules.append({"filter_id": None, "domain": entry.get("label") or "archive", "criteria": {},
                      "current_action": {},
                      "senders": [{"address": entry["id"],
                                   "recommendation": "archive" if entry.get("archive") else "label_only",
                                   "replacement": {"criteria": criteria, "action": action}}],
                      "reason": entry.get("_why", "generated from refile_spec.json")})
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "refile-filters",
            "rules": rules, "kept": kept}


LOW_PRIORITY = "Low Priority"


def build_low_priority(live: list) -> dict:
    """Every auto-trash rule becomes "file under Low Priority, skip the inbox".

    Gmail empties Trash after 30 days, so a wrong trash rule loses mail for
    good; a folder never does. Criteria are kept exactly, including each rule's
    security guard, so account-security mail from these senders still reaches
    the inbox.
    """
    rules = []
    for f in live:
        if "TRASH" not in f.get("action", {}).get("addLabelIds", []):
            continue
        text = criteria_text(f["criteria"])
        rules.append({"filter_id": f["id"], "domain": "TRASH", "criteria": f["criteria"],
                      "current_action": f["action"], "reason": "never auto-delete",
                      "senders": [{"address": text[:60], "recommendation": "archive",
                                   "replacement": {"criteria": f["criteria"],
                                                   "action": {"addLabelNames": [LOW_PRIORITY],
                                                              "removeLabelIds": ["INBOX"]}}}]})
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "junk-to-low-priority", "rules": rules}


def count(svc, query: str, cap: int = 1500) -> int:
    n, token = 0, None
    while n < cap:
        page = svc.users().messages().list(userId="me", q=f"in:anywhere -in:spam -in:trash {query}",
                                           maxResults=500, pageToken=token).execute()
        n += len(page.get("messages", []))
        token = page.get("nextPageToken")
        if not token:
            break
    return n


def main() -> int:
    import sender_filter
    svc = sender_filter.gmail_service()
    if "--low-priority" in sys.argv:
        live = svc.users().settings().filters().list(userId="me").execute().get("filter", [])
        plan = build_low_priority(live)
        out = SNAPSHOTS / f"low-priority-plan-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps(plan, indent=1))
        guarded = sum(1 for r in plan["rules"] if r["criteria"].get("negatedQuery"))
        print(f"{len(plan['rules'])} auto-trash rules → '{LOW_PRIORITY}' ({guarded} keep their security guard)")
        print(f"Plan written to {out}")
        return 0
    spec = json.loads(rr.SPEC.read_text())["filters"]
    live = svc.users().settings().filters().list(userId="me").execute().get("filter", [])
    label_names = {l["id"]: l["name"] for l in svc.users().labels().list(userId="me").execute()["labels"]}
    plan = build(spec, live, label_names, approved_filter_ids())
    out = SNAPSHOTS / f"refile-filter-plan-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(plan, indent=1))
    deletes = [r for r in plan["rules"] if r["filter_id"]]
    creates = [r for r in plan["rules"] if not r["filter_id"]]
    junk = sum(1 for f in live if is_junk_filter(f, label_names))
    print(f"live filters: {len(live)} ({junk} junk rules, untouched) | already correct: {len(plan['kept'])}")
    print(f"REMOVE {len(deletes)}:")
    for r in deletes:
        print(f"  - {criteria_text(r['criteria'])[:78]:78} (→ {r['domain']})")
    print(f"CREATE {len(creates)} (existing mail each would match):")
    empty = []
    for r in creates:
        s = r["senders"][0]; crit = s["replacement"]["criteria"]
        n = count(svc, crit["query"] + (" -" + crit["negatedQuery"] if crit.get("negatedQuery") else ""))
        if n == 0:
            empty.append(s["address"])
        print(f"  + {n:4}  {r['domain'][:40]:40} {s['recommendation']:10} {s['address']}")
    if empty:
        print("note — match nothing today (new or rare senders):", ", ".join(empty))
    print(f"after: {len(live) - len(deletes) + len(creates)} filters")
    print(f"Plan written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
