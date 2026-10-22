#!/usr/bin/env python3
"""
Post-flight capture + feedback-loop update.

Called by RULES.md when ~/.openclaw/pending_flight_log.json exists and
The user replies with two numbers (security wait, gate idle) — e.g. `log_flight.py
--reply "8 22"`. Also `--reply skip` to dismiss, and `--set-buffer N` to apply
a buffer change.

What it does with the two numbers:
  1. appends a row to flight_history.jsonl
  2. adds actual_security_min to a rolling per-airport / per-slot sample list in
     flight_learned.json and, once >= 3 samples, recomputes the p85 the
     calculator uses as its security fallback for that airport+slot
  3. tracks gate_idle_min: if the median over the last 5 flights stays above 35
     and none dipped below 5, it SUGGESTS (never applies) lowering buffer_minutes

Walk time is not learned here — the user only reports two numbers and neither is
walk. Walk stays a hand-tuned seed in travel_config.json; see SKILL.md.
"""
import os
import argparse
import json
import math
import re
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HISTORY_FILE = Path(os.path.expanduser("~/.openclaw/workspace/Skills/email-monitor/flight_history.jsonl")).expanduser()
LEARNED_FILE = Path(os.path.expanduser("~/.openclaw/workspace/Skills/email-monitor/flight_learned.json")).expanduser()
PENDING = Path(os.path.expanduser("~/.openclaw/pending_flight_log.json")).expanduser()
TG_TOKEN   = __import__("runpy").run_path(os.path.expanduser("~/.openclaw/workspace/Skills/shared/runtime_secrets.py"))["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"

SAMPLE_WINDOW = 10          # security samples kept per airport+slot
MIN_SAMPLES_TO_LEARN = 3    # before overriding the config seed
IDLE_WINDOW = 5             # flights considered for the buffer suggestion
IDLE_SUGGEST_ABOVE = 35     # median gate-idle that triggers a suggestion
IDLE_FLOOR = 5              # any flight below this cancels the suggestion cycle
BUFFER_STEP = 10            # suggested reduction
SUGGEST_COOLDOWN_DAYS = 30


def send_tg(text: str) -> None:
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            json.loads(r.read())
    except Exception as e:
        print(f"tg send error: {e}", file=sys.stderr)


def _load_learned() -> dict:
    try:
        return json.loads(LEARNED_FILE.read_text())
    except Exception:
        return {}


def _save_learned(d: dict) -> None:
    LEARNED_FILE.write_text(json.dumps(d, indent=2, sort_keys=True))


def _slot(hhmm: str) -> str:
    try:
        return "early_am" if 6 <= int(hhmm.split(":")[0]) < 9 else "default"
    except Exception:
        return "default"


def _p85(samples: list) -> int:
    vals = sorted(float(s) for s in samples)
    if len(vals) == 1:
        return max(1, math.ceil(vals[0]))
    try:
        p = statistics.quantiles(vals, n=20, method="inclusive")[16]
    except Exception:
        p = vals[-1]
    return max(1, math.ceil(p))


def _read_history() -> list:
    if not HISTORY_FILE.exists():
        return []
    rows = []
    for line in HISTORY_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _append_history(row: dict) -> None:
    with HISTORY_FILE.open("a") as f:
        f.write(json.dumps(row) + "\n")


def apply_buffer(new_val: int) -> None:
    learned = _load_learned()
    old = learned.get("buffer_minutes", 20)
    learned["buffer_minutes"] = int(new_val)
    learned.pop("buffer_suggestion", None)
    _save_learned(learned)
    print(f"buffer_minutes: {old} -> {new_val}")
    send_tg(f"Airport buffer set to <b>{new_val} min</b> (was {old}). 🎩")


def maybe_suggest_buffer(learned: dict) -> None:
    rows = _read_history()
    idles = [r["gate_idle_min"] for r in rows[-IDLE_WINDOW:] if isinstance(r.get("gate_idle_min"), (int, float))]
    if len(idles) < IDLE_WINDOW:
        return
    if min(idles) < IDLE_FLOOR:
        print(f"buffer suggestion skipped: a flight idled {min(idles)} min (< {IDLE_FLOOR}).")
        return
    med = statistics.median(idles)
    if med <= IDLE_SUGGEST_ABOVE:
        return

    cur = learned.get("buffer_minutes", 20)
    if cur <= 0:
        # Buffer is already at the floor — persistent idle now points at the
        # walk/security seeds being too high, not the buffer. Nothing to suggest.
        print("buffer suggestion skipped: buffer already at 0; check walk/security seeds.")
        return

    prev = learned.get("buffer_suggestion", {})
    if prev.get("suggested_at"):
        try:
            if datetime.now() - datetime.fromisoformat(prev["suggested_at"]) < timedelta(days=SUGGEST_COOLDOWN_DAYS):
                return
        except Exception:
            pass

    new = max(0, cur - BUFFER_STEP)
    learned["buffer_suggestion"] = {
        "suggested_at": datetime.now().isoformat(),
        "from": cur, "to": new, "median_idle": med,
    }
    _save_learned(learned)
    send_tg(
        f"Airport-timing note: your last {IDLE_WINDOW} flights you have sat a median "
        f"<b>{med:.0f} min</b> at the gate (none under {IDLE_FLOOR}). "
        f"I suggest dropping the buffer from {cur} to {new} min. "
        f"Reply <code>lower airport buffer</code> to apply, or ignore to keep it. 🎩"
    )
    print(f"buffer suggestion sent: {cur} -> {new} (median idle {med:.0f})")


def handle_reply(reply: str) -> None:
    if not PENDING.exists():
        print("No pending flight log.")
        return
    ctx = json.loads(PENDING.read_text())

    if reply.strip().lower() in {"skip", "none", "-", "n/a", "na"}:
        PENDING.unlink(missing_ok=True)
        print(f"flight log skipped for {ctx.get('key')}")
        send_tg("Skipped the flight log. 🎩")
        return

    nums = [int(n) for n in re.findall(r"\d+", reply)]
    if len(nums) < 2:
        send_tg("Need two numbers: security wait, then gate wait. e.g. <code>8 22</code>. 🎩")
        return
    security_min, gate_idle_min = nums[0], nums[1]
    if not (0 <= security_min <= 180 and 0 <= gate_idle_min <= 240):
        send_tg(
            f"Got security={security_min}, gate={gate_idle_min} — that doesn't look right. "
            f"Reply just the two minute counts, e.g. <code>8 22</code>. 🎩"
        )
        return

    dep_hhmm = ctx.get("departure_hhmm", "")
    airport = ctx.get("origin", "")
    slot = _slot(dep_hhmm)

    row = {
        "date": (ctx.get("departure", "") or "")[:10],
        "airport": airport,
        "terminal": ctx.get("terminal", ""),
        "departure": dep_hhmm,
        "slot": slot,
        "predicted_leave": ctx.get("predicted_leave_display"),
        "actual_security_min": security_min,
        "gate_idle_min": gate_idle_min,
        "logged_at": datetime.now().isoformat(),
    }
    _append_history(row)

    learned = _load_learned()
    samples_root = learned.setdefault("security_samples", {}).setdefault(airport, {})
    samples = samples_root.setdefault(slot, [])
    samples.append(security_min)
    del samples[:-SAMPLE_WINDOW]

    msg = [f"Logged {ctx.get('flight','flight')}: security {security_min} min, gate idle {gate_idle_min} min."]
    if len(samples) >= MIN_SAMPLES_TO_LEARN:
        new_p85 = _p85(samples)
        learned.setdefault("security_p85", {}).setdefault(airport, {})[slot] = new_p85
        msg.append(f"{airport} {slot} security p85 is now {new_p85} min (from {len(samples)} samples).")
    else:
        msg.append(f"{MIN_SAMPLES_TO_LEARN - len(samples)} more {airport} {slot} logs until security auto-tunes.")

    _save_learned(learned)
    maybe_suggest_buffer(_load_learned())

    PENDING.unlink(missing_ok=True)
    send_tg(" ".join(msg) + " 🎩")
    print(" ".join(msg))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reply", help='The user\'s reply text, e.g. "8 22" or "skip"')
    p.add_argument("--set-buffer", type=int, help="apply a new buffer_minutes value")
    p.add_argument("--apply-suggestion", action="store_true",
                   help="apply the pending buffer suggestion from flight_learned.json")
    args = p.parse_args()

    if args.set_buffer is not None:
        apply_buffer(args.set_buffer)
    elif args.apply_suggestion:
        s = _load_learned().get("buffer_suggestion")
        if not s:
            print("No pending buffer suggestion.")
            send_tg("No pending airport-buffer suggestion to apply. 🎩")
        else:
            apply_buffer(s["to"])
    elif args.reply is not None:
        handle_reply(args.reply)
    else:
        p.error("one of --reply / --set-buffer / --apply-suggestion is required")


if __name__ == "__main__":
    main()
