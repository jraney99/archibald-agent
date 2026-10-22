#!/usr/bin/env python3
"""Prototype usage ledger with explicit coverage gaps and daily rollups."""

from __future__ import annotations

import json
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PRICING_AS_OF = "2026-10-07"

PRICING = {
    "claude-sonnet-4-6": {"input": 3, "output": 15, "read": 0.30, "write5": 3.75, "write1h": 6},
    "claude-sonnet-5": {"input": 2, "output": 10, "read": 0.20, "write5": 2.50, "write1h": 4},
    "claude-sonnet-5-5": {"input": 2, "output": 10, "read": 0.10, "write5": 2.50, "write1h": 4},
    "claude-haiku-4-5": {"input": 1, "output": 5, "read": 0.10, "write5": 1.25, "write1h": 2},
    "claude-haiku-4-5-20251001": {"input": 1, "output": 5, "read": 0.10, "write5": 1.25, "write1h": 2},
    "claude-opus-4-8": {"input": 5, "output": 25, "read": 0.50, "write5": 6.25, "write1h": 10},
}

HAIKU_55_LOW = {"input": 0.10, "output": 0.50, "read": 0.01, "write5": 0.125, "write1h": 0.20}
HAIKU_55_HIGH = {"input": 0.50, "output": 2.50, "read": 0.05, "write5": 0.625, "write1h": 1.00}


def rates_for(model: str, usage: dict):
    if model == "claude-haiku-5-5":
        prompt = sum(int(usage.get(field, 0) or 0) for field in ("input", "cacheRead", "cacheWrite"))
        return HAIKU_55_HIGH if prompt > 100_000 else HAIKU_55_LOW
    return PRICING.get(model)


def cost(model: str, usage: dict):
    rates = rates_for(model, usage)
    if not rates:
        return None
    one_hour = int(usage.get("cacheWrite1h", 0) or 0)
    write_five = max(0, int(usage.get("cacheWrite", 0) or 0) - one_hour)
    return (
        int(usage.get("input", 0) or 0) * rates["input"]
        + int(usage.get("output", 0) or 0) * rates["output"]
        + int(usage.get("cacheRead", 0) or 0) * rates["read"]
        + write_five * rates["write5"]
        + one_hour * rates["write1h"]
    ) / 1_000_000


def empty_group():
    return {"calls": 0, "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "cacheWrite1h": 0, "estimated_cost_usd": 0.0, "unpriced_calls": 0, "unpriced_tokens": 0}


def session_category(sessions: Path, transcript: Path) -> str:
    """Classify a transcript using its trajectory without loading the transcript."""
    session_id = transcript.name.split(".jsonl", 1)[0]
    trajectory = sessions / f"{session_id}.trajectory.jsonl"
    try:
        with trajectory.open() as handle:
            first = next((line for line in handle if line.strip()), "")
        key = json.loads(first).get("sessionKey", "") if first else ""
    except (OSError, ValueError, TypeError):
        key = ""
    if ":heartbeat" in key:
        return "heartbeat"
    if ":cron:" in key:
        return "cron"
    return "conversation"


def add(groups: dict, daily: dict, category: str, model: str, usage: dict, stamp: datetime):
    key = f"{category}:{model}"
    group = groups.setdefault(key, empty_group())
    day_group = daily.setdefault(stamp.date().isoformat(), {}).setdefault(key, empty_group())
    for target in (group, day_group):
        target["calls"] += 1
        for field in ("input", "output", "cacheRead", "cacheWrite", "cacheWrite1h"):
            target[field] += int(usage.get(field, 0) or 0)
        estimated = cost(model, usage)
        if estimated is None:
            target["unpriced_calls"] += 1
            target["unpriced_tokens"] += sum(int(usage.get(field, 0) or 0) for field in ("input", "output", "cacheRead", "cacheWrite"))
        else:
            target["estimated_cost_usd"] = round(target["estimated_cost_usd"] + estimated, 8)


def summarize(root: Path, since: datetime):
    groups, daily = {}, {}
    seen_messages, seen_tools, seen_compactions = set(), set(), set()
    compactions_without_usage = 0
    web_calls = defaultdict(int)
    read_errors = []
    sessions = root / "agents/main/sessions"
    for path in sessions.glob("*.jsonl*") if sessions.exists() else []:
        if "trajectory" in path.name or path.name.endswith(".lock"):
            continue
        category = session_category(sessions, path)
        try:
            with path.open() as handle:
                lines = enumerate(handle, start=1)
                for line_number, line in lines:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError) as exc:
                        read_errors.append(f"{path.name}:{line_number}: {type(exc).__name__}")
                        continue
                    raw_stamp = row.get("timestamp") or row.get("ts")
                    if not raw_stamp:
                        continue
                    stamp = datetime.fromisoformat(str(raw_stamp).replace("Z", "+00:00"))
                    if stamp < since:
                        continue
                    message = row.get("message", {})
                    if row.get("type") == "compaction":
                        identity = (row.get("id"), raw_stamp) if row.get("id") else (path.name, raw_stamp)
                        if identity not in seen_compactions:
                            seen_compactions.add(identity)
                            if not row.get("usage") and not message.get("usage"):
                                compactions_without_usage += 1
                    if message.get("role") != "assistant":
                        continue
                    for block in message.get("content", []) or []:
                        if block.get("type") != "toolCall":
                            continue
                        tool_id = block.get("id") or (path.name, raw_stamp, block.get("name"), json.dumps(block.get("arguments"), sort_keys=True))
                        if tool_id in seen_tools:
                            continue
                        seen_tools.add(tool_id)
                        if block.get("name") in ("web_search", "web_fetch"):
                            web_calls[block["name"]] += 1
                    usage = message.get("usage")
                    if not usage or message.get("model") == "delivery-mirror":
                        continue
                    identity = (row.get("id"), raw_stamp) if row.get("id") else (path.name, raw_stamp, message.get("model"))
                    if identity in seen_messages:
                        continue
                    seen_messages.add(identity)
                    add(groups, daily, category, message.get("model", "unknown"), usage, stamp)
        except Exception as exc:
            read_errors.append(f"{path.name}: {type(exc).__name__}")

    direct = root / "logs/api_usage.jsonl"
    if direct.exists():
        try:
            with direct.open() as handle:
                for index, line in enumerate(handle, start=1):
                    try:
                        row = json.loads(line)
                        stamp = datetime.fromtimestamp(float(row["ts"]), timezone.utc)
                        if stamp < since:
                            continue
                        usage = {
                            "input": row.get("input_tokens", 0),
                            "output": row.get("output_tokens", 0),
                            "cacheRead": row.get("cache_read_input_tokens", 0),
                            "cacheWrite": row.get("cache_creation_input_tokens", 0),
                            "cacheWrite1h": row.get("cache_creation_1h_input_tokens", 0),
                        }
                        add(groups, daily, "direct:" + row.get("script", "unknown"), row.get("model", "unknown"), usage, stamp)
                    except Exception as exc:
                        read_errors.append(f"api_usage.jsonl:{index}: {type(exc).__name__}")
        except OSError as exc:
            read_errors.append(f"api_usage.jsonl: {type(exc).__name__}")

    reasons = []
    if compactions_without_usage:
        reasons.append("compaction_usage_missing")
    if web_calls:
        reasons.append("web_provider_cost_missing")
    if any(group["unpriced_calls"] for group in groups.values()):
        reasons.append("unknown_model_pricing")
    reasons.append("provider_invoice_not_reconciled")
    return {
        "since": since.isoformat(),
        "pricing_as_of": PRICING_AS_OF,
        "billing_status": "local_estimate_lower_bound" if reasons else "local_estimate",
        "groups": groups,
        "daily": daily,
        "estimated_cost_usd": round(sum(group["estimated_cost_usd"] for group in groups.values()), 8),
        "unpriced_calls": sum(group["unpriced_calls"] for group in groups.values()),
        "unpriced_tokens": sum(group["unpriced_tokens"] for group in groups.values()),
        "compactions_without_usage": compactions_without_usage,
        "unpriced_web_calls": dict(web_calls),
        "lower_bound_reasons": reasons,
        "read_errors": read_errors,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(summarize(args.root, datetime.now(timezone.utc) - timedelta(days=args.days)), indent=2))
