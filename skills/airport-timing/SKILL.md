---
name: airport-timing
description: "Compute exactly what time the user should leave home for a flight — worked backward from gate close using live traffic, a p85 security-wait estimate, and per-terminal walk times, tightened by his own logged history. Also: picking someone UP at the airport (when to leave, pickup calendar event). Use for 'when should I leave for the airport', 'am I going to make my flight', 'I'm picking someone up', a forwarded flight screenshot, or any mention of a flight number plus a time."
metadata:
  openclaw:
    emoji: "🛫"
    requires:
      bins: ["python3"]
---

# Airport Timing 🛫

Answers one question: **what time to walk out the door.** A number computed from
live data, not a fixed "arrive 2 hours early" rule of thumb — the user was
consistently sitting 45 minutes at the gate.

The scheduled reminders are created automatically by the email-monitor daemon
when a flight confirmation is parsed. This skill is the **on-demand** entry
point and the reference for how the number is built.

## The calculation

Everything works backward from **gate close**, not departure:

```
gate_close      = scheduled_departure - gate_close_lead   (15 min; 10 for Southwest)
security_est    = p85(live TSA wait, static per-airport fallback)
walk_time       = profile[airport][terminal]              (seed; learned from history)
arrive_terminal = gate_close - walk_time - security_est - buffer
leave_home      = arrive_terminal - drive_time - parking_time
```

- **p85, not the mean.** Mean security wait is ~5 min, the tail is ~40. p85 is
  the compromise that neither misses flights nor over-buffers.
- **`buffer_minutes` is the only risk knob.** Default 20. Everything else is
  measured. It lives in `travel_config.json`; a learned override can sit in
  `flight_learned.json`.

### Adjustments (applied after the base calc)

| Condition | Effect |
|---|---|
| Checked bag | Floor terminal arrival at departure − 60 (bag cutoff is unrecoverable) |
| International | +30 min (document check; PreCheck does not help) |
| Departure 06:00–08:59 | +10 min (PreCheck lanes open late) |
| Precipitation / below freezing at origin | +15 min |

### Fail soft

If any data source is down the calc falls back to the static default for that
term and names it in the output (`estimated_terms`, `caveats`). It never fails
to produce a number.

## Picking someone up

If the user sends a flight that isn't his, ask one line first: "Your flight, or are you
picking someone up?" (and whether they're checking a bag, if he didn't say). Then:

```bash
python3 ~/.openclaw/workspace/Skills/airport-timing/scripts/pickup_time.py \
  --flight DL100 --airport DAL --arrival "2026-09-22 14:05" --checked-bag [--passenger "Name"]
```

`--arrival` is the landing time exactly as the screenshot shows it (local to the arrival
airport). It works backward from when the passenger walks out: deplane 10 + walk, or
bag on the carousel by +20 (Delta's guarantee) + 5 to grab it; be at the airport 5 minutes
before that; leave = minus predicted traffic for that time, rounded down to 5 minutes.
It prints the plan and a `propose_command`: run that exact command. It sends the user the
draft with an Approve button; the popup reminder fires at the leave time. Tell him the
leave time, curb time and back-by time in one short message. Never re-ask what the
screenshot shows. The airport must be in `email-monitor/travel_config.json` (with its
`pickup_note`, if there is one).

## On-demand usage

**"When should I leave for my flight?" / "Am I going to make WN 1250?"**

Run the day-of script directly (it pulls live TomTom traffic + security p85):

```bash
python3 ~/.openclaw/workspace/Skills/email-monitor/scripts/travel_reminder.py \
  --flight "WN 1250" --origin DAL --dest LAX \
  --departure "2026-09-15T13:40:00-05:00" --confirmation ABC123 \
  --airport-lat 32.8481 --airport-lng -96.8512 \
  --home-lat 40.0000 --home-lng -75.0000 \
  --airport-name "Dallas Love Field"
```

Airport coordinates/names come from `travel_config.json`. For a quick estimate
without sending Telegram, `compute_leave_time.py` runs standalone with a
hardcoded input dict, and `airport_timing.py` has a `leave_time_for_flight()`
one-call helper.

## Scheduled runs (auto, per flight)

Created by `daemon.py::_schedule_flight_reminders` from a parsed confirmation:

| Job | When | What |
|---|---|---|
| `checkin-*` | T − 24h | Check-in link + Uber link + a **rough** leave estimate (low confidence) |
| `dayof-*` | T − 3h | The real leave time — live traffic + security, one time + one-line reason |
| `recheck-*` | T − 90m | Silent unless the leave time moved > 15 min from the T−3h estimate |
| `flightlog-*` | T + 45m | Prompts the user for two numbers to feed the feedback loop |

All four are cancelled and rebuilt if the flight is rescheduled (matched by
confirmation number).

## Feedback loop

After each flight, `prompt_flight_log.py` asks for **two numbers only**:
security wait, and minutes spent at the gate. RULES.md routes the reply to
`log_flight.py`, which:

- appends a row to `flight_history.jsonl`
- adds the security number to a rolling per-airport / per-slot sample list in
  `flight_learned.json`; after 3 samples it recomputes the p85 the calculator
  uses for that airport + time-of-day
- tracks gate-idle: if the median over the last 5 flights stays above 35 min
  (and none dipped below 5), it **suggests** — never applies — lowering
  `buffer_minutes` by 10. The user replies `lower airport buffer` to apply.

**Walk time is not auto-learned** — the user only reports two numbers and neither
is walk. Walk stays a hand-tuned seed under
`travel_config.json → airports.<CODE>.terminals.<ID>.walk_minutes`. Adjust it
there if a terminal consistently feels off.

## Files

| File | Role |
|---|---|
| `email-monitor/scripts/compute_leave_time.py` | Pure calculation. No I/O. `test_compute_leave_time.py` covers it. |
| `email-monitor/scripts/airport_timing.py` | Config → calculation inputs (terminal by airline, walk, p85, knobs) |
| `email-monitor/scripts/fetch_tsa.py` | Live security wait (tsawaittimes.com key required) → static fallback |
| `email-monitor/scripts/travel_reminder.py` | T−3h and T−90m runs |
| `email-monitor/scripts/prompt_flight_log.py` | T+45m two-number prompt |
| `email-monitor/scripts/log_flight.py` | Feedback-loop update |
| `email-monitor/travel_config.json` | Hand-edited: airports, terminals, walk seeds, security seeds, `buffer_minutes` |
| `email-monitor/flight_learned.json` | Machine-written: learned p85s, security samples, buffer override |
| `email-monitor/flight_history.jsonl` | One row per logged flight |

## Live TSA data

There is no free TSA wait-time API as of 2026 (the official MyTSA endpoints
were retired). `fetch_tsa.py` will use **tsawaittimes.com** if a paid
subscription key is added to `travel_config.json` as `tsawaittimes_key`;
otherwise every airport runs on the static p85 seed until enough flights are
logged. This is expected, not a failure.
