"""Read-only usage accounting across main-agent conversations, cron, and direct API calls."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from usage_logger import estimate_cost, PRICING_VERSION

ROOT = Path.home() / ".openclaw"


def summarize(since, root=ROOT):
    groups = {}
    seen = set()
    errors = []
    sessions = root / "agents/main/sessions"
    if not sessions.is_dir():
        raise FileNotFoundError("Main-agent session directory is unavailable")
    # Live transcripts AND their archives (`<id>.jsonl.reset.<ts>`, `.deleted.<ts>`,
    # `.bak…`): a daily reset or a compaction moves the real usage records into an
    # archive, so a transcripts-only glob would undercount by the whole previous day
    # (2026-10-03: $6.39 lived in a .reset file; the live file held $0.66). What is
    # NOT a transcript: the trajectory file, and OpenClaw's transient `<id>.jsonl.lock`
    # that exists only while a turn is being written — on 2026-10-03 the monitor ran
    # mid-turn, read the lock, and reported its JSONDecodeError to the user as a usage
    # read error.
    for p in sessions.glob("*.jsonl*"):
        if "trajectory" in p.name or p.name.endswith(".lock") or p.stat().st_mtime < since.timestamp():
            continue
        try:
            with p.open() as f:
                records = [json.loads(line) for line in f if line.strip()]
            sid = next((r.get("id") for r in records if r.get("type") == "session"), p.stem)
            key = ""
            trajectory = sessions / (sid + ".trajectory.jsonl")
            if trajectory.exists():
                with trajectory.open() as f:
                    key = json.loads(f.readline()).get("sessionKey", "")
            category = "heartbeat" if ":heartbeat" in key else "cron" if ":cron:" in key else "conversation"
            for r in records:
                m = r.get("message", {})
                usage = m.get("usage")
                if m.get("role") != "assistant" or not usage or m.get("model") == "delivery-mirror":
                    continue
                stamp = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
                identity = (r.get("id"), r["timestamp"])
                if stamp < since or identity in seen:
                    continue
                seen.add(identity)
                add(groups, category, m.get("model", "unknown"), usage)
        except (ValueError, OSError, KeyError) as exc:
            errors.append(f"{p.name}: {type(exc).__name__}")
    direct_seen = set()
    direct = root / "logs/api_usage.jsonl"
    if direct.exists():
        with direct.open() as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("ts", 0) < since.timestamp():
                        continue
                    identity = r.get("provider_response_id") or r.get("event_id")
                    if identity and identity in direct_seen:
                        continue
                    if identity:
                        direct_seen.add(identity)
                    add(groups, "direct:" + r.get("script", "unknown"), r.get("model", "unknown"), {
                        "input": r.get("input_tokens", 0), "output": r.get("output_tokens", 0),
                        "cacheRead": r.get("cache_read_input_tokens", 0),
                        "cacheWrite": r.get("cache_creation_input_tokens", 0),
                        "cacheWrite1h": r.get("cache_creation_1h_input_tokens", 0),
                    })
                except (ValueError, TypeError):
                    errors.append("Malformed direct API record")
    return {"since": since.isoformat(), "groups": groups,
            "pricing_version": PRICING_VERSION, "invoice_verified": False,
            "coverage": "main-agent transcripts and direct-call ledger; cron rows are not added again",
            "estimated_cost_usd": round(sum(g["estimated_cost_usd"] for g in groups.values()), 6),
            "unpriced_calls": sum(g["unpriced_calls"] for g in groups.values()), "read_errors": errors}


def add(groups, category, model, usage):
    g = groups.setdefault(category + ":" + model, {"calls": 0, "input": 0, "output": 0,
                        "cacheRead": 0, "cacheWrite": 0, "estimated_cost_usd": 0, "unpriced_calls": 0})
    g["calls"] += 1
    for field in ("input", "output", "cacheRead", "cacheWrite"):
        g[field] += usage.get(field, 0) or 0
    cost = estimate_cost(model, *(usage.get(field, 0) or 0 for field in
                                  ("input", "output", "cacheRead", "cacheWrite", "cacheWrite1h")))
    if cost is None:
        g["unpriced_calls"] += 1
    else:
        g["estimated_cost_usd"] = round(g["estimated_cost_usd"] + cost, 8)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(summarize(datetime.now(timezone.utc) - timedelta(days=args.days)), indent=2))
