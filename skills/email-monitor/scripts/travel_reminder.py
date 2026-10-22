#!/usr/bin/env python3
"""
Day-of travel reminder — fired ~3h before departure by a scheduled cron job
(see daemon.py::_schedule_flight_reminders section 3).

This is the "real" run in the Airport Departure Timing model: traffic and
checkpoint data are actually predictive at T-3h. It pulls live TomTom traffic,
runs the backward-from-gate-close calculation (compute_leave_time.py via
airport_timing.py), and sends one Telegram message with a single leave-by time,
a one-line reason, and check-in / Uber deep links.

Security wait is the static per-airport p85 fallback until fetch_tsa.py is
wired in; when that lands, this script passes the live p85 through.

Arg surface is kept stable — daemon.py stores this exact command in the cron
job and _cancel_stale_flight_reminders matches stale jobs by `--confirmation`.
"""
import os
import argparse
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from airport_timing import leave_time_for_flight, load_cfg
from compute_leave_time import one_line_reason
from fetch_tsa import get_security_p85

TG_TOKEN   = __import__("runpy").run_path(os.path.expanduser("~/.openclaw/workspace/Skills/shared/runtime_secrets.py"))["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"

STATE_FILE = Path(os.path.expanduser("~/.openclaw/flight_timing_state.json")).expanduser()
RECHECK_THRESHOLD_MIN = 15   # T-90m re-check only pings if it moved more than this


def _flight_key(flight_number: str, dep_dt: datetime) -> str:
    return f"{flight_number.replace(' ', '')}-{dep_dt.strftime('%Y%m%d')}"


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _write_state(key: str, entry: dict) -> None:
    state = _read_state()
    state[key] = entry
    # keep the file from growing without bound
    state = dict(list(state.items())[-100:])
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"state write error: {e}", file=sys.stderr)


def get_drive_minutes(home_lat, home_lng, apt_lat, apt_lng, tomtom_key):
    url = (
        f"https://api.tomtom.com/routing/1/calculateRoute/"
        f"{home_lat},{home_lng}:{apt_lat},{apt_lng}/json"
        f"?key={tomtom_key}&traffic=true&travelMode=car"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        seconds = data["routes"][0]["summary"]["travelTimeInSeconds"]
        return max(10, math.ceil(seconds / 60))
    except Exception as e:
        print(f"TomTom error: {e}", file=sys.stderr)
        return None


def send_tg(text):
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flight",        required=True)
    parser.add_argument("--origin",        required=True)
    parser.add_argument("--dest",          required=True)
    parser.add_argument("--departure",     required=True)
    parser.add_argument("--confirmation",  default="")
    parser.add_argument("--airport-lat",   type=float, required=True)
    parser.add_argument("--airport-lng",   type=float, required=True)
    parser.add_argument("--home-lat",      type=float, default=None)
    parser.add_argument("--home-lng",      type=float, default=None)
    parser.add_argument("--airport-name",  default="")
    parser.add_argument("--recheck", action="store_true",
                        help="T-90m re-check: only send if the leave time moved more than "
                             f"{RECHECK_THRESHOLD_MIN} min from the T-3h estimate")
    parser.add_argument("--checked-bag", action="store_true",
                        help="Floor terminal arrival at departure-60 (bag cutoff is unrecoverable)")
    args = parser.parse_args()

    cfg          = load_cfg()
    tomtom_token = cfg.get("tomtom_key", "")
    fallbacks    = cfg.get("fallback_drive_minutes", {})

    airline_code = args.flight.split()[0] if " " in args.flight else args.flight[:2]
    checkin_tmpl = cfg.get("airline_checkin", {}).get(airline_code, "")
    checkin_url  = checkin_tmpl.replace("{conf}", args.confirmation) if checkin_tmpl and args.confirmation else checkin_tmpl

    dep_dt = datetime.fromisoformat(args.departure)

    drive_min = None
    if tomtom_token and args.home_lat is not None and args.home_lng is not None:
        drive_min = get_drive_minutes(
            args.home_lat, args.home_lng, args.airport_lat, args.airport_lng, tomtom_token
        )
    if drive_min is not None:
        drive_source = "live"
    else:
        drive_min    = fallbacks.get(args.origin, 30)
        drive_source = "static"

    sec_p85, sec_source = get_security_p85(cfg, args.origin, dep_dt)

    result = leave_time_for_flight(
        cfg=cfg,
        flight_number=args.flight,
        orig=args.origin,
        dest=args.dest,
        dep_dt=dep_dt,
        drive_minutes=drive_min,
        drive_source=drive_source,
        security_p85=sec_p85,
        security_source="live" if sec_source.startswith("live") else "static",
        checked_bag=args.checked_bag,
        stage="final",
    )

    leave_str = result["leave_home_display"]
    dep_str   = dep_dt.strftime("%-I:%M %p")
    date_str  = dep_dt.strftime("%a %b %-d")

    key       = _flight_key(args.flight, dep_dt)
    new_leave = datetime.fromisoformat(result["leave_home"])

    # T-90m re-check: compare against the T-3h estimate and stay quiet unless
    # traffic / checkpoint data moved the number materially.
    if args.recheck:
        prior = _read_state().get(key, {})
        prior_iso = prior.get("computed_leave")
        if prior_iso:
            delta_min = round((datetime.fromisoformat(prior_iso) - new_leave).total_seconds() / 60)
            if abs(delta_min) <= RECHECK_THRESHOLD_MIN:
                print(f"Re-check: leave time moved {delta_min:+d} min "
                      f"(<= {RECHECK_THRESHOLD_MIN}); staying quiet.")
                _write_state(key, {**prior, "recheck_leave": result["leave_home"],
                                   "recheck_at": datetime.now().isoformat()})
                return
            # delta_min = prior_leave - new_leave; positive => new time is earlier
            direction = "earlier" if delta_min > 0 else "later"
            recheck_prefix = (
                f"⚠️ <b>Plans changed</b> — leave time moved {abs(delta_min)} min {direction} "
                f"since the earlier estimate ({prior.get('leave_display', '?')})."
            )
        else:
            recheck_prefix = "⚠️ <b>Updated leave time</b> (no earlier estimate on file)."
    else:
        recheck_prefix = ""
        _write_state(key, {
            "flight": args.flight, "origin": args.origin, "dest": args.dest,
            "departure": args.departure,
            "computed_leave": result["leave_home"],
            "leave_display": leave_str,
            "reason": one_line_reason(result),
            "computed_at": datetime.now().isoformat(),
        })

    apt_label = args.airport_name or args.origin
    apt_enc   = urllib.parse.quote(apt_label)
    uber_url  = (
        f"https://m.uber.com/looking?"
        f"dropoff[formatted_address]={apt_enc}"
        f"&dropoff[latitude]={args.airport_lat}"
        f"&dropoff[longitude]={args.airport_lng}"
    )

    lines = [f"<b>{args.flight} {args.origin} → {args.dest}</b> · {date_str} {dep_str}", ""]
    if recheck_prefix:
        lines += [recheck_prefix, ""]
    lines += [
        f"Leave by <b>{leave_str}</b>",
        f"<i>{one_line_reason(result)}</i>",
    ]
    for note in result["notes"]:
        lines.append(f"• {note}")
    if result["caveats"]:
        lines.append(f"<i>{' '.join(result['caveats'])}</i>")
    lines.append("")
    if checkin_url:
        lines.append(f'<a href="{checkin_url}">✅ Check in now</a>')
    lines.append(f'<a href="{uber_url}">\U0001f697 Book Uber to {apt_label}</a>')
    lines.append("")
    lines.append("Safe travels, Master Wayne. \U0001f3a9")

    send_tg("\n".join(lines))
    print(f"Sent travel reminder: leave by {leave_str} ({drive_source} drive {drive_min}m) — {one_line_reason(result)}")


if __name__ == "__main__":
    main()
