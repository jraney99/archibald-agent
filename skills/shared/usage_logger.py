"""Local ledger for direct Anthropic API calls made by workspace scripts.

These scripts call anthropic.Anthropic() directly with their own API key,
outside of OpenClaw's own cron/agent-turn token tracking (state/openclaw.sqlite).
Without this, there was no local way to see where API spend was going short of
re-deriving it from Anthropic Console usage. Call log_usage() right after every
client.messages.create() with the response.

Pricing is a snapshot (per 1M tokens) — update PRICING when rates change.
"""

import json
import time
import os
import sys
import uuid
import fcntl
from pathlib import Path
from typing import Optional

LOG_FILE = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw"))) / "logs/api_usage.jsonl"
PRICING_VERSION = "inherited-local-table-2026-10-15-unverified"
RUN_ID = os.environ.get("ARCHIBALD_RUN_ID") or uuid.uuid4().hex

# USD per 1,000,000 tokens. Update if Anthropic pricing changes.
PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5":          {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-sonnet-5":           {"input": 2.00, "output": 10.00},
    "claude-opus-4-8":           {"input": 5.00, "output": 25.00},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read: int = 0, cache_write: int = 0,
                  cache_write_1h: int = 0) -> Optional[float]:
    rates = PRICING.get(model)
    if not rates:
        return None
    short_write = max(0, cache_write - cache_write_1h)
    return ((input_tokens + cache_read * 0.1 + short_write * 1.25 + cache_write_1h * 2) * rates["input"]
            + output_tokens * rates["output"]) / 1_000_000


def log_usage(script: str, model: str, usage, extra: Optional[dict] = None, *, response_id=None) -> None:
    """Append one line to the local API usage ledger.

    `usage` is the response.usage object from client.messages.create()
    (has .input_tokens, .output_tokens, and optionally cache fields).
    """
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    creation = getattr(usage, "cache_creation", None)
    cache_write_1h = getattr(creation, "ephemeral_1h_input_tokens", 0) or 0
    cost = estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write, cache_write_1h)

    entry = {
        "ts": time.time(),
        "schema_version": 2, "run_id": RUN_ID,
        "event_id": str(response_id) if response_id else uuid.uuid4().hex,
        "provider_response_id": response_id, "pricing_version": PRICING_VERSION,
        "script": script,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "cache_creation_1h_input_tokens": cache_write_1h,
        "estimated_cost_usd": round(cost, 6) if cost is not None else None,
        "pricing_status": "estimated" if cost is not None else "unpriced",
    }
    if extra:
        entry["extra"] = extra

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(entry) + "\n")
            f.flush()
    except Exception as exc:
        print("usage_logger: usage NOT recorded (" + type(exc).__name__ + ")", file=sys.stderr)
