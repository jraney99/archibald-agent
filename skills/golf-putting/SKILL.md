---
name: golf-putting
description: The user's nightly putting habit (goal in config/putting_config.json, 25 since 2026-10-03) — the bedtime cue, the one-tap log, and the streak.
---

# Golf Putting

A habit tracker, not a performance log. The user set the goal on 2026-09-27 at 50
putts every night before sleep and lowered it to 25 on 2026-10-03 (the number lives in
`config/putting_config.json` → `nightly_goal_putts`; every message reads it from there), and what he asked for was help doing it
*consistently* — so this skill answers one question a night ("did you?") and
nothing else. Makes/misses, distances and stroke quality are deliberately not
tracked; see **If he wants performance data** below.

## Why this shape

Nothing about the habit was hard except remembering it at 9:45pm and noticing
when a week had slipped. So: one message a night with one tap, and one line in
the morning briefing. Every deliberate choice here is the user's, made 2026-09-27:

- **Its own cue at 9:45pm**, not bolted onto the 9pm energy check-in — two
  unrelated buttons on one message is how taps get mis-attributed, and 9pm is
  early for a pre-bed habit. 9:45pm sits in the gap between that check-in and
  the 10:00pm lights fade-off, on a `:45` mark so its notification can't be
  visually coalesced with another job's (the failure that broke the freezer
  reminder — see `hydration/SKILL.md`).
- **Binary, not a count.** Done or not done. Hydration learned this the hard
  way: fractional quick-add buttons were "hard to track" and killed the logging
  habit, so it went to one tap per bottle. `--putts N` exists for honesty when
  he says he only got through 30, but no button asks for a number.
- **Travel nights are stepped over.** `is_away_from_home()` suppresses the cue
  and the night is logged `away`, so a trip neither breaks the streak nor
  inflates it. The entry is the point: without it nothing later could tell a
  travel night from a night he blew off.

## Mechanics

- **Cue:** `scripts/send_putting_cue.py`, fired by the `putting-nightly-cue`
  cron as a `command` payload with `delivery.mode: "none"` — it sends its own
  Telegram message. No agent turn in the path that could reword it, time out,
  or double-send (same reasoning as the hydration nudge). `--dry-run` prints the
  exact text and sends nothing.
- **Message:** `⛳ 25 putts before bed, Master Wayne.` (the configured goal) plus `🔥 N nights running.`
  once there is a streak, with `✅ Done` / `Not tonight` buttons. No streak, no
  second line — the message never invents pressure that isn't there.
- **Buttons:** `confirm:putting` → `log_putting.py --done`, `skip:putting` →
  `log_putting.py --skip`. Both edit the cue in place and print `NO_REPLY`, so a
  tap never produces a second message. A cue from an earlier night is never
  edited — `cue_message_id()` checks the stored night first.
- **Log:** `data/putting_log.json`, flat and append-only, one entry per night:
  `{"night_of", "status": done|skipped|away, "timestamp", "putts"}`. Written only
  through `putting_status.append_entry()`, which holds an exclusive lock —
  the callback plugin and a redundant agent turn can process one tap
  simultaneously, and an unlocked read-modify-write of this file corrupts it
  (proven in hydration).
- **One message in the chat:** each cue retires the previous night's via
  `chat_cleanup.replace_stream("putting:nightly", …)` — archived first, then
  deleted, and only after the new one is up.

## A "night" is not a calendar day

`putting_status.night_of()` treats anything before 4am as the previous night, so
a session logged at 12:30am counts for the night he actually putted. Without
this, a late session reads as two misses: one for the night he did it and one
for the next day. The cutoff is `night_rollover_hour` in the config.

## The streak

`putting_status.streak_nights(entries, through)` walks back from a night:
`done` counts, `away` is stepped over, and anything else — `skipped` or no entry
at all — stops the walk. Bounded by the log's earliest night, so an all-travel
log can't loop forever.

## Briefing integration

`run_morning_briefing.py` → `get_putting_brief()` inlines one line:
`⛳ Putting: 25 putts last night ✅ — 7 nights running` (the count logged that night), or on a miss
`⛳ Putting: nothing logged last night — streak was 6, last done Thu Sep 24`.
Silent (prints nothing, so the briefing grows no section) before the first
session is ever logged, and on travel nights.

## On demand

`python3 Skills/golf-putting/scripts/putting_status.py` prints tonight's status,
the streak and the last night he putted, as JSON — use it when he asks "what's my
putting streak."

## Changing it

- Goal, cue time, rollover hour, travel behaviour: `config/putting_config.json`.
- Moving the cue time means editing **both** the config (the misfire guard reads
  its slot from there) and the cron expression, or the guard will reject the run
  as a misfire. Keep it off other jobs' exact minute marks.

## If he wants performance data

Makes out of the nightly goal, distance, or a trend belongs in `arccos-golf` (which already
holds real round data), not here — this log has no room for it by design. Adding
it means a second tap per night, which is the thing most likely to kill the
habit, so raise the cost before building it.

## Tests

`python3 scripts/test_putting.py` — 33 tests: the after-midnight boundary, the
streak rules (including travel), the locked append and its de-dup, the cue
(wording, misfire, travel, already-logged, dry-run) and both buttons. The log is
redirected to a temp file, so the tests never read or write the live one.
