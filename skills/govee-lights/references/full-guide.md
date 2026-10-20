---
name: govee-lights
description: Control the configured Govee lights, scenes, brightness, and schedules.
---

# Govee Lights Skill

Controls the user's Govee smart lights (on/off, brightness, RGB color, color
temperature, dynamic scenes) via the official Govee Open API, plus
cron-based automation (e.g. lights on in the evening, off at bedtime).

## Why this shape

Govee's Open API is keyed by `sku` + `device` (a MAC-address-style ID), not
by the friendly name shown in the Govee Home app — so `config/devices.json`
maps human nicknames ("office", "bedroom") to those raw IDs, and every
script takes `--light <nickname>` instead of raw IDs. `--light all` expands
to every configured device, matching the `all` convention used elsewhere
(e.g. hydration's bottle buttons).

## Setup (done as of 2026-08-10 — reference for adding a *new* light)

1. **API key**: Govee Home app → Profile → Settings → Apply for API Key
   (arrives by email). Add it to `openclaw.json` under `env.vars` as
   `GOVEE_API_KEY` — same pattern as `ALPHA_VANTAGE_API_KEY` /
   `TODOIST_API_TOKEN`. `govee_client.py` checks the `GOVEE_API_KEY` env var
   first, then falls back to that config path.
2. **Discover devices**: `python3 scripts/discover_devices.py` — lists
   every device on the account (name, sku, device ID, capabilities) and
   saves the raw dump to `config/raw_devices.json`.
3. **Assign nicknames**: hand-edit `config/devices.json`'s `devices` object,
   e.g.:
   ```json
   {"devices": {"office": {"sku": "H605C", "device": "AA:BB:CC:DD:...", "room": "office"}}}
   ```
4. **Verify**: `python3 scripts/status.py --light all`.

Currently two devices configured, both COB Strip Light sku `H61E6`:
`console_light` ("Console Light") and `bedroom` ("Bedroom", added
2026-08-11). If `control.py`/`status.py` ever errors with "no API key" or
"unknown light nickname" for a light not yet in `devices.json`, that's this
setup flow — run steps 2-3 for the new device.

## Mechanics

- **Client**: `scripts/govee_client.py` — one `GoveeClient` class wrapping
  `https://openapi.api.govee.com/router/api/v1`. Friendly methods
  (`.power`, `.brightness`, `.color_rgb`, `.color_temp`, `.scene`) plus a
  raw `.control(sku, device, type, instance, value)` escape hatch for any
  capability not yet wrapped.
- **Control**: `scripts/control.py --light <nickname|all> [--on|--off]
  [--brightness 1-100] [--color "R,G,B"|"#hex"] [--temp KELVIN]
  [--scene "Name"]`. Multiple flags in one call apply in sequence (e.g.
  `--on --brightness 40 --color "255,180,60"` powers on, then sets
  brightness, then color). `--scene` looks up the device's available scenes
  via `/device/scenes` and matches by name case-insensitively — scene IDs
  are per-device and not guessable, so this is always a live lookup, never
  hardcoded. **Since 2026-10-01 a confirmed local command stops after one
  pass** (see § Local control — the strip itself says whether it changed, which
  is what the double-apply was standing in for); cloud-only commands (scenes,
  gradient) still get both passes. **From 2026-08-12 to then, every call applied
  its full flag sequence twice** (`REPEAT_COUNT = 2`, 3s gap) per light, unconditionally — see the
  bug note above; a single control call can silently not reach the physical
  device even when the cloud API reports success, and there's no reliable
  way to detect that after the fact, so the mitigation is applying it twice
  instead of trying to verify. Adds ~3-7s per light per call; harmless for
  cron jobs, negligible for chat-driven use. Doesn't apply to
  `winddown_fade.py`/`sunrise_ramp.py`, which call `GoveeClient` directly,
  not `control.py`.
- **Status**: `scripts/status.py --light <nickname|all>` asks the strip itself
  over the LAN and falls back to the cloud; each row's `source` is `device` (the
  truth) or `cloud` (a claim). `--cloud` forces the old `/device/state` read, which
  is useful only for comparing the two.
- **Color values**: Govee's `colorRgb` capability takes a single 24-bit int
  (`r<<16 | g<<8 | b`), not a hex string or triplet — `govee_client.py`
  handles the conversion; `control.py` accepts either `"R,G,B"` or
  `"#RRGGBB"` on the command line.
- **Rate limits** (Govee-documented): device list 30/min, control 12/sec
  (2/sec/device), state 30/min/device. Normal chat-driven usage never gets
  close to these.

## On-demand modes — night light, movie (added 2026-09-30, generalised 10-01)

The user's modes that are **not on the schedule**: he asks for one, it holds until
the lights go off for the night. `night_light` was requested 2026-09-30 as "the
last light setting for my evening wind down right before it turns off, but it
stays on and turns off at 10:45 and overrides the winddown routine I already
have"; `movie` followed on 10-01, which is why the values moved into
`config/light_modes.json` — **a new mode is an entry there plus its phrases in
`ROUTING.md`, no new code.**

```
python3 scripts/evening_hold.py on --mode night_light   # (the default)
python3 scripts/evening_hold.py on --mode movie
python3 scripts/evening_hold.py on --scene "War Films"  # ANY of the strips' own scenes, held
python3 scripts/evening_hold.py on --scene Reading --lights bedroom
python3 scripts/evening_hold.py off     # end it now, those lights off, schedule resumes
python3 scripts/evening_hold.py status
```

**The user naming any scene — "war movie", "put on the Thrillers one" — is a HELD scene,
not a bare `control.py --scene`:** use `on --scene <name>`, so the 8pm winddown and
the 10pm fade stand down and it still goes off at 10:45pm. A bare scene change would
be stomped by the next scheduled step. `--lights` narrows it to one strip. Scene
names come from `control.py --list-scenes --light bedroom` (156 of them: Movie, War
Films, Thrillers, Dramas, Comedies, Documentaries, Science Fiction, Reading, Study,
Romance, Candlelight, Sleep, Night…).

- **night_light** — "night light", "wind down now", "dim it for the night",
  "last light": both lights, Sunset Gold at **6%**.
- **movie** — "movie lights", "movie mode", "watching a movie", "putting a film
  on": both lights, **Govee's own built-in `Movie` scene**. It was a flat amber
  (`255,140,60` at 12%) for an hour on 2026-10-01 until the user said "no movie lights
  on they are still the winddown" — the strips *were* on it, but a dim warm orange
  is indistinguishable from the night light's gold, so he asked for Govee's scene
  instead. A scene is cloud-only and can't be read back (see § Local control), so
  this one mode can't be confirmed the way the others are; `status.py` will keep
  reporting the last flat colour for it.

Run it, don't ask him to confirm — same as any other explicit light request.

- **What a mode sets**: `lights` (`all` / `console_light` / `bedroom`), a
  `brightness`, and one of `color` / `temp` / `scene` — the same vocabulary as
  `control.py`, applied through it (so the double-apply reliability trick comes for
  free). `night_light` instead carries `fade_step_from_end: 2`, which reads its
  brightness off `winddown_fade.BRIGHTNESS_CURVE` — the user ran it live on 2026-10-01
  at the curve's last step (2), said "a little too dark", and picked the step above
  (6); next steps up that curve are 12, 20, 30.
- **When it ends**: **10:45pm**, where the normal fade reaches off —
  `govee-evening-hold-off` switches both lights off then and clears the hold.
  Asked for *after* 10:45pm it ends at the next **1:00am** instead
  (`govee-evening-hold-sweep`), so a late night can't leave them burning till
  morning; asked for in the small hours or the morning it holds 45 min and the
  day's own schedule takes over.
- **Per-light coverage**: a hold only stands down the jobs for the lights it covers
  (`held(light)`), so a future one-light mode can't silence the other light's evening.
- **What stands down while it's on** (this is the "override" part): the 8pm
  `govee-winddown-*` jobs (via `control.py --skip-if-held`), the console curve
  *and* its post-Zoom-call restore (`console_schedule.py` checks `held()` with no
  flag — while a hold is live there is no scheduled state to apply), and the 10pm
  `winddown_fade.py` fade. A **manual** call never carries the flag, so chat
  requests ("turn the lights blue") still work mid-hold and simply override it.
- **Fail-safe**: the hold carries its own `off_at`, and an expired hold counts as
  no hold — so a missed off-run can never suppress the next day's schedule. The
  worst case is lights left dim until the next scheduled step.
- State: `state/evening_hold.json`; modes: `config/light_modes.json` (both registered
  in `shared/data_paths.py`). The script keeps its night-light name because the state
  file's registered name does.
- Tests: `python3 scripts/test_evening_hold.py` (23). Anything else that exercises the
  evening jobs must patch `held()` off, or a hold the user has on right now changes the result
  (test_skip_if_away.py's fade tests do).

## Local control — the fix for Govee's lying API (added 2026-10-01)

**`scripts/govee_lan.py`. Flat commands (power, brightness, colour, colour
temperature) go to the strips over the LAN and are then CONFIRMED against the
device; scenes, gradient and DIY stay on the cloud because the LAN API has no
commands for them. `GOVEE_NO_LAN=1` forces the cloud path.** Callers need no
changes — `GoveeClient.power/brightness/color_rgb/color_temp` try local first and
fall back to the cloud, so control.py, the sunrise ramp, the fade and the modes
all got this at once.

```
python3 scripts/govee_lan.py            # discover + print what each strip is really showing
python3 scripts/status.py --light all   # same truth, in status.py's shape
```

Why: on 2026-10-01 the user asked why his movie lights looked blue. The cloud
reported both strips at amber 12% *and* reported `console_light` as "Device is
offline. Please check the Wi-Fi connection" — while that strip was answering
every LAN question we asked it. `/device/state` echoes the last command we sent,
not the device, and `/device/control` returns "ok" for commands that never arrive.
The user: "The API for Govee is awful." (The lights were not in fact blue, but the
investigation found the API lying in both directions, which is the real bug and
the reason the August "logged amber, showed blue" incident was never explained.)

- **Setup** (done 2026-10-01, needed once per device): Govee Home app → the light
  → **LAN Control** on. Without it the strips ignore the LAN entirely and
  everything silently falls back to the cloud.
- **Protocol**: discovery is a multicast scan to `239.255.255.250:4001` with
  replies to `:4002`; commands and `devStatus` go to `<ip>:4003`. IPs are cached in
  `state/govee_lan.json` (DHCP moves them; a miss, a silent strip or a cache older
  than a day re-scans). The user's strips: `console_light` 192.168.1.20, `bedroom`
  192.168.1.16 — both H61E6, both local-capable.
- **Confirmation, not hope**: `govee_lan.apply()` sends, waits `SETTLE_SECONDS`,
  reads `devStatus` and compares only the fields asked for; one retry, then it
  returns `verified: False` and `GoveeClient` raises — a strip that disagrees is
  reported out loud instead of being reported as success. This is the root fix for
  the whole "the cloud said ok" class of bug.
- **Port 4002 is shared**: if another run holds it, `LanUnavailable` → cloud, so
  overlapping crons degrade instead of fighting.
- **macOS local-network permission** applies to the gateway's python too;
  verified 2026-10-01 under `sh -lc`, the same shape a command cron runs.
- Tests: `python3 scripts/test_govee_lan.py` (15) — no sockets opened.

## Stopping a sunrise in progress (added 2026-10-01)

**Switching the bedroom light off during the ramp now ENDS the ramp.** the user,
2026-10-01: "during the sunrise I'll say stop and it turns back on which is super
annoying" — the ramp sets absolute brightness every ~37s, so an off command was
undone by the next step. Fixed in `sunrise_ramp.py`:

```
python3 scripts/sunrise_ramp.py --light bedroom --stop   # "stop" / "I'm up" / "turn it off"
```

- **Any phrasing works.** `control.py --off` writes the same stop flag
  (`state/sunrise_stop.json`), so Archibald doesn't have to recognise the word
  "sunrise" — if the light goes off, the sunrise is over. `--stop` is just the
  direct version, and it switches the light off itself.
- **Backstop**: before every step the ramp asks the strip over the LAN whether it
  is off, which also catches the Govee app and the plug being pulled. A strip that
  can't be reached is never read as "off" — better a sunrise than a dark bedroom on
  a guess.
- **A stop is a normal ending** (exit 0, no failure alert), and the flag is cleared
  when it is consumed and again at the start of each ramp, so yesterday's stop can
  never cancel tomorrow's sunrise.
- Verified live 2026-10-01: a 90s ramp, `control.py --off` at 25s → the ramp printed
  `{"sunrise": "stopped", "at_step": 4}` and the strip was still off 20s later.
- Tests: `python3 scripts/test_sunrise_stop.py` (14).

## Automation / scheduling

**Live as of 2026-08-10; extended to `--light all` on 2026-08-11 when the
`bedroom` light was added; split back into per-light jobs later that same
day** when the user asked to be able to diverge console from bedroom on
sunset/winddown/fade-off, while keeping energize and focus (Study) shared.
Current split, all `command`-type cron jobs (not `agentTurn` — same
reasoning as [hydration](../hydration/SKILL.md): fixed light commands don't
need an LLM call in the path; delivery mode `none` so they run silently):

**Shared (`--light all`):** none anymore. `govee-focus-end` (1pm `--off`)
was the last shared job; it went bedroom-only on 2026-08-30 and was retired
entirely on 2026-08-31 (replaced by `govee-bedroom-morning-off` at wake +
30 min — see below). (`govee-morning-energize` was shared until 2026-08-12;
`govee-focus-start` was shared until 2026-08-29 — see the bug notes near the
bottom of this file for both.)

**`console_light` only:** `govee-focus-start` (11am `--on --scene "Study"`)
— rescoped 2026-08-29 at the user's request, he doesn't want the Study scene
in the bedroom.

**Per-light (`bedroom` and `console_light` each have their own job, same
values today but independently editable):**

- ~~`govee-sunset-on-bedroom` / `govee-sunset-on-console`~~ and
  ~~`govee-sunset-scheduler`~~ — **removed 2026-08-13**, the user's request to
  simplify the evening schedule. These used to turn the lights on to "White
  Light" at actual sunset (retimed daily via Open-Meteo,
  `scripts/sunset_scheduler.py`), ~14min before winddown/Sunset Gold took
  over. Now `govee-winddown-*` (below) is the sole evening on-trigger, at a
  fixed clock time instead of a sunset-anchored one.
  `scripts/sunset_scheduler.py` was deleted (no remaining caller);
  `config/schedule_config.json`'s `sunset_on_job_ids` entry was removed.
- **`govee-winddown-bedroom`**
  / **`govee-winddown-console`**
  — each `--on --brightness 50 --color "255,170,90"` ("Sunset Gold" — see
  below), fixed **`8:00pm`** for both (moved from 8:30pm 2026-08-13, same
  request that removed sunset-on). This is now where the lights turn on for
  the evening, not just where they shift color.
- **`govee-winddown-fade-off-bedroom`** (originally named
  `govee-bedtime-off` — renamed 2026-08-11 when its behavior changed from
  instant-off to a fade) / **`govee-winddown-fade-off-console`** — each fixed `0 22 * * *`
  America/Chicago (10:00pm), runs `winddown_fade.py --light
  <bedroom|console_light> --duration-seconds 2700`, which holds the
  winddown color and steps brightness down through 7 points over 45
  minutes before finally powering off — reaching fully off at 10:45pm.
  Static; never needs updating unless the fade duration or off-time
  changes. `--timeout-seconds 3300 --no-output-timeout-seconds 600` since
  the script sleeps in-process for the full 45 minutes (same pattern as the
  bedroom sunrise ramp below).

**Daily-goals routine (added 2026-08-10)**, requested as "help me be a
better version of myself" — grounded in existing anchors (8am morning
briefing, 11am-1pm work block, midnight sleep cutoff) rather than abstract
goals the user didn't specify further:

- **`govee-morning-energize`** —
  `7:55am`, `--light console_light --on --brightness 100 --temp 6500`
  (bright daylight-white), fires just before the 8am morning briefing as a
  physical "start strong" cue. **`console_light` only since 2026-08-12** —
  originally `--light all`, which included `bedroom` and stomped the
  Hatch-synced sunrise ramp below; see that bug note.
- **`govee-focus-start`** —
  `11:00am`, `--light console_light --on --scene "Study"`, for the user's daily
  11am-1pm work block. **`console_light` only since 2026-08-29** (was
  `--light all`) — the user didn't want the Study scene coming on in the
  bedroom, especially on sleep-in days.
- **`govee-bedroom-morning-off`**
  — snaps the **bedroom** light off at **wake + 30 min**, once the user is up and
  out of the room (workout is wake + 60 min). Static argv (`--light bedroom
  --off`); the schedule is rewritten nightly by
  [`shared/sync_bedroom_sunrise.py`](../shared/sync_bedroom_sunrise.py) (the
  same job that retimes the sunrise ramp), so it always tracks the current
  Hatch wake time. Job id cached in `config/schedule_config.json`'s
  `bedroom_morning_off_job_id` — update both if the job is ever recreated.
  **Replaced `govee-focus-end`** (deleted 2026-08-31): that job turned the
  bedroom off at a fixed `1:00pm`, a timing left over from when it was
  `--light all --off` at the end of the 11am-1pm focus block. Nothing about
  1pm was chosen for the bedroom, so the strip sat at 100% white for ~5.5h
  after wake for no reason (the "post-wake fade-down discussed 2026-08-29"
  rough edge — resolved by this job, though as a snap-off not a fade;
  revisit if the user wants the fade). Delay constant is `MORNING_OFF_DELAY` in
  `sync_bedroom_sunrise.py`.
- **Console afternoon/evening curve (added 2026-08-30)** — instead of the
  console going dark 1pm→8pm, it now steps down in color temperature to
  ease into the evening (circadian wind-down + kills the post-lunch dip).
  Both new jobs just run
  [`scripts/console_schedule.py`](scripts/console_schedule.py) `--skip-if-away`,
  which applies whatever that script's `CURVE` says for the current time —
  the CURVE is the single source of truth and also drives the post-Zoom-call
  restore (see the zoom section):
  - **`govee-console-afternoon`**
    — `1:00pm`, holds console at **65% / 4300K** neutral.
  - **`govee-console-warm`** —
    `4:30pm`, warms console to **55% / 3800K**, bridging to the 8pm gold.
  Brightness/temp values are first-pass guesses — tune the `CURVE` in
  `console_schedule.py` if the user says any step feels off. (Both jobs +
  `govee-focus-start` were disabled for Sun 2026-08-30 only, a rest day,
  with a Mon 6am `govee-console-daytime-reenable-oneshot` to turn them back
  on.)
- **`govee-winddown-bedroom` / `govee-winddown-console`** — **`8:00pm`** for
  both (moved from 11:15pm on 2026-08-11, then from 8:30pm to 8:00pm on
  2026-08-13 when sunset-on was removed — see above), `--on --brightness 50
  --color "255,170,90"` ("Sunset Gold" — picked 2026-08-11 after
  live-cycling several saturated-orange and subtle-white options on the real
  lights; brightness also tuned live, 15%→35%→50%→75% landed on 50%). Turns
  the lights on directly to gold from off — there's no separate "on" step
  before it anymore.
- The `govee-winddown-fade-off-*` jobs (above) pick up from here at
  10:00pm, holding this same gold color while fading brightness out,
  reaching off at 10:45pm — so the felt sequence is steady gold 8:00-10:00pm,
  then a slow 45min fade to dark. **`winddown_fade.py`'s `COLOR` constant is
  hardcoded to match `255,170,90`** — if the winddown color/brightness is
  changed again, update that constant too, there's no automatic link
  between the winddown and fade-off jobs.

Full daily sequence: **bedroom** — off (default) → Hatch-synced sunrise
ramp (time varies, see below) → holds ramp-end white briefly →
**off at wake + 30 min** (`govee-bedroom-morning-off`) → winddown/Sunset
Gold 8:00pm (per-light) → fade to off 10:00-10:45pm (per-light). **console_light** — off (default) → energize
7:55am → focus (Study) 11am → afternoon hold 65%/4300K 1pm → warm
55%/3800K 4:30pm → winddown/Sunset Gold 8:00pm → fade to off 10:00-10:45pm.
The console no longer goes dark 1-8pm (changed 2026-08-30); the bedroom
still does. 7:55-8:00am is deliberately different per light since only the
bedroom needs a wake cue. All
`--brightness`/`--temp`/`--scene` values were chosen as reasonable
defaults unless noted otherwise above — if the user finds the energize
brightness too harsh or the focus scene wrong, adjust via `openclaw cron
edit <id> --command-argv '[...]'`.

**Favorite scenes:** the user likes "Romantic" and "White Light" — reach for
these two first when he asks to "set the mood" or similar. Neither is tied
to the automated schedule anymore (sunset-on/"White Light" was removed
2026-08-13) — either is purely a manual, on-demand `control.py --scene`
call that doesn't touch any cron job and gets overwritten at the next
scheduled event (8:00pm winddown, or whatever fires next) as usual.

**Travel gate: scheduled "on" jobs skip while the user's away (added 2026-08-28).**
The user asked for the same behavior the Hatch alarm got — don't drive lights in
an empty house. `control.py` and `sunrise_ramp.py` take a `--skip-if-away`
flag: when set and `shared/current_location.is_away_from_home()` is true
(vacation mode OR a travel equipment-profile from `detect_travel.py` — the
same shared signal the freezer/meal-at-home reminders use), they print
`{"skipped": "away from home"}` and exit 0 without touching the device.
- **Flag is on the "on" cron jobs only:** `govee-morning-energize`,
  `govee-bedroom-sunrise`, `govee-winddown-bedroom`, `govee-winddown-console`,
  `govee-focus-start`, and the `zoom-light-on-*` one-shots that
  `shared/sync_zoom_lighting.py` generates.
- **Deliberately NOT on the "off" jobs:** `govee-bedroom-morning-off`
  (wake + 30 min) and `govee-winddown-fade-off-{bedroom,console}` (10pm fade
  to dark) keep running while away, so a light left on when the user left town
  still goes dark on the normal schedule. (The user's explicit choice — "skip
  on-jobs, keep off-jobs.")
- **Manual/chat control is unaffected** — those `control.py` calls don't
  pass `--skip-if-away`, so "turn on the console light" still works while
  travelling.
- **Coming home is automatic:** nothing is disabled, the jobs stay
  scheduled and simply resume running once `is_away_from_home()` clears —
  no re-enable step (unlike the Hatch alarm, which does get disabled).
- The scheduler jobs (`govee-bedroom-sunrise-scheduler`,
  `govee-zoom-lighting-scheduler`) are left ungated — retiming/creating a
  job that will itself skip is harmless. Tests:
  `scripts/test_skip_if_away.py`.

**Misfire guard on the fixed-clock morning jobs (added 2026-08-30).** On
2026-08-30 the `govee-bedroom-sunrise` cron (7:15am America/Chicago) ran at
**4:37pm** as a gateway catch-up of a "missed" occurrence, then a second
restart killed it mid-ramp — leaving the bedroom strip stuck on the ramp's
first step (10%, dim amber). Root cause is the one
[`shared/misfire_guard.py`](../shared/misfire_guard.py) already documents:
the scheduler replays "missed" jobs on startup, and a westward `--tz` shift
on travel makes an already-completed weekly/daily run look pending.
- `sunrise_ramp.py` takes `--skip-if-misfire`. **With `--expected-start
  HH:MM` (2026-09-09):** checks the wall clock against the ramp's real
  scheduled start via `misfire_guard.near_slot` (20 min early / 30 min late
  grace) — so a 4:30am golf-day wake still runs, which the old fixed
  04:00–10:00 window would have wrongly rejected. Without `--expected-start`
  it falls back to a fixed `MISFIRE_WINDOW` (03:00–11:00, widened
  2026-09-09). All print `{"skipped": "skipped misfire: …"}` and exit 0 on
  a misfire.
- **Every fixed-time light job carries its own time (2026-09-21).** The
  scheduler has no per-job "don't replay" setting (checked the gateway's
  `planStartupCatchup`: any enabled cron job whose last slot is newer than
  its last run is replayed on restart) and passes the job nothing about its
  slot — so the time is in the argv:
  - `control.py --expected-start HH:MM [--late-grace-min N]` (early grace 20
    min, late default 60). Replaced the old `--skip-if-misfire LO-HI` hour
    window, which no longer exists. Late grace per job = until the next
    step that owns that light: `govee-morning-energize` 07:55 / 180,
    `govee-focus-start` 11:00 / 120, `govee-winddown-console`/`-bedroom`
    20:00 / 120, `govee-bedroom-morning-off` wake+30 / 120.
  - `winddown_fade.py --expected-start HH:MM` (`govee-winddown-fade-off-*`,
    22:00): on time (≤30 min late) → the normal fade; later the same night
    (≤300 min, i.e. by 03:00) → **power off only, no fade** — the fade opens
    by setting 50% gold, which would relight a lamp the user already switched off;
    any other hour → nothing.
  - `govee-bedroom-morning-off`'s argv is now rewritten nightly by
    `sync_bedroom_sunrise.py` (`morning_off_argv`) along with its time — same
    whole-argv-replace rule as the sunrise job below.
  - **If you retime one of the fixed jobs, change its `--expected-start` in
    the same `cron edit`**, or its real run will be skipped as a misfire.
  - Not guarded, on purpose: `govee-console-afternoon`/`-warm` run
    `console_schedule.py`, which applies whatever the curve says for *now* —
    a replay at any hour sets the right state. Zoom one-shots: see
    `sync_zoom_lighting.py`.
- Manual/chat `control.py` calls omit the flag and are unaffected.
- Tests: `scripts/test_skip_if_away.py` (`TestControlExpectedStart`,
  `TestFadeOffExpectedStart`), `shared/test_sunrise_length.py` (`MorningOff`).

**Bedroom sunrise ramp, synced to the Hatch alarm (added 2026-08-11):**
The user asked for the `bedroom` light to gradually wake him up like the Hatch
Restore's sunrise alarm (see [hatch-restore](../hatch-restore/SKILL.md)),
rather than snapping on instantly. Two pieces, separate from the `all`-light
schedule above (this one targets `bedroom` only):

- **`scripts/sunrise_ramp.py --light bedroom --duration-seconds N`** — runs
  in-process for the full duration, stepping brightness/color through the
  13-point `CURVE` from a dim deep-amber glow (brightness 1, `RGB(60,20,5)`)
  to full daylight white (brightness 100, `RGB(255,255,255)`), evenly spaced
  in *time* across `N` seconds. **`CURVE` brightness is spaced
  ~perceptually-evenly, not linearly (2026-09-09):** the old 7-point linear
  curve (2/10/25/45/65/85/100) put its top three steps in a range the strip
  can't visually distinguish, so the light read as fully-on ~3 min before
  the ramp ended — the user: "hit the max at 7:27" for a 7:30 wake. The 13-point
  curve keeps climbing visibly through the final minutes. It has no
  knowledge of Hatch — it just runs whatever ramp it's told to.
  - **Reliability (reworked 2026-08-29 after the user: "wakeup just goes
    straight to bright white"):** these strips drop off Wi-Fi constantly,
    worst first thing in the morning after idling all night. The old code
    had a fail-safe that, on *any* step error, jumped the light straight to
    the ramp's final state — brightness 100, pure white — which is exactly
    what the user was seeing almost every morning. Now: (a) the initial
    power-on is retried for up to `RECONNECT_GRACE_SECONDS` (90s, polling
    every 10s) so a strip still rejoining the network gets its sunrise;
    (b) each step retries `STEP_RETRIES` extra times then is *skipped* —
    every step is an absolute set, so the next step that lands corrects the
    ramp; (c) the ramp **never** slams the light to full white on failure —
    if a majority of steps failed it falls back to `FALLBACK_STATE`
    (brightness 35, `RGB(255,180,90)`, a gentle warm glow) and exits
    non-zero so the cron failure alert fires. If the device is unreachable
    for the entire grace window it exits non-zero without touching the
    light at all.
- **`govee-bedroom-sunrise`** —
  the cron job that fires `sunrise_ramp.py`. Its `--cron` time and
  `--duration-seconds` argv value are rewritten every night by
  [`shared/sync_bedroom_sunrise.py`](../shared/sync_bedroom_sunrise.py),
  which logs into Hatch, reads the sunrise alarm's *effective wake time* and
  ramp duration (`hatch_alarm_api.effective_wake_datetime` /
  `_alarm_sunrise_duration_seconds`), and sets `ramp_start = wake_time −
  sunrise length` — so the Govee ramp always finishes exactly when Hatch wakes
  The user, whatever that time turns out to be that night. **Sunrise length =
  `sunrise_minutes` in `config/schedule_config.json` — 7.5 min, the user's choice
  2026-09-21** ("the lights woke me up too early"). It was a 30-min code floor
  (`MIN_RAMP_SECONDS`, 2026-09-09) until the first morning it actually ran; before
  that the lamp was unplugged most nights. It is now exactly his setting, no
  longer `max(Hatch's own sunrise, floor)`. To change it, edit that one number;
  it applies at the next retime. The cron minute is whole-minute, so a 7:30
  wake starts at 7:22 and ends ~30 s before the Hatch. The sync re-adds
  **`--skip-if-away`, `--skip-if-misfire --expected-start HH:MM` and
  `--follow-hatch`** to the rewritten argv every night (the nightly
  `cron edit` replaces the whole argv, so anything not re-listed is
  dropped — this is how the misfire guard silently vanished before
  2026-09-09), and sets `--timeout-seconds` to `ramp_duration +
  RAMP_TIMEOUT_MARGIN_SECONDS` (600) = **2400s** for the 30-min ramp. Its
  job ID is cached in `config/schedule_config.json`'s
  `bedroom_sunrise_job_id` — update both places if this job is ever
  deleted/recreated. `--no-output-timeout-seconds` stays 900 (steps print
  ~every 150s regardless of total duration, well under it).
- **`govee-bedroom-sunrise-scheduler`** — runs
  `shared/sync_bedroom_sunrise.py` at 8:12pm daily, 12 minutes after the
  `evening-accountability-8pm` briefing computes and
  applies *tomorrow's* Hatch wake time (see hatch-restore's `sync_alarm.py`)
  — must run after that, not before, or it'd sync against a stale wake
  time. Deliberately a separate cron job rather than folded into the
  evening briefing itself, to keep govee-lights and hatch-restore decoupled
  (see the bridge script's module docstring).
- **Why `bedroom` only, not `all`:** the sunrise wake-up is specifically
  about *waking the user up in bed* — `console_light` isn't in the bedroom, so
  syncing it to the alarm wouldn't make sense. If a light is ever moved
  into the bedroom or a second bedroom light is added, extend
  `sunrise_ramp.py`'s `--light` target (or its device lookup) rather than
  switching this to `all`.

For any *new* recurring light routine, create a `command`-type cron job the
same way, e.g.:

```
openclaw cron add --name "<name>" --cron "<expr>" --tz America/Chicago \
  --command-argv '["python3","~/.openclaw/workspace/Skills/govee-lights/scripts/control.py","--light","<nickname>","--off"]' \
  --session isolated --channel telegram --to 1000000001 --best-effort-deliver --no-deliver
```

Always pass `--channel telegram --to 1000000001` explicitly — `--channel
last` fails closed and silently logs every run as an error.

## Bug fixed 2026-08-12: winddown fade finished ~5.5min early

The user noticed the light schedule "didn't feel right." Diagnosis: comparing
`winddown_fade.py`'s actual run duration (`openclaw cron runs`, last
`lastDurationMs` ≈ 2,369,700ms) against its configured `--duration-seconds
2700` (45min) showed it finishing in ~39.5min, not 45. Root cause: the loop
labeled power-off as "step 8 of 8" and divided the duration by 8 accordingly,
but only ever slept *between* the 7 brightness steps — the final sleep before
executing step 8 (power-off) was missing, so power-off fired immediately
after step 7's wait instead of a full interval later. Net effect: the fade
reliably cut off 1/8 of its intended duration and snapped to off right after
the dimmest (brightness 2) step instead of a final gradual interval.
Verified against `sunrise_ramp.py` (no such bug — its loop correctly omits
the wait only after the *last* point, since nothing follows it) and fixed by
adding the missing final `time.sleep(interval)` before the power-off block.
Verified with a synthetic short-duration run + wall-clock timing after the
fix; the two `govee-winddown-fade-off-*` cron jobs need no changes since the
fix is inside the script they already call with the same `--duration-seconds
2700` argument.

## Bug fixed 2026-08-12: morning-energize stomped the bedroom sunrise ramp

The user reported that the bedroom and console lights weren't matching the Hatch
that morning — the bedroom snapped straight to full brightness instead of
gradually ramping. Root cause: `govee-morning-energize` fires at a **fixed
7:55am** every day with `--light all --on --brightness 100 --temp 6500` —
an instant snap-on, not a fade — and `all` included `bedroom`. That's 30+
minutes before the Hatch-synced ramp (`govee-bedroom-sunrise`, dynamically
retimed nightly to `wake_time − ramp_duration`, e.g. 8:25-8:30am for a
default 8:30am wake) even starts. So the bedroom light was already blasted
to full white by the time the "gradual sunrise" job ran — the ramp then
executed against a light that had nothing left to ramp toward. This is a
pre-existing conflict: `govee-morning-energize` predates the sunrise-ramp
feature (added 2026-08-11) and was never re-scoped to exclude `bedroom` once
the ramp took over ownership of bedroom mornings — the "Shared" list above
and the "Bedroom sunrise ramp" section below had been contradicting each
other since 2026-08-11. Fixed by re-scoping `govee-morning-energize` to
`--light console_light` only (`openclaw cron edit
<job-id> --command-argv [...]`), so the bedroom
is exclusively owned by the Hatch-synced ramp in the morning. Only
manifests on normal-wake days (7:55am earlier than ramp start); an
early-golf wake time (ramp finishing well before 7:55am) wouldn't have hit
this, since the ramp would've already completed by the time energize fired.

## Bug fixed 2026-08-12: console stuck on stale color despite cron reporting "ok"

The user noticed the console light was a different color than the bedroom light
during the 8-10pm winddown window, even though both `govee-winddown-*` cron
jobs fired at 8:00pm with identical `--on --brightness 50 --color
255,170,90` argv and both were logged `ok`. Diagnosis: queried
`/device/state` live for both lights — it reported bedroom as **off**
(`powerSwitch: 0`) even though the user confirmed the bedroom was visibly on and
correctly gold, proving the state API can be stale/unreliable and isn't
trustworthy for visually confirming a light's real color, not just for
dynamic scenes (see "Known limitations" below — this extends that caveat to
basic on/off + color too). Since the API can't be trusted to diagnose which
capability is actually stuck, the fix was a full power-cycle rather than a
targeted re-send: `control.py --light console_light --off` followed by
`--on --brightness 50 --color 255,170,90`, which forces the device to fully
reinitialize rather than layering a color command on top of whatever state
it was actually stuck in (suspected cause: an earlier scene/color command
not fully overridden by the plain `colorRgb` set in the 8pm job, though this
wasn't confirmed since the state API couldn't be trusted to show the "before"
state either). The user confirmed visually after the power-cycle that console
matched bedroom's gold. If this recurs, power-cycling the affected light is
the fix, not just re-sending the color — and don't trust `status.py`'s
output as proof the fix worked; ask the user to confirm visually.

The user was frustrated that this needed a manual diagnosis at all — he just
wants the schedule to work without him having to notice and report a
mismatch. Since the state API can't be used to verify delivery, the only
real lever is reducing the odds of a dropped command in the first place:
`control.py` now applies every command twice by default (see Mechanics
above). This is a mitigation, not a confirmed fix — the true root cause
of the dropped/stuck command was never identified — so it's still worth
spot-checking the lights occasionally rather than assuming this is
airtight.

## Bug fixed 2026-08-14: winddown fade silently killed every night, light never powered off

The user reported the lights "don't follow the correct schedule, turn on and off
randomly" and that he'd taken to unplugging them when they stayed on
overnight. Diagnosis: `openclaw cron get` on both `govee-winddown-fade-off-*`
jobs showed `consecutiveErrors: 5` (console) / `4` (bedroom), each failing
with `"command produced no output before noOutputTimeoutSeconds"` /
`errorReason: "timeout"` / SIGTERM, `lastDurationMs` ≈ 44min against a
45min (`--duration-seconds 2700`) job. Root cause: the 2026-08-12 fix below
("fade finished ~5.5min early") added a final `time.sleep(interval)` after
the brightness-stepping loop, but that loop's *own* last iteration already
ends with a `time.sleep(interval)` — so after printing the "step 7 of 8"
line, the script goes silent for **two consecutive intervals** (~675s for a
2700s duration) before printing "step 8 of 8, power off". The cron job's
`noOutputTimeoutSeconds` is 600 — so the process gets SIGTERM'd ~600s after
the step-7 print, every single night, before the power-off call ever runs.
Net effect: the light never actually powers off via this job — it's left
stuck at brightness 2 (or wherever the fade was interrupted), still on, all
night. Fixed by adding a heartbeat print (`{"waiting": "final power-off"}`)
between the two sleeps, splitting the silent gap in half (~340s, safely
under the 600s watchdog) without changing the fade's actual timing. Verified
with a synthetic `--duration-seconds 8` run — heartbeat printed, power-off
step reached, exited 0.

This is very likely the root cause of the broader "lights are unreliable"
complaint, not just a fade-off glitch: a light stuck on overnight prompts
The user to physically unplug it, which is what produced the separate
`"Device is offline"` errors seen on `govee-morning-energize` /
`govee-focus-start` / `govee-bedroom-sunrise` on 2026-08-13/14 — those
aren't Wi-Fi flakiness, they're the device genuinely unplugged. No retry
logic was added for that (a retry can't help a truly unplugged device);
the fix here should remove the need to unplug in the first place. Worth
The user confirming over the next few nights that the lights actually go dark
around 10:45pm and stay that way.

**Zoom-call backlighting (added 2026-08-26):** the user asked for the
`console_light` (positioned behind him, i.e. a rim/background accent, not a
key light — see the on-call discussion) to automatically light up for
scheduled Zoom calls, to improve video-call production quality without a
manual step. Two pieces:

- **[`shared/sync_zoom_lighting.py`](../shared/sync_zoom_lighting.py)** —
  scans the next 2 days of calendar events (`gog cal ls`, same four
  calendars as `fetch_tomorrow_cal.py`) for ones whose description/location
  contains `zoom.us` (Google Meet and other video links are deliberately
  not matched — the user asked specifically about Zoom), then creates two
  one-shot cron jobs per matched event via `openclaw cron add --at
  ... --delete-after-run`: `zoom-light-on-<eventId>` (3 min before start,
  clamped forward if the meeting is closer than that) turns
  `console_light` on at **brightness 20 / 3500K** soft warm-neutral white
  (lowered from 30 / 4000K on 2026-08-29 — the strip head is partly in
  The user's camera frame, so 30% bloomed and pulled his webcam auto-exposure;
  20 / 3500K reads as ambient glow, not a light source, and stays close to
  room-light temperature so it doesn't grey his skin tone) — and
  `zoom-light-off-<eventId>` (at the event's end time) turns it back off.
  Idempotent via `shared/zoom_lighting_state.json` (seen event IDs, trimmed
  to the most recent 200), so re-running doesn't create duplicate cron
  jobs for the same event.
- **`govee-zoom-lighting-scheduler`** — runs the sync script every 30
  minutes (`*/30 * * * *`), so a Zoom meeting added to the calendar
  same-day still gets picked up with enough lead time.

**Post-call restore (fixed 2026-08-30):** the `zoom-light-off-*` job no
longer hard-powers the console off at a call's end — it runs
[`scripts/console_schedule.py`](scripts/console_schedule.py), which
re-applies whatever the daytime curve wants for that moment (focus scene,
afternoon hold, evening gold, or off). So a call ending at 2pm returns the
desk to the 4300K hold instead of going dark until the next scheduled
step. `console_schedule.py`'s `CURVE` is the single source of truth for the
console's day and MUST stay in sync with the console jobs that still carry
explicit argv (energize / focus-start / winddown-console / fade-off) —
noted in its docstring.

## Known limitations

- `/device/state` does not reflect live color/animation for dynamic scenes
  (`--scene`) — it only reports the last static `colorRgb`/`colorTemperatureK`
  value, so `status.py` can't confirm what a scene actually looks like.
  Verify scenes visually, not via `status.py`.
- More broadly (see 2026-08-12 bug fix below): `/device/state` can report
  stale power/color values even for plain static commands, not just scenes —
  it reported `bedroom` as off while it was visibly on and correctly
  colored. Never treat `status.py` output as proof a command took effect;
  when diagnosing a "light looks wrong" report, ask the user to confirm
  visually rather than trusting the state API either way.
- Scenes and color temperature ranges are device-specific (not every Govee
  model supports every capability); `discover_devices.py`'s capability list
  per device is the source of truth for what a given light actually
  supports — don't assume a light supports scenes/color/temp without
  checking.
