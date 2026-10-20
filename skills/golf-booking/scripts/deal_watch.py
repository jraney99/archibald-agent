#!/usr/bin/env python3
"""Find a really good deal at an approved course on a day his calendar has room, and
propose it. Never books.

The user, 2026-10-12: "Right now I can get out at Oak Park for free at dark so I am doing that.
The idea with this would be if it finds a really good deal and my calendar has bandwidth,
it would be to book it."

    python3 deal_watch.py            # scan; send at most one card if watch.enabled
    python3 deal_watch.py --dry-run  # scan and print what it WOULD send; sends nothing

What counts (config/approved_courses.json -> watch, and deal_price per course):
  deal       a GolfNow Hot Deal, or price <= the course's deal_price
  window     weekend 07:00-11:00 / weekday 15:00-18:00 tee times (his to adjust)
  bandwidth  the calendar is clear from block_before (60 min) to block_after (5 h) around
             the tee time, all three calendars, timed events; no multi-day all-day block
             (= a trip); not away from home for a slot within a day; and for TODAY,
             sunset-golf's dinner rule (an hour of give; breakfast never moved)
  notice     at least min_hours_notice before the tee time
  cap        max_cards_per_day cards; the same slot (course, date, rate id) never twice;
             nothing while a proposal is already waiting

Cheapest relative to the course's usual price wins when several qualify. The card is the
one propose_booking.py sends, with a "Deal:" line; his ✅ runs confirm_booking.py.

Quiet by design: when nothing qualifies, it prints a one-line summary to stdout for the
cron log and sends nothing. The crons are created DISABLED and watch.enabled is false until
The user has approved the exact card and trigger (rollout gate).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SKILLS / "shared"))
sys.path.insert(0, str(SKILLS / "sunset-golf" / "scripts"))
import courses as course_list  # noqa: E402
import golfnow  # noqa: E402
import propose_booking as pb  # noqa: E402
from data_paths import path  # noqa: E402


def state_file() -> Path:
    return path("deal_watch_state.json")


def load_state() -> dict:
    p = state_file()
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"seen": {}, "sent": {}}


def save_state(state: dict) -> None:
    p = state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def slot_key(course: dict, slot: dict) -> str:
    return f"{course['id']}|{slot['time'].strftime('%Y-%m-%d')}|{slot['rate_id']}"


def is_deal(course: dict, slot: dict) -> str:
    """The reason this slot is a deal, or '' if it is not one."""
    if slot.get("restricted"):
        return ""
    ceiling = course.get("deal_price")
    usual = course.get("usual_price")
    if slot["hot"]:
        return f"GolfNow hot deal, ${slot['price']:.0f} vs the usual ${usual:.0f}" if usual else "GolfNow hot deal"
    if ceiling is not None and slot["price"] <= ceiling:
        return f"${slot['price']:.0f} vs the usual ${usual:.0f}" if usual else f"${slot['price']:.0f}, under your ${ceiling:.0f} deal price"
    return ""


def in_window(slot: dict, windows: dict) -> bool:
    kind = "weekend" if slot["time"].weekday() >= 5 else "weekday"
    w = windows.get(kind) or {}
    if not w:
        return False
    return golfnow.within(slot, w["earliest"], w["latest"])


def bandwidth_blockers(slot: dict, events: list, cfg: dict, now: datetime, away: bool) -> list:
    """Every reason the calendar does NOT have room for this slot. Empty = it does."""
    import sunset_golf as sg
    from current_location import get_tz
    b = cfg["watch"]["bandwidth"]
    # GolfNow's clock times are naive course-local; calendar events and the meal plan carry
    # offsets. Everything is Dallas time, so the window is made aware before comparing.
    tee = slot["time"].replace(tzinfo=get_tz())
    win = {"leave_by": tee - timedelta(minutes=int(b["block_before_minutes"])),
           "home_by": tee + timedelta(minutes=int(b["block_after_minutes"]))}
    reasons = []
    clashes = sg.clashing_events(win, events)
    if clashes:
        reasons.append("calendar: " + "; ".join(clashes))
    trips = sg.multi_day_blocks(events)
    if trips:
        reasons.append("away: " + "; ".join(trips))
    if away and slot["time"].date() <= now.date() + timedelta(days=1):
        reasons.append("away from home right now")
    if slot["time"].date() == now.date():
        dinner = sg.dinner_plan(win, slot["time"].date(), {"dinner_slack_minutes": 60})
        if not dinner["fits"]:
            reasons.append(f"dinner won't fit ({dinner['planned'].strftime('%-I:%M %p')})")
    return reasons


def _events_for(day: date, cache: dict, read_events):
    if day not in cache:
        try:
            cache[day] = read_events(day)
        except Exception as exc:  # noqa: BLE001
            cache[day] = exc
    return cache[day]


def scan(cfg: dict, now: datetime, state: dict, fetch=None, read_events=None, away=None) -> dict:
    """{"candidates": [...], "rejected": [...], "pick": candidate|None}. Pure given its
    injected readers; the cron wrapper decides whether to send."""
    import sunset_golf as sg
    # Late-bound on purpose: a `fetch=golfnow.fetch` default is frozen at import, so a test
    # that patches golfnow.fetch would still hit the network through main() (it did, 2026-10-12).
    fetch = fetch or golfnow.fetch
    read_events = read_events or sg._calendar_events
    if away is None:
        from current_location import is_away_from_home
        try:
            away = is_away_from_home()
        except Exception:  # noqa: BLE001
            away = False
    w = cfg["watch"]
    players = int(w.get("players") or cfg.get("defaults", {}).get("players", 1))
    home = cfg.get("home", {})
    notice = timedelta(hours=float(w.get("min_hours_notice", 3)))
    candidates, rejected = [], []
    cal_cache = {}
    for offset in range(0, int(w.get("days_ahead", 7)) + 1):
        day = now.date() + timedelta(days=offset)
        for course in course_list.approved(cfg):
            if not course_list.bookable(course):
                continue
            try:
                slots = golfnow.slots(fetch(course["facility_id"], day, players, home.get("lat", 32.78), home.get("lon", -96.80)))
            except golfnow.ProviderError as exc:
                rejected.append({"course": course["short"], "day": day.isoformat(), "why": str(exc)[:120]})
                continue
            for slot in slots:
                why = is_deal(course, slot)
                if not why:
                    continue
                item = {"course": course, "slot": slot, "deal": why, "key": slot_key(course, slot)}
                if not in_window(slot, w.get("windows", {})):
                    rejected.append({**_brief(item), "why": "outside the tee-time window"})
                    continue
                if slot["time"] - now < notice:
                    rejected.append({**_brief(item), "why": "too little notice"})
                    continue
                if item["key"] in state.get("seen", {}):
                    rejected.append({**_brief(item), "why": "already proposed"})
                    continue
                events = _events_for(day, cal_cache, read_events)
                if isinstance(events, Exception):
                    rejected.append({**_brief(item), "why": f"calendar unreadable ({str(events)[:80]})"})
                    continue
                blockers = bandwidth_blockers(slot, events, cfg, now, away)
                if blockers:
                    rejected.append({**_brief(item), "why": "; ".join(blockers)})
                    continue
                candidates.append(item)
    for c in candidates:
        usual = c["course"].get("usual_price") or c["slot"]["price"]
        c["ratio"] = c["slot"]["price"] / usual if usual else 1.0
    candidates.sort(key=lambda c: (c["ratio"], c["slot"]["time"]))
    return {"candidates": candidates, "rejected": rejected, "pick": candidates[0] if candidates else None}


def _brief(item: dict) -> dict:
    return {"course": item["course"]["short"], "when": item["slot"]["time"].strftime("%a %b %-d %-I:%M %p"),
            "price": item["slot"]["price"], "deal": item["deal"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent; send nothing, record nothing")
    ap.add_argument("--now", default="", help="pretend it is this ISO datetime (tests / rehearsal)")
    a = ap.parse_args(argv)
    cfg = course_list.load()
    now = datetime.fromisoformat(a.now) if a.now else datetime.now()
    state = load_state()
    result = scan(cfg, now, state)
    pick = result["pick"]
    today = now.date().isoformat()
    sent_today = int(state.get("sent", {}).get(today, 0))
    live = bool(cfg["watch"].get("enabled")) and not a.dry_run

    summary = {"scanned_at": now.isoformat(timespec="minutes"), "candidates": [_brief(c) for c in result["candidates"]],
               "rejected": result["rejected"][:40], "sent_today": sent_today, "live": live}
    if not pick:
        print(json.dumps({**summary, "outcome": "nothing qualifies"}))
        return 0
    if sent_today >= int(cfg["watch"].get("max_cards_per_day", 1)):
        print(json.dumps({**summary, "outcome": "cap reached today, card not sent"}))
        return 0
    if pb.pending_file().exists():
        print(json.dumps({**summary, "outcome": "a proposal is already waiting, card not sent"}))
        return 0

    course, slot = pick["course"], pick["slot"]
    pending, text = pb.build(cfg, course["id"], datetime.combine(slot["time"].date(), datetime.min.time()),
                             slot["time_label"], int(cfg["watch"].get("players") or 1), "", deal_line="Deal: " + pick["deal"])
    if not live:
        print(json.dumps({**summary, "outcome": "would send (dry run / watch disabled)", "card": text}, indent=1))
        return 0
    try:
        receipt = pb.propose(pending, text)
    except RuntimeError as exc:
        print(json.dumps({**summary, "outcome": f"send failed: {exc}"}))
        return 1
    state.setdefault("seen", {})[pick["key"]] = now.isoformat(timespec="minutes")
    state.setdefault("sent", {})[today] = sent_today + 1
    save_state(state)
    print(json.dumps({**summary, "outcome": "card sent", "receipt": receipt}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
