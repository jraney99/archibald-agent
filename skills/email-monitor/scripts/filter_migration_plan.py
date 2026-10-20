#!/usr/bin/env python3
"""Read-only planner: replace broad-domain Gmail archive rules with exact-address
rules, using the same policy (email_policy) the daemon and audit use.

For every live filter that archives a whole domain it:
  1. discovers every exact sender the rule caught over the policy window,
  2. profiles each sender (up to 30 messages, 90 days, Trash included),
  3. applies the policy-5 evidence rule and the coverage model, and
  4. emits a machine-readable plan: replacement exact-address filters, or
     message-level handling, or removal without replacement.

It never modifies Gmail. Run:
  python3 filter_migration_plan.py --live [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import email_filter_audit as audit
import email_policy as policy
import sender_filter

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "filter_snapshots"
DISCOVERY_MAX = 300
_REPLACEMENT_LABELS = {
    policy.ARCHIVE: "exact-address archive",
    policy.TRASH: "exact-address unsubscribe + Trash",
    policy.MESSAGE_LEVEL: "message-level (no filter)",
    policy.INSUFFICIENT: "remove without replacement",
}


def broad_archive_rules(filters: list) -> list:
    return [f for f in filters
            if policy.is_archive_action(f.get("action", {}))
            and any(policy.is_broad_domain(t)
                    for t in policy.filter_from_tokens(f.get("criteria", {})))]


def exact_filter_index(filters: list) -> dict:
    """address → ids of existing filters that name that exact address."""
    index: dict = {}
    for f in filters:
        for token in policy.filter_from_tokens(f.get("criteria", {})):
            if "@" in token:
                index.setdefault(token, []).append(f["id"])
    return index


def sender_rows(messages: list, address: str) -> list:
    return [m for m in messages
            if sender_filter.extract_address(m.get("from", "")) == address]


def _confidence(rule: str, counts: dict) -> str:
    total = sum(counts.values())
    if rule == policy.INSUFFICIENT:
        return "low"
    if rule == policy.MESSAGE_LEVEL and counts.get("security"):
        return "high"
    return "high" if total >= 5 else "medium"


def plan_sender(address: str, rows: list, rule_labels: list, action: dict,
                labels: dict, unsubscribe: dict | None = None) -> dict:
    """Recommendation for one exact sender, from its sampled history."""
    counts = policy.empty_counts()
    samples, uncovered, paths = [], [], set()
    for msg in rows[:policy.HISTORY_CAP]:
        subject = msg.get("subject", "(no subject)")
        names = msg.get("labels") or rule_labels
        kind = policy.classify_subject(subject, names)
        counts[kind] += 1
        if len(samples) < 6:
            samples.append({"kind": kind, "subject": subject, "date": msg.get("date")})
        owner = policy.dedicated_path_for(address, subject, rule_labels)
        if owner:
            paths.add(owner.id)
        if kind in ("security", "material") and policy.visibility_path(
                kind, address, subject, rule_labels) is None:
            uncovered.append(subject)

    rule, reason = policy.recommend_rule(counts)
    if rule == policy.ARCHIVE and uncovered:
        rule, reason = policy.MESSAGE_LEVEL, (
            f"records would be archived with no backstop or catch-up path "
            f"(e.g. {uncovered[0]!r})")

    replacement = None
    if rule == policy.ARCHIVE:
        replacement = {"criteria": {"from": address},
                       "action": {"addLabelIds": list(action.get("addLabelIds", [])),
                                  "removeLabelIds": ["INBOX"]},
                       "label_names": rule_labels}
    elif rule == policy.TRASH:
        replacement = {"criteria": policy.trash_filter_criteria(address),
                       "action": policy.junk_filter_action(),
                       "unsubscribe": unsubscribe or {"http": None, "mailto": None}}

    dominant = max(policy.KINDS, key=lambda k: counts[k])
    outliers = [m.get("subject", "") for m in rows[:policy.HISTORY_CAP]
                if policy.classify_subject(m.get("subject", ""),
                                           m.get("labels") or rule_labels) != dominant][:5]

    after = ("inbox" if rule in (policy.MESSAGE_LEVEL, policy.INSUFFICIENT)
             else "trash" if rule == policy.TRASH else
             sorted({policy.visibility_path("material", address, s["subject"], rule_labels)
                     or "none" for s in samples}))
    return {
        "address": address,
        "counts": counts,
        "sampled": sum(counts.values()),
        "sample_subjects": samples,
        "outlier_subjects": outliers,
        "dedicated_paths": sorted(paths),
        "recommendation": rule,
        "replacement_kind": _REPLACEMENT_LABELS[rule],
        "replacement": replacement,
        "visibility_after": after,
        "confidence": _confidence(rule, counts),
        "reason": reason,
    }


def caught_addresses(filt: dict, discovered: list) -> list:
    """(address, match) for every sender the live rule actually catches.

    Gmail's `from:` matching is token-based, so `@marriott.com` also catches
    reservations@res-marriott.com and a display name that mentions the domain.
    Those "gmail-fuzzy" senders lose their filing too when the rule goes, so
    they are profiled like in-domain ("domain") senders.
    """
    tokens = policy.filter_from_tokens(filt.get("criteria", {}))
    seen, out = set(), []
    for msg in discovered:
        address = sender_filter.extract_address(msg.get("from", ""))
        if "@" in address and address not in seen:
            seen.add(address)
            match = "domain" if any(policy.covers(address, t) for t in tokens) else "gmail-fuzzy"
            out.append((address, match))
    return out


def plan_rule(filt: dict, labels: dict, discovered: list, sender_plans: dict,
              owned_by: dict, exact_index: dict) -> dict:
    tokens = policy.filter_from_tokens(filt.get("criteria", {}))
    broad = sorted(t for t in tokens if policy.is_broad_domain(t))
    caught = caught_addresses(filt, discovered)

    senders, replacements = [], []
    for address, match in caught:
        sp = dict(sender_plans[address], match=match)
        existing = [fid for fid in exact_index.get(address, []) if fid != filt["id"]]
        if existing:
            sp["note"] = f"already named by exact filter(s) {', '.join(existing)}"
            sp["replacement"] = None
        elif owned_by.get(address) != filt["id"]:
            sp["note"] = f"replacement planned under overlapping rule {owned_by[address]}"
            sp["replacement"] = None
        if sp["replacement"]:
            replacements.append(sp["replacement"])
        senders.append(sp)

    totals = policy.empty_counts()
    for sp in senders:
        for k in policy.KINDS:
            totals[k] += sp["counts"][k]
    confs = {sp["confidence"] for sp in senders}
    confidence = ("low" if not senders or confs == {"low"} else
                  "high" if confs == {"high"} else "medium")
    if not senders:
        reason = f"no mail from @{', @'.join(broad)} in {policy.HISTORY_DAYS} days"
    else:
        reason = "; ".join(f"{sp['address']}: {sp['recommendation']} ({sp['reason']})"
                           for sp in senders)
    return {
        "filter_id": filt["id"],
        "domain": broad[0] if len(broad) == 1 else broad,
        "criteria": filt.get("criteria", {}),
        "current_action": filt.get("action", {}),
        "current_labels": audit.label_names(filt.get("action", {}).get("addLabelIds", []), labels),
        "exact_senders_observed": [a for a, _ in caught],
        "gmail_fuzzy_matches": [a for a, m in caught if m == "gmail-fuzzy"],
        "counts": totals,
        "dedicated_paths": sorted({p for sp in senders for p in sp["dedicated_paths"]}),
        "senders": senders,
        "rule_action": "replace" if replacements else "delete",
        "recommended_replacement": replacements,
        "confidence": confidence,
        "reason": reason,
    }


def build_plan(filters: list, labels: dict, discovery: dict, histories: dict,
               unsubscribe: dict | None = None) -> dict:
    """Pure planning step (tests drive this directly).

    discovery  filter id → messages the rule matched (for sender discovery)
    histories  exact address → that sender's own sampled messages
    """
    unsubscribe = unsubscribe or {}
    rules = broad_archive_rules(filters)
    exact_index = exact_filter_index(filters)
    sender_plans, owned_by = {}, {}
    for filt in rules:
        rule_labels = audit.label_names(filt["action"].get("addLabelIds", []), labels)
        for address, _ in caught_addresses(filt, discovery.get(filt["id"], [])):
            if address not in sender_plans:
                owned_by[address] = filt["id"]
                sender_plans[address] = plan_sender(
                    address, sender_rows(histories.get(address, []), address),
                    rule_labels, filt["action"], labels, unsubscribe.get(address))
    planned = [plan_rule(f, labels, discovery.get(f["id"], []), sender_plans,
                         owned_by, exact_index) for f in rules]
    tally = {}
    for sp in sender_plans.values():
        tally[sp["recommendation"]] = tally.get(sp["recommendation"], 0) + 1
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "policy": {"history_days": policy.HISTORY_DAYS, "history_cap": policy.HISTORY_CAP,
                   "min_consistent": policy.MIN_CONSISTENT},
        "filter_count": len(filters),
        "broad_archive_rules": len(planned),
        "exact_senders": len(sender_plans),
        "sender_recommendations": tally,
        "rules_to_delete_without_replacement": sum(r["rule_action"] == "delete" for r in planned),
        "replacement_filters": sum(len(r["recommended_replacement"]) for r in planned),
        "rules": planned,
    }


# ── live collection (read-only) ─────────────────────────────────────────────

def _unsubscribe_header(message_id: str) -> dict:
    try:
        raw = audit._run_json(["gog", "gmail", "get", "--account", audit.ACCOUNT,
                               message_id, "--format", "metadata", "-j"])
    except Exception:
        return {"http": None, "mailto": None}
    msg = raw.get("message", raw)
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    parsed = sender_filter.parse_list_unsubscribe(headers.get("list-unsubscribe", ""))
    parsed["one_click"] = bool(headers.get("list-unsubscribe-post"))
    return parsed


def collect_live() -> tuple[list, dict, dict, dict, dict]:
    filters = audit._run_json(["gog", "gmail", "settings", "filters", "list",
                               "--account", audit.ACCOUNT, "-j"]).get("filters", [])
    labels = {x["id"]: x["name"] for x in audit._run_json(
        ["gog", "gmail", "labels", "list", "--account", audit.ACCOUNT, "-j"]).get("labels", [])}
    rules = broad_archive_rules(filters)
    discovery = audit.sample_filters(rules, max_results=DISCOVERY_MAX)
    addresses = sorted({address for f in rules
                        for address, _ in caught_addresses(f, discovery.get(f["id"], []))})

    def history(address):
        q = (f"in:anywhere -in:spam newer_than:{policy.HISTORY_DAYS}d from:({address})")
        return address, audit.search(q, policy.HISTORY_CAP)

    with ThreadPoolExecutor(max_workers=4) as pool:
        histories = dict(pool.map(history, addresses))

    unsubscribe = {}
    for address, rows in histories.items():
        rows = sender_rows(rows, address)
        kinds = {policy.classify_subject(m.get("subject", ""), m.get("labels", [])) for m in rows}
        if rows and kinds == {"marketing"}:
            unsubscribe[address] = _unsubscribe_header(rows[0]["id"])
    return filters, labels, discovery, histories, unsubscribe


def print_summary(plan: dict) -> None:
    print(f"BROAD ARCHIVE MIGRATION PLAN — {plan['broad_archive_rules']} rules, "
          f"{plan['exact_senders']} exact senders observed")
    print("Sender recommendations: " + ", ".join(
        f"{k}={v}" for k, v in sorted(plan["sender_recommendations"].items())))
    print(f"Rules replaced: {plan['broad_archive_rules'] - plan['rules_to_delete_without_replacement']}"
          f" · deleted without replacement: {plan['rules_to_delete_without_replacement']}"
          f" · replacement filters: {plan['replacement_filters']}")
    for rule in plan["rules"]:
        print(f"\n{rule['domain']}  [{rule['filter_id']}]  → {rule['rule_action']} "
              f"({rule['confidence']})")
        for sp in rule["senders"]:
            c = sp["counts"]
            print(f"   {sp['address']}: {sp['recommendation']} "
                  f"[sec {c['security']} · mat {c['material']} · mkt {c['marketing']} · "
                  f"unk {c['unknown']}] {sp.get('note', '')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    filters, labels, discovery, histories, unsubscribe = collect_live()
    plan = build_plan(filters, labels, discovery, histories, unsubscribe)
    out = args.out or SNAPSHOT_DIR / f"migration-plan-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(plan, indent=2))
    print_summary(plan)
    print(f"\nPlan written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
