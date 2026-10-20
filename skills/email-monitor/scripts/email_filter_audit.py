#!/usr/bin/env python3
"""Read-only audit of live Gmail filters against the user's relevance policy.

Every judgement comes from email_policy (the same module the daemon uses), so
the audit and the runtime cannot drift.

Run:
  python3 email_filter_audit.py --live [--json]
  python3 email_filter_audit.py --snapshot ../filter_snapshots/FILENAME.json

Levels: error (policy violation that can lose or expose mail), gap (policy
violation to migrate), warning (worth a look). Exit 0 means no errors/gaps.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "shared"))
from fetch_calendar import gog_env  # noqa: E402
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import email_policy as policy
import sender_filter

ACCOUNT = "owner@example.com"
HERE = Path(__file__).resolve().parent
DAEMON_LABEL = "com.openclaw.email-agent"
DAEMON_SOURCES = ("daemon.py", "email_policy.py", "sender_filter.py")
FILTER_SAMPLE_MAX = 100
_SENDER_CONFIG = sender_filter.load_config()
PROTECTED_DOMAINS = {"googlemail.com"} | {
    domain.lower()
    for entry in _SENDER_CONFIG.get("senders", [])
    if entry.get("area") == "finance"
    for domain in entry.get("domains", [])
}


# ── live loading (read-only) ────────────────────────────────────────────────

GOG = "/opt/homebrew/bin/gog"


def _run_json(args: list[str]) -> dict:
    # Every caller here and in filter_migration_plan.py writes a bare "gog". Normalise at
    # the one chokepoint: gog has needed GOG_KEYRING_PASSWORD since 2026-10-02, and a bare
    # name only resolves where /opt/homebrew/bin is on PATH. Both hold under the gateway;
    # neither holds in a plain shell or under the email daemon.
    if args and args[0] == "gog":
        args = [GOG, *args[1:]]
    result = subprocess.run(args, capture_output=True, text=True, timeout=180,
                            env=gog_env())
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return json.loads(result.stdout)


def search(query: str, max_results: int) -> list:
    return _run_json([
        "gog", "gmail", "messages", "search", "--account", ACCOUNT,
        "--max", str(max_results), "-j", "--", query,
    ]).get("messages", [])


def filter_query(criteria: dict) -> str:
    """The Gmail search that selects what a filter's sender criteria match."""
    parts = []
    if criteria.get("from"):
        parts.append(f"from:({criteria['from']})")
    if criteria.get("query"):
        parts.append(criteria["query"])
    return " ".join(parts)


def sample_filters(filters: list, max_results: int = FILTER_SAMPLE_MAX,
                   days: int = policy.HISTORY_DAYS) -> dict:
    """filter id → the mail it matched over the policy window (Trash included,
    Spam excluded). Only archive and Trash filters are sampled."""
    wanted = [f for f in filters
              if (policy.is_archive_action(f.get("action", {}))
                  or policy.is_trash_action(f.get("action", {})))
              and filter_query(f.get("criteria", {}))]

    def one(filt):
        q = f"in:anywhere -in:spam newer_than:{days}d {filter_query(filt['criteria'])}"
        return filt["id"], search(q, max_results)

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(one, wanted))


def load_live() -> tuple[list, dict, list, dict]:
    filters = _run_json([
        "gog", "gmail", "settings", "filters", "list",
        "--account", ACCOUNT, "-j",
    ]).get("filters", [])
    labels = _run_json([
        "gog", "gmail", "labels", "list", "--account", ACCOUNT, "-j",
    ]).get("labels", [])
    messages = search(f"in:anywhere newer_than:{policy.HISTORY_DAYS}d", 500)
    return filters, {x["id"]: x["name"] for x in labels}, messages, sample_filters(filters)


def daemon_freshness() -> dict:
    """Is the running daemon older than the policy code on disk?"""
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    pid = next((line.split()[0] for line in out.splitlines()
                if line.endswith(DAEMON_LABEL)), "-")
    if not pid.isdigit():
        return {"running": False}
    started = subprocess.run(["ps", "-o", "lstart=", "-p", pid],
                             capture_output=True, text=True).stdout.strip()
    from datetime import datetime
    started_ts = datetime.strptime(started, "%a %b %d %H:%M:%S %Y").timestamp()
    newest = max((HERE / name).stat().st_mtime for name in DAEMON_SOURCES)
    return {"running": True, "pid": int(pid), "started": started_ts,
            "source_mtime": newest, "stale": newest > started_ts}


# ── analysis ─────────────────────────────────────────────────────────────────

def _criterion_key(criteria: dict) -> str:
    if criteria.get("from"):
        return "from:" + criteria["from"].strip().lower().lstrip("@")
    return "query:" + re.sub(r"\s+", " ", criteria.get("query", "").strip().lower())


def _issue(level: str, code: str, detail: str, **extra) -> dict:
    return {"level": level, "code": code, "detail": detail, **extra}


def label_names(label_ids, labels: dict) -> list:
    return [labels.get(lid, lid) for lid in label_ids or []]


def profile_messages(messages: list, cap: int = policy.HISTORY_CAP) -> dict:
    """address → {counts, subjects, messages} for up to `cap` messages each."""
    out: dict = {}
    for msg in messages:
        address = sender_filter.extract_address(msg.get("from", ""))
        if "@" not in address:
            continue
        prof = out.setdefault(address, {"counts": policy.empty_counts(),
                                        "subjects": [], "messages": []})
        if len(prof["messages"]) >= cap:
            continue
        kind = policy.classify_subject(msg.get("subject", ""), msg.get("labels", []))
        prof["counts"][kind] += 1
        prof["subjects"].append((kind, msg.get("subject", "(no subject)")))
        prof["messages"].append({**msg, "kind": kind})
    return out


def _example(prof: dict, kind: str) -> str:
    return next((s for k, s in prof["subjects"] if k == kind), "")


def _low_priority_id(labels: dict) -> str:
    return next((i for i, name in labels.items() if name == policy.LOW_PRIORITY_LABEL), "")


def audit_filter(filt: dict, labels: dict, sample: list) -> list:
    """Issues for one archive/Trash filter, judged on the mail it matched."""
    issues = []
    fid = filt.get("id")
    action = filt.get("action", {})
    added = label_names(action.get("addLabelIds", []), labels)
    finance_label = next((n for n in added if n.split("/")[0] == policy.FINANCE_LABEL_ROOT), "")
    # A junk rule (legacy Trash, or filed under Low Priority) is judged by the
    # Trash checks, not the archive ones: it is meant to take marketing away.
    trash = policy.is_junk_action(action, _low_priority_id(labels))
    archive = policy.is_archive_action(action) and not trash
    guarded = policy.has_security_guard(filt.get("criteria", {}))

    # Every address in the sample is judged: Gmail's `from:` matching is
    # token-based, so a rule also files senders outside its literal domain.
    for address, prof in profile_messages(sample).items():
        c = prof["counts"]
        total = sum(c.values())
        exposed = [m for m in prof["messages"] if m["kind"] in ("security", "material")
                   and not (guarded and policy.trash_guard_spares(m.get("subject", "")))]
        if trash and exposed:
            issues.append(_issue("error", "mixed-sender-trash",
                f"{address}: Trash filter {fid} matches {len(exposed)} security/material "
                f"message(s), e.g. {exposed[0].get('subject', '')}",
                filter_id=fid, address=address, counts=c))
        if not archive:
            continue
        if c["security"]:
            issues.append(_issue("gap", "security-sender-auto-filed",
                f"{address}: {c['security']} security message(s) auto-filed by {fid} "
                f"— security senders stay under message-level review, e.g. "
                f"{_example(prof, 'security')}",
                filter_id=fid, address=address, counts=c))
        elif c["marketing"] and (c["material"] or c["unknown"]):
            issues.append(_issue("gap", "mixed-purpose-auto-file",
                f"{address}: {fid} auto-files a mixed stream ({c['material']} material, "
                f"{c['marketing']} marketing, {c['unknown']} unknown)",
                filter_id=fid, address=address, counts=c))
        elif c["marketing"] >= policy.MIN_CONSISTENT and c["marketing"] == total:
            issues.append(_issue("gap", "marketing-auto-filed",
                f"{address} is marketing-only but filter {fid} archives it instead "
                f"of unsubscribing/trashing it, e.g. {_example(prof, 'marketing')}",
                filter_id=fid, address=address, counts=c))
        elif c["unknown"] and c["material"]:
            issues.append(_issue("warning", "unverified-auto-file",
                f"{address}: {fid} auto-files {c['unknown']} unclassified subject(s) "
                f"alongside {c['material']} record(s)",
                filter_id=fid, address=address, counts=c))
        if finance_label and c["marketing"]:
            issues.append(_issue("gap", "finance-marketing-filed",
                f"{address}: {c['marketing']} marketing message(s) filed under "
                f"{finance_label} by {fid}, e.g. {_example(prof, 'marketing')}",
                filter_id=fid, address=address, counts=c))
        uncovered = [m for m in prof["messages"] if not visible(m, address, added)]
        if uncovered:
            issues.append(_issue("gap", "monitoring-coverage-missing",
                f"{address}: {len(uncovered)} security/material message(s) auto-filed by "
                f"{fid} have no inbox, backstop or catch-up path, e.g. "
                f"{uncovered[0].get('subject', '')}",
                filter_id=fid, address=address,
                subjects=[m.get("subject", "") for m in uncovered[:5]]))
    return issues


def visible(msg: dict, address: str, filter_labels: list) -> bool:
    """Would this message reach the user? Only security/material mail is owed."""
    if msg["kind"] not in ("security", "material"):
        return True
    names = msg.get("labels") or filter_labels
    if "INBOX" in names:
        return True
    return policy.visibility_path(msg["kind"], address, msg.get("subject", ""), names) is not None


def audit(filters: list, labels: dict, messages: list,
          filter_samples: dict | None = None, daemon: dict | None = None) -> dict:
    issues = []
    by_criterion = defaultdict(list)
    substack_trash = False
    filter_samples = filter_samples or {}
    low_priority = _low_priority_id(labels)

    for filt in filters:
        criteria = filt.get("criteria", {})
        action = filt.get("action", {})
        by_criterion[_criterion_key(criteria)].append(filt)
        tokens = policy.filter_from_tokens(criteria)
        add = set(action.get("addLabelIds", []))
        remove = set(action.get("removeLabelIds", []))

        for label_id in add | remove:
            if label_id not in labels:
                issues.append(_issue("error", "unknown-label",
                    f"{filt.get('id')}: unknown label {label_id}"))

        if "SPAM" in remove:
            issues.append(_issue("error", "spam-bypass",
                f"{_criterion_key(criteria)} removes Gmail's SPAM label "
                f"(Never send it to Spam) — filter {filt.get('id')}",
                filter_id=filt.get("id")))

        if policy.is_junk_action(action, low_priority):
            if not policy.has_security_guard(criteria) and not (
                    tokens and tokens <= set(policy.ALLOWED_BROAD_TRASH)):
                issues.append(_issue("warning", "trash-without-security-guard",
                    f"{_criterion_key(criteria)} → Trash has no security-subject "
                    f"exclusion (filter {filt.get('id')})", filter_id=filt.get("id")))
            for token in tokens:
                if token == "substack.com":
                    substack_trash = True
                if not policy.is_broad_domain(token) or token in policy.ALLOWED_BROAD_TRASH:
                    continue
                # A finance root domain can carry security/account mail. A
                # dedicated marketing subdomain (news.bank.example) is still a
                # broad rule, just not a critical one.
                protected = token in PROTECTED_DOMAINS
                issues.append(_issue("error" if protected else "gap",
                    "protected-domain-trash" if protected else "broad-domain-trash",
                    f"@{token} sends every address on the domain to Trash",
                    filter_id=filt.get("id"), domain=token))

        if policy.is_archive_action(action) and not policy.is_junk_action(action, low_priority):
            for token in tokens:
                if policy.is_broad_domain(token):
                    issues.append(_issue("gap", "broad-domain-archive",
                        f"@{token} sends every address on the domain past the inbox "
                        f"(filter {filt.get('id')})",
                        filter_id=filt.get("id"), domain=token))

        if filt.get("id") in filter_samples:
            issues.extend(audit_filter(filt, labels, filter_samples[filt["id"]]))

    for key, copies in by_criterion.items():
        if key and len(copies) > 1:
            issues.append(_issue("error", "duplicate-filter",
                f"{key} has {len(copies)} filters: " + ", ".join(x["id"] for x in copies)))

    if not substack_trash:
        issues.append(_issue("error", "required-filter-missing",
            "The user's required @substack.com junk rule (Low Priority or Trash) is missing"))

    # Relevant mail in Trash / clutter left in Inbox, across the recent sample
    # plus every filter sample (in:anywhere includes Trash). A Trash filter
    # that matches the message is an error; otherwise it was most likely
    # deleted by hand (or by a rule that no longer exists) — a warning.
    by_id = {f.get("id"): f for f in filters}
    trash_matches = {
        m.get("id") for fid, sample in filter_samples.items() for m in sample
        if policy.is_junk_action(by_id.get(fid, {}).get("action", {}), low_priority)
        and not (policy.has_security_guard(by_id[fid].get("criteria", {}))
                 and policy.trash_guard_spares(m.get("subject", "")))}
    seen_ids, pool = set(), []
    for msg in messages + [m for sample in filter_samples.values() for m in sample]:
        key = msg.get("id") or (msg.get("from"), msg.get("subject"), msg.get("date"))
        if key not in seen_ids:
            seen_ids.add(key)
            pool.append(msg)
    for msg in pool:
        kind = policy.classify_subject(msg.get("subject", ""), msg.get("labels", []))
        if kind in ("security", "material") and "TRASH" in set(msg.get("labels", [])):
            address = sender_filter.extract_address(msg.get("from", ""))
            by_filter = msg.get("id") in trash_matches
            issues.append(_issue("error" if by_filter else "warning",
                "relevant-mail-trashed" if by_filter else "relevant-mail-in-trash",
                f"{address}: {kind} mail in Trash"
                f"{' (matched by a Trash filter)' if by_filter else ''} — {msg.get('subject', '')}",
                address=address))

    for address, prof in profile_messages(messages).items():
        inbox_marketing = [m for m in prof["messages"]
                           if m["kind"] == "marketing" and "INBOX" in set(m.get("labels", []))]
        if len(inbox_marketing) >= policy.MIN_CONSISTENT and not (
                prof["counts"]["security"] or prof["counts"]["material"]):
            issues.append(_issue("gap", "recurring-clutter-inbox",
                f"{address}: {len(inbox_marketing)} marketing messages remain in Inbox; "
                f"example: {inbox_marketing[0].get('subject', '')}", address=address))

    if daemon and daemon.get("running") is False:
        issues.append(_issue("error", "daemon-not-running",
            f"{DAEMON_LABEL} is not running — nothing is monitoring Gmail"))
    elif daemon and daemon.get("stale"):
        issues.append(_issue("gap", "daemon-stale",
            f"{DAEMON_LABEL} (pid {daemon['pid']}) started before the current policy "
            f"code was written — coverage fixes are not live until it restarts"))

    summary = Counter(i["level"] for i in issues)
    return {
        "status": "PASS" if not (summary["error"] or summary["gap"]) else "FAIL",
        "filter_count": len(filters),
        "message_sample_count": len(messages),
        "filters_profiled": len(filter_samples),
        "summary": dict(summary),
        "issues": issues,
    }


def print_human(report: dict) -> None:
    s = report["summary"]
    print(f"EMAIL FILTER AUDIT: {report['status']} — "
          f"{s.get('error', 0)} errors, {s.get('gap', 0)} gaps, "
          f"{s.get('warning', 0)} warnings")
    print(f"Checked {report['filter_count']} filters, profiled "
          f"{report.get('filters_profiled', 0)} filters' 90-day mail and "
          f"{report['message_sample_count']} recent messages.")
    by_code = Counter((x["level"], x["code"]) for x in report["issues"])
    print("By type: " + ", ".join(f"{code}={n} ({level})"
                                  for (level, code), n in sorted(by_code.items())))
    for level in ("error", "gap"):
        for issue in report["issues"]:
            if issue["level"] == level:
                print(f"[{level.upper()}] {issue['code']}: {issue['detail']}")
    for issue in [x for x in report["issues"] if x["level"] == "warning"][:5]:
        print(f"[WARNING SAMPLE] {issue['code']}: {issue['detail']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--live", action="store_true")
    src.add_argument("--snapshot", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    daemon = None
    if args.live:
        filters, labels, messages, samples = load_live()
        daemon = daemon_freshness()
    else:
        raw = json.loads(args.snapshot.read_text())
        filters = raw.get("filters", raw.get("filter", []))
        labels = {"INBOX": "INBOX", "SPAM": "SPAM", "TRASH": "TRASH"}
        for filt in filters:
            for lid in filt.get("action", {}).get("addLabelIds", []):
                labels.setdefault(lid, lid)
            for lid in filt.get("action", {}).get("removeLabelIds", []):
                labels.setdefault(lid, lid)
        messages, samples = [], {}

    report = audit(filters, labels, messages, samples, daemon)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
