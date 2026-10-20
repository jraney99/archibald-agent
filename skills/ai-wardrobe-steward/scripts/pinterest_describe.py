#!/usr/bin/env python3
"""Describe imported Pinterest pins with a vision model, once each.

  python3 pinterest_describe.py [--limit N] [--dry-run]

For every pin in data/inspiration_pins.json that has no description, sends the
saved image (downscaled to 1000 px with sips so a pin costs ~1.5k tokens, not 13k)
to Claude Haiku and stores the answer: whether the pin is a single PIECE he likes or
a whole LOOK, the pieces visible with their colours, and a one-line summary. This is
a record of what is in the picture, not a claim about the user, so it writes without
asking. Nothing is sent to Telegram here.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json, save_json  # noqa: E402

PINS_PATH = data_path("inspiration_pins.json")
MODEL = "claude-haiku-4-5-20251001"
MAX_PX = 1000
PROMPT = (
    "This is a pin the user saved to his Pinterest fashion board. He saves two kinds of pins: a single "
    "ITEM he likes, or a whole OUTFIT he likes. Answer in JSON only, no prose:\n"
    '{"kind": "piece" | "look", "pieces": [{"item": "<garment or accessory>", "colour": "<plain colour name>"}], '
    '"summary": "<one line, under 15 words, naming the pieces and the overall feel>"}\n'
    "Describe only what is visible. If it is a product shot of one thing, kind is piece. If it shows a "
    "person in a full outfit, kind is look. Do not guess brands."
)


def downscaled_jpeg(path: Path) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        out = Path(tmp.name)
    try:
        r = subprocess.run(["/usr/bin/sips", "-Z", str(MAX_PX), "-s", "format", "jpeg", str(path), "--out", str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:120])
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)


def describe_image(jpeg: bytes, client) -> dict:
    """One model call; returns the parsed JSON or raises ValueError."""
    resp = client.messages.create(
        model=MODEL, max_tokens=300,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
            {"type": "text", "text": PROMPT}]}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    text = text[text.find("{"): text.rfind("}") + 1]
    data = json.loads(text)
    if data.get("kind") not in ("piece", "look") or not isinstance(data.get("pieces"), list):
        raise ValueError(f"unexpected shape: {text[:120]}")
    data["summary"] = str(data.get("summary", ""))[:160]
    try:
        from usage_logger import log_usage
        log_usage("pinterest_describe.py", MODEL, resp.usage, response_id=getattr(resp, "id", None))
    except Exception:  # noqa: BLE001 - the ledger must never block the description
        pass
    return data


def describe_pending(pins: list, client, limit: int = 50, dry_run: bool = False, reader=downscaled_jpeg, source_fetch=None) -> dict:
    report = {"described": 0, "skipped_duplicates": 0, "errors": []}
    by_id = {p["pin_id"]: p for p in pins}
    for p in pins:
        if p.get("description") is not None or not p.get("file"):
            continue
        if p.get("duplicate_of") and by_id.get(p["duplicate_of"], {}).get("description") is not None:
            p["description"] = dict(by_id[p["duplicate_of"]]["description"])
            p["described_at"] = datetime.now(timezone.utc).isoformat()
            report["skipped_duplicates"] += 1
            continue
        if report["described"] >= limit:
            break
        if dry_run:
            report["described"] += 1
            continue
        try:
            p["description"] = describe_image(reader(ROOT / p["file"]), client)
            p["described_at"] = datetime.now(timezone.utc).isoformat()
            if p["description"]["kind"] == "piece" and not p.get("source_url"):
                p["source_url"] = source_link(p["link"], fetch=source_fetch)
            report["described"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad pin must not stop the rest
            report["errors"].append({"pin": p["pin_id"], "error": str(exc)[:120]})
    return report


SOURCE_RE = re.compile(r'"link":"(https?:[^"]{1,300})"')   # the page JSON escapes slashes: https:\/\/...


def source_link(pin_link: str, fetch=None) -> str:
    """The URL a pin was saved from (product page, article...), or "" when unknown."""
    try:
        fetch = fetch or (lambda u: __import__("pinterest_board_watch").fetch_bytes(u))
        m = SOURCE_RE.search(fetch(pin_link.rstrip("/") + "/").decode("utf-8", "replace"))
        return m.group(1).replace("\\/", "/").encode().decode("unicode_escape") if m else ""
    except Exception:  # noqa: BLE001
        return ""


def make_client():
    import anthropic
    from runtime_secrets import require_secret
    return anthropic.Anthropic(api_key=require_secret("ANTHROPIC_API_KEY"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true", help="count what would be described; no model call, no write")
    a = ap.parse_args(argv)
    pins = load_json(PINS_PATH, [])
    report = describe_pending(pins, None if a.dry_run else make_client(), limit=a.limit, dry_run=a.dry_run)
    if not a.dry_run:
        save_json(PINS_PATH, pins)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
