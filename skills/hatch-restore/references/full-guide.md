---
name: hatch-restore
description: Manage the Hatch Restore sound machine and wake alarm, including travel-aware schedules.
---

# Hatch Restore Skill

Controls the user's Hatch Restore (the original gen-1 device, product code
`restoreIot` — a "Restore 2/3" would be `restoreV4`/`restoreV5`, not this
one) via Hatch's unofficial cloud protocol: light color/brightness, sound
favorites, volume, clock display, and wake alarms.

## Why this shape (very different from [govee-lights](../govee-lights/SKILL.md))

Govee has a public API keyed by a single API key, with simple REST
request/response control calls. Hatch has **no public API** — the Hatch app
itself authenticates with your account email/password, gets federated AWS
Cognito credentials, and controls the device by publishing deltas to an AWS
IoT **device shadow** over a live MQTT connection. There's no "send one HTTP
request, get one HTTP response."

This skill is built on [`hatch_rest_api`](https://github.com/dahlb/hatch_rest_api),
an actively-maintained reverse-engineered Python client, but with one
complication: **alarm support only exists on that library's GitHub `main`
branch, which requires Python ≥3.12** (its `pyproject.toml` sets
`requires-python = ">=3.12.0"`). This machine's system Python is 3.9.6, and
that's what the MQTT stack (`awscrt`/`awsiotsdk`, native bindings) is
installed against — not worth a second Python install just for alarms. So:

- **`hatch_client.py`** wraps the PyPI-released `hatch_rest_api` (v1.20.4,
  installed via plain `pip install hatch-rest-api`) for everything that
  needs the live MQTT shadow connection: light, sound-via-favorite, volume,
  clock. `HatchSession` is an async context manager — login → MQTT connect →
  use `.devices` → disconnect — used by `control.py` and `status.py`.
- **`hatch_alarm_api.py`** is a from-scratch, synchronous, plain-`requests`
  client for the alarm endpoints (`service/app/routine/v3/fetch`,
  `.../v2/editMultiple`, `.../v2/dataVersion`) — these are ordinary REST
  calls, no MQTT involved at all, so there was no need to touch the
  MQTT/Python-version problem. Its payload-building logic (weekday bitmask
  packing, sunrise-lead-time math for wake-time edits) is ported line-for-line
  from `hatch_rest_api`'s `main`-branch `scheduled_routine.py`, verified live
  against the user's account on 2026-08-10. One gap: it doesn't port the
  auto-reschedule-to-next-occurrence behavior for *one-time* (`daysOfWeek==0`)
  alarms — the user's alarm is recurring, so this wasn't worth the complexity;
  see the module docstring if a one-time alarm ever gets added in the app.
- **`alarm.py`** uses `hatch_alarm_api.py` only — no MQTT session, so it's
  fast and can't collide with the more elaborate MQTT flow.

## Setup (done as of 2026-08-10 — reference for re-setup or a new device)

1. **Credentials**: Hatch has no separate API key — auth is the real Hatch
   account email/password. Stored in `openclaw.json`'s `env.vars` as
   `HATCH_EMAIL` / `HATCH_PASSWORD`, same lookup pattern as `GOVEE_API_KEY`
   (env var first, then that config path).
2. **Dependencies**: `pip install --user hatch-rest-api` (pulls in `aiohttp`,
   `awscrt`, `awsiotsdk` — all installed globally to the system Python 3.9,
   no venv, matching how other skills' packages are installed on this
   machine). `requests` was already present for `hatch_alarm_api.py`.
3. **Discover devices**: `python3 scripts/discover_devices.py` — logs in via
   plain REST (no MQTT), lists every device on the account, saves the raw
   dump to `config/raw_devices.json`.
4. **Assign nicknames**: hand-edit `config/devices.json`'s `devices` object,
   e.g. `{"devices": {"restore": {"mac": "AA:BB:CC:DD:EE:FF", "product":
   "restoreIot", "name": "The users Restore"}}}`. Currently one device,
   nickname `restore`.
5. **Verify**: `python3 scripts/status.py --device restore`.

## Mechanics

- **`control.py --device <nickname> [flags]`** — light/sound/clock control.
  Multiple flags in one call apply in sequence within a single MQTT session
  (don't chain separate invocations back-to-back — see rate limits below):
  - `--off` — light and sound both off.
  - `--color "R,G,B"` (or `"#RRGGBB"`) `[--brightness 0-100] [--white 0-100]`
    — turns the light on to that color.
  - `--sound "<name>"` — plays a sound. On this device (`restoreIot`) sounds
    are whatever's saved as a **Favorite/routine in the Hatch app** — there's
    no open sound-content library for this model, so `--sound` matches
    against `device.favorites` by name. (`hatch_client.find_favorite()`
    handles this; newer models with `set_sound()` would use a direct lookup
    instead, handled automatically via `hasattr` check.) `--sound none`
    stops it.
  - `--volume 0-100`.
  - `--clock on|off` — the always-on clock display. This model packs a
    single brightness value (`--clock-brightness`, default 50); newer
    models split day/night brightness and use a different call, also
    handled automatically.
- **`status.py --device <nickname>|all`** — reads live shadow state: online,
  light on/off + RGBW + brightness, sound playing + volume, favorite names,
  clock on/off + 24h, and an alarm count/link to `alarm.py`.
- **`alarm.py [--list | --time 9am|09:00 | --enable | --disable | --weekdays ...]`**
  — list or edit existing alarms. To change the wake time, `alarm.py --time 9am`
  is the whole command: the device and the alarm id default to
  `config/alarm_config.json` (kept current by `sync_alarm.py`), so never type
  an alarm id from memory — the id changes when the alarm is recreated in the
  app (it has changed twice already; 2026-09-20 a stale one cost three
  failed tries). `--device` / `--alarm-id` still override. With no change flag
  it only lists. **A wake-time change does three things, and prints a line for
  each:** sets the Hatch; records the time for that one morning
  (`wake_override.py` → `state/manual_wake.json`) so the 9pm sync keeps it
  instead of resetting to the default (an EARLIER calendar commitment still
  wins); and retimes what hangs off the wake time right away — the bedroom
  sunrise + morning-off lights (`shared/sync_bedroom_sunrise.py`), the
  breakfast reminder (`refresh_breakfast_anchor.py`) and the morning briefing
  (`refresh_morning_briefing.py`). Before 2026-09-20 none of
  that happened: the sync undid his time at 9pm, the lights were retimed only at
  9:15pm, so the sunrise came on at 7:00 whatever the alarm said — and the user
  unplugged the bedroom lamp every night (56 of 70 "light failures" in a month
  were that unplugged lamp). Report every line it prints, including any
  `⚠️ … NOT retimed`. `--no-follow` changes the Hatch only.
- **The lights follow the Hatch however it was set** (The user, 2026-09-20: "If I set the alarm
  to 9:30 am, the lights should adjust to that time") — including in the Hatch app, which
  nothing announces. Three reads of the device itself, all through
  `shared/sync_bedroom_sunrise.py::read_plan()`:
  1. `alarm.py` retimes the lights the moment Archibald changes the alarm.
  2. `predawn_follow_hatch.py` (cron `hatch-predawn-follow`, 3:05am, silent) retimes them to
     whatever the alarm says then — covers a wake moved EARLIER at bedtime — and records that
     time as the baseline in `state/wake_sync_state.json`.
  3. `govee-lights/scripts/sunrise_ramp.py --follow-hatch` asks the Hatch when the ramp fires:
     alarm off → no sunrise; alarm now later → the job retimes itself and exits instead of
     ramping; Hatch unreadable → ramps as scheduled. It compares CLOCK TIMES only — the date
     stored on a Hatch alarm is stale (it read "2026-09-18" on 09-20).
  The 9pm sync keeps a time he set himself for that one morning: recorded by `alarm.py`, or
  inferred because the device differs from the baseline (`wake_override.changed_on_device`).
  The pre-dawn baseline refresh is what stops a 1am "9:30 for today" being carried into
  tomorrow. Known limits: an alarm moved EARLIER in the app after 3:05am is not seen; one
  moved in the app between 3:05am and waking is kept one extra morning. Tests that touch
  `compute_and_apply` or `alarm.main()` MUST sandbox `wake_override.path` — twice on 09-20 an
  unsandboxed test wrote real state the live 9pm sync would have obeyed. `--weekdays` takes comma-separated names or
  `weekdays`/`weekends`/`everyday`. Doesn't create new alarms (not supported
  by the endpoints used). One alarm on the account
  ("Alarm_Default_Name"), **every day**, baseline wake 8:30am — see the
  automation section below, its actual nightly wake time is managed by
  `sync_alarm.py`, not fixed.
  - `--list` prints the *effective* wake time (`hatch_alarm_api.effective_wake_datetime`),
    not the raw `startTime`/`endTime` fields — this alarm is a sunrise alarm
    with a 5-minute ramp step, so the API's `startTime` is when the sunrise
    ramp *begins*, `endTime` is usually left `null`, and the actual "you're
    awake" moment is `startTime + ramp duration`. The raw stored date on
    `startTime` (e.g. "2026-08-10") is a leftover artifact from whenever it
    was last edited — for a recurring alarm (`daysOfWeek != 0`) only the
    time-of-day and the weekday bitmask matter, the date component is
    ignored by the device.

## Automation: calendar-aware wake time

**Live as of 2026-08-10, re-timed to a single 9:10pm run on 2026-08-17.**
The user wants to wake at **8:30am by default**, but **45 minutes before he has
to leave** on any day with an early commitment — golf specifically gets a
45-minute arrival buffer (he likes to warm up before tee time) instead of
the standard 5-minute buffer used for other appointments.

- **`scripts/sync_alarm.py`** (config: `config/alarm_config.json`) — looks at
  *tomorrow's* calendar (all 4 accounts, same `gog cal ls --tomorrow` call
  `fetch_tomorrow_cal.py` uses), and for every **timed** event with a
  **location** field, computes `leave_time = event_start − (drive_minutes +
  arrival_buffer)` via [`shared/drive_time.py`](../shared/drive_time.py)'s
  `leave_by_minutes()` — the exact same drive-time logic that already powers
  the user's recurring-appointment leave-by reminders, so there's one source of truth for "how long does it take to get
  there." `arrival_buffer` is `golf_arrival_buffer_minutes` (45) if "golf"
  appears in the event title, else `default_arrival_buffer_minutes` (5).
  `wake = leave_time − wake_before_leave_minutes` (45). The **earliest**
  required wake time across all qualifying events wins; if that's earlier
  than the 8:30am default, the alarm gets retimed to it via
  `hatch_alarm_api.set_alarm_wake_time`, otherwise it's reset to 8:30am.
  Events with no `location` field are skipped entirely (can't compute drive
  time) rather than guessed at. The fail-safe compute+apply+conflict-check
  logic lives in `build_result(config, tz)`, shared by the CLI (`main()`)
  and `sync_alarm_and_notify.py` below.
- **Trigger: `scripts/nightly_wake_and_workout_sync.py`, its own cron job
  (`nightly-wake-and-workout-sync-9pm`) at 9:00pm** — a plain `command`
  payload, no LLM/agentTurn involved, so this costs zero tokens beyond the
  trivial cron dispatch. Computes + applies tomorrow's wake time via
  `sync_alarm.py`'s `compute_and_apply()`, **then** books tomorrow's workout
  via `hevy-workout/scripts/schedule_workouts.py --date tomorrow` (no
  `--preferred-start` — the workout is INDEPENDENT of the wake time, see
  "Workout is NOT wake-time-derived" below), **then** re-fetches tomorrow's
  calendar and runs
  `find_back_to_back_conflicts()` now that the real workout event exists,
  and sends ONE Telegram message covering wake time + workout time + any
  conflicts (`notify()` — same self-notify pattern as the hydration nudges,
  cron delivery set to `--no-deliver` since the script handles its own send).
  - **History — three iterations to get the ordering right.** Originally ran
    twice a night (2026-08-10–17, folded into the 8pm evening briefing as
    STEP 0A) but that computed against a `--dry-run` *preview* of the
    workout, not the real event (a separate job booked the real one an hour
    later) — wrong on every workout morning. Re-timed 2026-08-17 to a single
    9:10pm run in its own script (`sync_alarm_and_notify.py`), deliberately
    *after* a separate `hevy-night-prep-9pm` job (9:00pm) that booked the
    workout at a **static** config time, so the conflict check could see the
    real event. That fixed the ordering but left the workout time itself
    disconnected from the wake time — both independently hardcoded to
    "8:30," which combined with gym transit time put the user working out 5
    minutes after waking (caught 2026-08-21, see feedback memory
    "Workout/Wake Buffer"). **Merged into this single script the same day**
    so the workout is actually *derived* from the computed wake time instead
    of being a second independent guess at the same value — wake time first
    (using tomorrow's calendar *before* the workout exists, so no
    circularity), workout second, conflict check third. `sync_alarm_and_notify.py`
    and `hevy-workout/scripts/book_tomorrow_workout.py` still exist and work
    standalone but are no longer on any cron — their old jobs
    (`hatch-alarm-sync-910pm`, `hevy-night-prep-9pm`) are disabled, not
    deleted, in case of rollback.

**Workout is NOT wake-time-derived (reverted 2026-08-28).** `book_workout()`
just runs `schedule_workouts.py --date tomorrow` — that script reads
tomorrow's calendar, tries `scheduling.preferred_start_times[weekday]`
(09:25 weekdays / 10:00 Saturday), and falls back to an earliest-free-window
search when that slot's blocked. The wake time does not enter into it.

  From 2026-08-21 to 2026-08-28 the orchestrator passed
  `--preferred-start = (wake + 60min) − gym_transit`, intending to make an
  early golf-tee-time wake pull the workout earlier too. The user killed it: on
  a day he woke early *for a meeting* (e.g. 6:00am wake for a 7:30am
  meeting), it told the booker to try ~7:00am first, and if that gap before
  the meeting was free it grabbed it — a workout he never wanted. The
  get-ready buffer (wake → leave for gym) belongs baked into the fixed
  `preferred_start_times` values, not chained to a commitment-driven wake.
  `wake_buffer_minutes` config key, `get_wake_buffer_minutes()` and
  `get_active_transit_minutes()` were all deleted. See
  [[feedback-workout-wake-buffer]].

  Still open (The user aware): `schedule_workouts.py` has no travel awareness —
  it'll book a workout at the *home-city* gym on a day the user's flying home
  (seen 2026-08-28: a 4pm home-city workout booked for a day he only lands
  back home at 7pm). Separate fix from this one — gate the booker on
  `is_away_from_home()` next time it's touched.
- **Reliability: The user wants to always be told his wake time, no
  exceptions** (2026-08-10, still holds) — `sync_alarm.py`'s `build_result()`
  never fails silently. Any error mid-sync (calendar fetch, drive-time
  lookup, Hatch login/rate-limit) is caught, falling back to
  `read_current_only()` — a plain read of whatever the alarm is *currently*
  set to, no changes — and `sync_alarm_and_notify.py` always sends a message
  either way (never conditional on success). There is no code path where
  a night passes with nothing sent.
- To change the default wake time or either buffer, edit
  `config/alarm_config.json` directly — no code change needed.
- **`scripts/refresh_breakfast_anchor.py`** (cron `breakfast-anchor-refresh-3am`,
  03:00 CT, added 2026-09-19) — the nutrition Breakfast Anchor reminder fires
  at alarm + 30 min, scheduled by the nightly sync. The user's rule is "7:30
  unless I change it", so this re-reads the **live** alarm pre-dawn
  (`sync_alarm.read_current_wake_time()`) and moves that one-shot if he
  changed the alarm in the app after the 21:00 sync. Read-only against the
  Hatch, and it never sends a message. Note the nightly sync itself still
  resets the Hatch to the computed time at 21:00 — a manual change only
  sticks if it's made after that. Tests: `test_breakfast_anchor_refresh.py`.
- **`scripts/refresh_morning_briefing.py`** (run by `alarm.py` and by
  `predawn_follow_hatch.py`, added 2026-09-27) — the morning briefing fires at
  **alarm + 15**, scheduled by the nightly sync as the one-shot
  `morning-briefing-at-wake-<date>` (the fixed `morning-briefing-daily-8am` cron is
  disabled). The user, 2026-09-27: "I just want it to fire when I actually get up as
  opposed at 8am daily. If I choose to sleep in I want it to be dynamic." It follows
  the ALARM, not him — snoozing does not move it. Unlike the breakfast anchor it is
  NOT skipped while travelling: the time is computed, never read off the Hatch, and
  `--at` carries his current timezone, so a trip in another time zone fires on local time (what a trip
  does lose is the pre-dawn re-read, since there is no Hatch to look at). Plan file:
  `Skills/morning-briefing/state/briefing_plan.json`. Tests:
  `test_morning_briefing_schedule.py`.
- **Travel: Hatch forced OFF, phone alarm brief + backup nudge instead
  (added 2026-08-28).** `compute_and_apply()` checks
  `shared/current_location.is_away_from_home()` (the same shared travel gate
  the freezer / meal-at-home reminders use — `vacation_mode.active` OR a
  non-home equipment profile set by `detect_travel.py`). The wake-time
  *computation* is unchanged whether home or away (`drive_time.py` already
  swaps its origin to whatever home base the user's near); only the **action**
  differs:
  - **home:** write the computed time to the Hatch, enabling it if off.
  - **away:** disable the Hatch (it's in an empty house — a sunrise alarm
    ramping to full volume with nobody to dismiss it is worse than useless)
    and return the computed time with `away: True`. The Hatch is
    **re-enabled automatically** on the first nightly run back home, via
    the existing `not alarm.get("enabled")` branch. This also means the
    nightly sync re-disables a Hatch alarm the user manually re-enables in the
    app while still travelling — that's intended.
  - The user's phone is his real alarm on the road (no API to set it for him).
    So `nightly_wake_and_workout_sync.py` sends a 9pm brief —
    "📱 Away — Hatch off. Set your phone alarm for 6:45 AM — 'Early
    breakfast meeting' (actually 8:00 AM), leave by 7:15 AM." — AND schedules a
    one-shot backup nudge cron (`travel-wake-nudge-<date>`) at the computed
    wake time that runs `scripts/travel_wake_nudge.py` (sends the wake
    message 3× at 4-min spacing — no ack loop, deliberately: Telegram
    callback flows have been a recurring bug source here, and this is
    already the *backup* to a real alarm). If the nudge can't be scheduled,
    The user is told *now* so he sets the phone alarm himself — never a silent
    gap.
  - **The travel message is wake-up info ONLY** (The user's call 2026-08-28) —
    `format_message()` returns early on `away OR flight_tomorrow`, with no
    workout line, no back-to-back conflict lines. The workout booker and
    conflict check still *run* in `main()` (The user: "keep booking, just hide
    it"); their output is simply not rendered on a travel day. Ordinary home
    nights are unchanged (full message).
  - **Night-before-departure gap (fixed 2026-09-01).** `is_away_from_home()`
    only flips once `detect_travel.py` has run *during* a trip — never the
    night before the user flies out — and its 5:45am cron silently no-ops when
    the Mac is off overnight for an early departure. So the 9pm sync the
    night before a 6am flight used to read as "home": it framed the ~3am
    flight wake as a plain Hatch line and skipped the backup nudge (The user got
    no phone-alarm heads-up for his early flight; the Hatch, meanwhile,
    was left blaring at 3:27am in the empty house because the sync never
    reached its disable branch — a separate stale-alarm-id bug, since
    fixed). Fix: `sync_alarm.flight_tomorrow(events)` — a flight (`O→D` in
    the title) on tomorrow's calendar makes the wake **travel-sensitive on
    its own**, independent of the away flag. When `flight_tomorrow and not
    away` (home tonight, fly out tomorrow) the Hatch is still set *and* the
    message becomes "🛫 Travel day tomorrow — Hatch set for X … Set a phone
    alarm as backup too (nudge scheduled)" *and* `schedule_travel_wake_nudge`
    fires. `schedule_workouts.py` uses the same `FLIGHT_RE`.
  - `schedule_travel_wake_nudge()` fires when `away OR flight_tomorrow`;
    no-ops on default-time days (no early commitment to be up for) or when
    the wake compute failed (no reliable time to fire at). The Monday
    weekly check-in reminder (`schedule_weekly_checkin_reminder`) is gated on
    `is_away_from_home()` too — no bathroom scale on the road.
  - **`_cancel_oneshots(prefix)`** deletes *all* matching one-shot jobs
    before re-adding, because `openclaw cron add <name>` DUPLICATES a
    same-named job rather than replacing it (verified 2026-08-28) — so
    re-running the nightly sync twice in one evening would otherwise stack
    two nudges. Same helper now backs the weekly check-in reminder (was a
    keep-name exclusion that had the same latent dup bug).
  - Tests: `test_sync_alarm.py::TestAlarmDisabledWhileAwayFromHome` +
    `::TestFlightTomorrowSignal`, `test_travel_wake_nudge.py` (incl.
    `TestAwayMessageIsWakeOnly` + the flight-day cases),
    `test_weekly_checkin_reminder.py::test_noop_when_away_from_home`.
- **Same-day back-to-back conflict check (added 2026-08-12).** Wake time
  only ever covers the *first* qualifying event of the day — it says
  nothing about whether the user can actually get from one timed, located event
  to the next. Caught live on 2026-08-12: an 8:45am meeting across town
  (ending 9:45am) followed by a 10am tee time at a course — the meeting's
  wake-time requirement (6:54am) was earlier than golf's, so golf's
  warm-up buffer was silently unreachable (needed to leave the meeting by
  9:04am, 41 min before it ended) and nothing said so.
  `find_back_to_back_conflicts()` in `sync_alarm.py` now checks every
  consecutive pair of tomorrow's timed+located events: is there enough
  time between event A ending and event B starting to drive from A's
  location to B's location (via `drive_time.py`'s `leave_by_minutes(...,
  origin_address=A's location)`) and still arrive with B's required
  buffer (golf's 45 min or the default 5 min, same rule as the wake-time
  calc)? Runs off the same `fetch_tomorrow_events()` call already made for
  the wake-time calc — no extra API cost — and independently of it,
  wrapped in its own try/except so a Hatch-side failure can't suppress a
  real conflict and vice versa. Always present in `build_result()`'s
  output as a `conflicts` array (empty when none), surfaced by
  `sync_alarm_and_notify.py` as one `⚠️ Can't make it: ...` line per
  conflict, right after the alarm line. Does not touch the calendar or
  resolve anything automatically — the user decides same evening whether to
  reschedule, shorten the buffer, or accept it.

**Downstream: bedroom Govee light synced to this alarm (added 2026-08-11).**
[`shared/sync_bedroom_sunrise.py`](../shared/sync_bedroom_sunrise.py) reads
this alarm's effective wake time + sunrise-step duration nightly (9:15pm,
after the wake+workout sync above) and retimes two Govee cron jobs: one so
the bedroom light gradually brightens in step with the Hatch's own sunrise
ramp finishing at the same moment, and `govee-bedroom-morning-off` to snap
that light off at wake + 30 min. This skill has no code that knows Govee
exists — the bridge script lives in `shared/` and does the cross-skill
reading. Full detail in
[govee-lights/SKILL.md](../govee-lights/SKILL.md)'s "Bedroom sunrise ramp"
section.
- **Was silently broken 2026-08-17 through 2026-08-21.** Two independent bugs
  compounded: (1) this job stayed at its original 8:12pm slot when the alarm
  sync moved to 9:10pm on 2026-08-17, so it read *last night's* wake time,
  not tonight's — the doc above (and the script's own docstring) still said
  "after the alarm sync" the whole time, nobody re-checked the actual cron
  times against it; (2) `alarm_config.json`'s `alarm_id` had gone stale
  (pointed at a deleted/recreated alarm), so every run was actually failing
  outright (`alarm <id> not found`), not just reading a stale value. Both
  fixed 2026-08-21 while rebuilding the wake+workout sync above — retimed
  to 9:15pm, `alarm_id` corrected. **Lesson: when retiming one cron job in a
  dependency chain, grep for every other job whose doc says "runs after
  this one" — a stale schedule reference doesn't error, it just quietly
  reads wrong data forever.**

## Known limitations

- **Rate limits are tight and undocumented.** Hit a 429 on `public/v1/login`
  after ~6 logins within a couple minutes while building/testing this — much
  stricter than Govee's documented limits. `hatch_client.py`'s `HatchSession`
  surfaces this as a clean `HatchError` ("wait a minute before retrying")
  instead of a raw `aiohttp` traceback, but the underlying `get_rest_devices`
  call leaves an unclosed `aiohttp` session on that error path (a
  `hatch_rest_api` library detail, not something worth patching around) —
  cosmetic warning noise on a rate-limit failure, not a real leak in normal
  (non-rapid-fire) usage. **Batch flags into one `control.py` call** rather
  than multiple separate invocations, and avoid running `status.py`/
  `control.py`/`discover_devices.py` back-to-back in scripts or automations.
- `hatch_alarm_api.py` doesn't support creating new alarms or the
  one-time-alarm reschedule edge case — see its module docstring.
- The alarm sync now runs once, at 9:10pm (see above) — a calendar event
  added or moved *after* 9:10pm the same night won't retrigger it until the
  next night's run, so the alarm could still be wrong for a very
  late-added early-morning event. Lower risk than the old 8pm timing since
  9:10pm is already after the two things most likely to add a morning
  commitment (the evening briefing's own review and night-prep's workout
  booking), but not zero risk. If this becomes a real problem, the fix
  would be a still-later or event-triggered re-check — but don't add a
  second nightly run to solve it; that's the exact duplication this was
  just fixed to remove.
- Golf detection is a simple case-insensitive "golf" substring match on the
  event title (`config/alarm_config.json`'s `golf_title_keywords`) — a title
  that doesn't mention golf (e.g. a course name alone) won't get the 45-min
  buffer.
