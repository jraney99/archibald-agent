---
name: chilipad
description: Control the ChiliPad Dock Pro bed cooler — temperature on demand, a warm-entry-then-descend night that follows the user's lights and alarm, and the trial that finds his sleeping temperature.
---

# ChiliPad Skill

Controls the ChiliPad Dock Pro under the user's mattress through sleep.me's public
developer API: on/off, temperature, live status, and a night that starts warm and
walks down, placed inside whatever window his bedroom lights and his Hatch alarm
actually describe.

**Status as of 2026-10-06: LIVE.** Token installed, device discovered
(`The user's Bed`), read and write both verified against
the real unit, all five cron jobs enabled. First real night: 2026-10-06.

## Why this shape (much simpler than [hatch-restore](../hatch-restore/SKILL.md))

The Hatch has no public API, so that skill authenticates as the app, gets
federated AWS Cognito credentials, and publishes deltas to an IoT device shadow
over live MQTT. The ChiliPad needs none of that. sleep.me publishes a real REST
API for its Wi-Fi units and hands out a bearer token from the account portal, so
`sleepme_client.py` is plain `requests`: one request in, one response out. It is
closer to [govee-lights](../govee-lights/SKILL.md) than to the Hatch.

Which unit matters. The Dock Pro and ChiliPad Pro are Wi-Fi and have this API.
The older **OOLER is Bluetooth-only** and the classic **Cube has no connectivity
at all** — neither is reachable this way, and if the unit is ever replaced with
one of those, none of this works and the skill should be retired rather than
patched.

### Two traps in the API, both already handled

- **999 and -1 are not temperatures.** The API uses `set_temperature_f: 999` to
  mean "max heat" and `-1` to mean "max cool". Printing or doing arithmetic on
  them is exactly the bug the Home Assistant integration shipped (its issue #50
  showed users 1830°F). Everything here goes through
  `sleepme_client.describe_temp()`, which renders them as words, and
  `clamp_f()`/`clamp_c()`, which keep real setpoints inside 55–115°F /
  13.0–48.0°C. Never format a raw `set_temperature_*` by hand.
- **The rate limit is per account, not per device.** 10 requests per minute,
  total, and the API answers 429 freely. The client holds a 9/min sliding window
  in-process and honours `Retry-After`. Each run here makes one to three calls, so
  this only bites if several scripts fire at once — don't add a polling loop.

## The shape of the night, and why

The user, 2026-10-06: **"I love to get into a warm bed and then have it gradually move
down. 95 is way too hot to sleep at."** That is two different temperatures, and it
is why `entry_temp_f` (92°F, how warm the bed is to climb into) is separate from
`base_temp_f` (the floor he actually sleeps at, and the only number the trial
ladders). He had the device sitting at 95°F when it was discovered — deliberately
warm, and by his own account too warm to sleep in. An earlier draft of this skill
treated the pad as purely a cooler and would have got that backwards.

Both ends have support. Warm skin at the extremities is the strongest single
predictor of how fast sleep arrives — better than core temperature, heart rate or
melatonin onset ([Kräuchi et al., Am J Physiol 2000](https://journals.physiology.org/doi/full/10.1152/ajpregu.2000.278.3.R741))
— so a bed that is already cold works against falling asleep, which is the thing
cooling pads usually get blamed for. And cooler across the first half of the night
is the finding with usable numbers. Warm on the way in, cool once he is under.

## What the temperatures are based on

The user asked what the science says, 2026-10-06. The short version, because it
shaped the defaults:

- **No study names a setpoint.** The one trial with objective sleep measures
  individualised it and ~68% of participants adjusted it themselves
  ([Clocks & Sleep 2026](https://pmc.ncbi.nlm.nih.gov/articles/PMC12550930/)).
  That trial found large *subjective* gains (sleep satisfaction d = 0.92) and **no
  significant objective change** in sleep time, efficiency or onset latency. The
  honest claim for this skill is that it makes the bed feel right, not that it
  measurably improves his sleep.
- **The physiological target is skin, not water**: 33.5–35.5°C. The pad is a lever
  on that, not the number itself.
- **The familiar 65–68°F figure is for AIR.** Water conducts heat far faster, so
  that number applied to a pad is much more aggressive than it sounds. An earlier
  draft of this skill defaulted to 60°F on exactly that mistake. Sleepme's own
  guidance: **72–76°F for hot sleepers, 78–82°F for cold**, their app defaults to
  78°F, and Dock Pro owners should go *higher* than they expect because the
  surface is large.
- **Cool early, warmer later** is the one finding with usable numbers
  ([Bioengineering 2024](https://doi.org/10.3390/bioengineering11040352)): cooler
  first half gave +14 min deep sleep (+22%) in men and +9 min REM (+25%) in women;
  warmer second half gave men +23 min light sleep (+19%). Authors work for the
  device maker. This is the shape of the night below: cool floor over the first
  half, and the pump stopped at 04:30 so the bed settles at body heat.
- **Feet are the exception.** The distal-to-proximal skin gradient predicts sleep
  onset better than core temperature, heart rate or melatonin onset
  ([Kräuchi et al.](https://journals.physiology.org/doi/full/10.1152/ajpregu.2000.278.3.R741)),
  so a uniformly cold pad works against falling asleep. If he reports taking
  longer to drop off on a cold setting, that is the likely cause, and socks are
  the standard answer — not a colder pad.

The user's own situation is mixed: he wants a blanket over him but tends to run
warm. That points at a cool surface under a warm cover rather than either
end of the range, which is why the trial ladder starts mid hot-sleeper band. **No
script here interprets that or scores his sleep** —
`trial.py --report` lays out what he said at each temperature and stops
(`feedback-no-medical-interpretation`: this skill records, it does not interpret).

## Before it can run

1. **The API token.** sleep.me → log in → **Account Details** → **Developer
   API** → Create New Token. Up to 10 per account, no expiry. Then
   `python3 scripts/install_token.py` — it prompts without echoing, so the token
   never reaches shell history, and rewrites `openclaw.json` atomically with a
   timestamped backup. `openclaw.json` is gitignored, so the token never lands
   in version control.
   No gateway restart is needed: `sleepme_client` reads the env var first and
   falls back to that file, and cron command jobs do not inherit a shell export.
2. **Discover the device**: `python3 scripts/discover_devices.py` — writes
   `config/chilipad_devices.json` (nickname `bed`) and a raw dump. Re-run it if
   the unit is re-registered in the app: the device id changes, and every script
   resolves through that map rather than a typed id, so this is the only file that
   has to catch up.
3. **Test it awake**, watching: `control.py --temp 72 --on`, `status.py`,
   `control.py --off`. Done 2026-10-06; the pad was returned to the 95°F standby it
   was found in.
4. **Enable the five cron jobs** — see [Automation](#automation). Done 2026-10-06.

## Mechanics

- **`control.py`** — the one way anything changes the bed.
  - `--temp 72` (also `21C`, `max-cool`, `max-heat`) or `--phase entry|base|rem`
    (resolved **at fire time** from tonight's floor, so stepping the trial never
    requires re-scheduling anything), `--on` / `--off`.
  - With a temperature and `--on` together the temperature is set **first**, so
    the pump never spends a minute running at last night's setpoint.
  - `--check-water` spends one extra request on the reservoir. Only the entry
    step takes it: a dry pad holds no temperature until morning and nothing else
    would notice.
  - Guards for scheduled callers: `--skip-if-away`, `--expected-start HH:MM`,
    `--late-grace-min N`. Omit them on manual calls.
- **`status.py [--json] [--quiet]`** — live read: running or standby, setpoint,
  water temperature and level, Wi-Fi, whether the user is away, tonight's plan, and
  whether the cron jobs are actually enabled. The two silent failure modes are an
  empty reservoir and a switched-off job, so both are in ordinary output.
- **`night_plan.py`** — the placement maths, and the only place that reads the
  lights. No network, fully unit-tested.
- **`ramp.py [--from 92 --to 72 --duration-seconds 2700] [--dry-run]`** — the
  gradual descent, in 2°F steps. Runs in-process for the whole ramp (so its cron
  job gets `duration + 600s` of timeout, set automatically). A failed step is
  skipped rather than aborting: every step sets an *absolute* temperature, so the
  next one puts the descent back on track, and a failure can never leave the pad at
  an extreme.
- **`night_schedule.py [--dry-run]`** — the nightly retime. See below.
- **`trial.py`** — the temperature ladder and the user's ratings. See below.
- **`maintenance.py`** — water, monthly cleaner, semi-annual deep clean. See
  [Upkeep](#upkeep-water-cleaner-deep-clean).
- **`install_token.py [--check]`**, **`discover_devices.py`** — setup.
- **`chilipad_jobs.py`** — reads the cron store to answer "will these jobs
  actually run". A local copy of `shared/sync_bedroom_sunrise.disabled_jobs`
  rather than an import, because that module reads the Hatch at import time and
  drags in the whole MQTT stack, far too much for a status check.
- **`test_chilipad.py`** — 111 tests, no network, no cron, no live state. Run it
  after touching any of the above.

## A temperature he set himself wins until morning (added 2026-10-06)

**Any manual `control.py` call — one without `--expected-start`, i.e. not a cron
job — starts a hold (`manual_hold.py`), and while it is live the scheduled
temperature steps stand down.** `ramp.py` abandons its descent where it is, and
the entry and descend jobs skip. **The two off steps are the exception: they still
run and they clear the hold** — stopping the pump is the warm option, so it never
works against him, and it means the next night is normal with nothing to re-enable. The morning-off job still runs (standby is where
the night ends anyway) and clears the hold, so the next night is normal with
nothing to re-enable by hand. The hold carries its own expiry — the morning-off
time read from the cron job that actually fires it — so a missed morning-off can
never silence the following night either.

```
python3 scripts/control.py --temp 80 --on    # holds until 8:00 AM, says so in its output
python3 scripts/manual_hold.py               # is a hold live, and until when
python3 scripts/manual_hold.py --clear       # hand the night back to the schedule
```

Why, from the skill's first live night: at **23:19** the user said *"I'm too cold rn"*.
The pad was at 76°F, four minutes from the next step of a descent to 72°F, so the
warmer setting he asked for would have been walked straight back down — and
`chilipad-rem` would have cooled the bed again at 03:07. The running ramp had to
be killed by hand (`kill <pid>`), which is not a thing he should ever need. This is
the same bug the Govee sunrise had, where saying "stop" was undone by the ramp's
next step (govee-lights/SKILL.md § Stopping a sunrise in progress) — a scheduled
ramp that sets absolute values every few minutes must ask, before each step,
whether the human has taken over.

Tests: the `ManualHold` class in `test_chilipad.py` (81 tests now), including that
an expired or unreadable hold silences nothing.

## His floor: nothing colder than `min_temp_f`

**The user, 2026-10-08: "The absolute coldest it should go right now is 85 degrees."**
`min_temp_f: 85` in `chilipad_config.json` is that line, and it is enforced in
`sleepme_client.set_temperature()` — the one place a temperature reaches the bed —
so no cron, script, stale ladder, agent turn or hand-typed `--temp` can go under
it. A number below it is **refused with a message naming the setting**, rather
than silently nudged, because a silent nudge is how he ended up not knowing what
his bed was doing.

Three places also lift their own numbers to the floor first, so the schedule
*lands* on 85 instead of erroring into it: `trial.ladder()` (rungs below the floor
are lifted and de-duplicated), `night_plan.phase_temps()` and `ramp.plan_steps()`
(the descent ends at the floor).

It narrows the hard `SAFE_MIN_F`/`SAFE_MAX_F` band (55–100°F) in
`sleepme_client.py` and can never widen it: that band is code, not config, for
injection safety. A config that cannot be read never blocks the bed.

**Why it exists.** The skill opened at a cool setpoint on the theory that he runs
hot. Every correction since went the other way, and on several nights he got up
and switched the machine off himself. The ladder now only goes up from the floor.

## Automation: the night is derived, not configured

All five jobs are **silent** — they send the user nothing, like the Govee light jobs.
Nothing in `chilipad_config.json` is a clock time except the no-alarm backstop.

| Job | When | What |
|---|---|---|
| `chilipad-entry` | lights-dark − 30 min | **92°F — warm**, pump on, check water |
| `chilipad-descend` | lights-dark | `ramp.py` walks 92°F → tonight's floor over 45 min, 2°F at a time |
| `chilipad-early-off` | **04:30** | standby — the bed settles at body heat |
| `chilipad-morning-off` | wake + 30 min | standby |
| `chilipad-night-schedule` | 9:20pm | recompute all four and retime them |

As scheduled for the night of 2026-10-06 (lights dark 22:45, alarm 07:30): warm to
92°F at **22:15**, start descending at **22:45**, reach 72°F by **23:30**, lift to
75°F at **03:07**, standby at **08:00**.

**The two anchors are read live:**

- **The alarm**, via `shared/sync_bedroom_sunrise.read_plan()` — the one place
  anything learns the user's calendar-aware wake time, the same read the bedroom
  lights use. So the bed follows the alarm however and whenever it was set,
  including a change made by hand in the Hatch app at bedtime that nothing
  announces. The rule the lights have followed since 2026-09-20 ("If I set the
  alarm to 9:30 am, the lights should adjust to that time"). This skill is also in
  `hatch-restore/scripts/alarm.py`'s `FOLLOWERS`, so an alarm change retimes the
  bed immediately rather than waiting for 9:20pm.
- **The lights**, via `night_plan.lights_dark_time()` — read out of
  `govee-winddown-fade-off-bedroom`'s own schedule *and* its `--duration-seconds`,
  so dark = fade start + fade length. govee-lights stays the owner of when the
  room goes dark and the bed just follows. Move the fade and the bed moves with
  it. A fallback is used if that job can't be read, and the report always says
  which it was — a silent fallback would quietly stop following the lights.

**Why the pump just stops.** The third phase was a lift twice and is now an off. It began at the MIDPOINT of the night (+3°F for the whole second half); after two nights the user said *"too hot while sleeping... too cold while waking up"*, so it moved to 45 minutes before the alarm (+5°F); hours later he said *"I think that if it shut off at 4:30 am that would be fine. It's mostly the falling asleep and the REM I am concerned about."* A stopped pump is not neutral — with no water circulating below skin temperature the bed settles at his own body heat under the duvet, warmer than any setpoint the pad holds. So the cooling covers falling asleep and the REM-heavy hours and then gets out of the way, and `04:30` is one of only two clock times in the config because he chose it. It is dropped on a night that would still be descending at 04:30, or one where he wakes within half an hour of it.

**9:20pm is not arbitrary and is not safe to move.** Tomorrow's wake time has to
already be on the device, which `hatch-restore`'s
`nightly_wake_and_workout_sync.py` does at 9:00pm; 9:20 also sits behind the
9:15pm bedroom-light sync and ahead of the ~10:15pm entry step. Reading the
Hatch earlier reads *yesterday's* wake time and reports a confident
`{"status": "ok"}` — how the lights read stale data for several days in 2026-08.

### What stops, moves or drops a run

- **Away from home** (`--skip-if-away` → `current_location.is_away_from_home()`):
  no cooling an empty bed at home while the user is travelling.
- **A gateway catch-up** (`--expected-start` → `shared/misfire_guard.near_slot()`):
  a job replayed hours off its slot is skipped. Both guards are re-listed in
  `argv_for()` on every retime, because `cron edit --command-argv` replaces the
  whole argv — a flag left out is a flag silently dropped, which is how the govee
  misfire guard vanished for weeks before 2026-09-09. There are tests for this.
- **A switched-off alarm moves the morning, it does not skip it.**
  `read_plan()` still returns a disabled alarm's time, and switching the pad off
  then would cut the cooling while the user is still asleep on exactly the morning he
  meant to sleep in. So the morning-off goes to `no_alarm_off_time` (10:00am)
  instead.
- **A night too short for a phase drops that phase** rather than churning the
  pump for under 30 minutes; the report names what was dropped.
- **Anchors that disagree refuse the whole plan.** If lights-dark and wake land on
  the same minute the window would come out as 24 hours and the midpoint phase
  would fire at lunchtime. Anything over 16 hours raises instead.
- **A disabled cron job is not success**: `cron edit` returns 0 on a disabled job,
  so the enable state is checked separately every night and reported as
  `"status": "will_not_run"`. Nothing here ever re-enables a job on its own — a
  job switched off by hand was switched off on purpose.

## The temperature trial

`base_temp_f` is a placeholder until the user's own number is known, and he asked to
test rather than guess. `trial.py` steps a ladder — **72 → 70 → 68 → 74 → 76°F,
three nights each** — and records how he rated each night against the temperature
it was actually slept at. Only the **floor** is laddered; the warm entry
temperature is tuned on its own.

`advance()` is **idempotent per night**: `night_schedule` legitimately runs more
than once in an evening (9:20pm, plus any time the Hatch alarm changes, since it is
in `alarm.py`'s `FOLLOWERS`), and counting each run would race through the ladder
and file a rating against a temperature he never slept at.

```
trial.py --status                      # tonight's temperature and where the ladder is
trial.py --rate good|hot|cold [--note "woke at 3"]
trial.py --report                      # what he said at each temperature
trial.py --stop                        # settle on the current rung
```

`night_schedule.py` calls `--advance` each night, so the ladder only moves on
nights the bed was actually scheduled. Ratings are **his words, verbatim**, with
no scoring or conclusion drawn.

### Deliberately not built

- **The only live Telegram message is the upkeep prompt** (`chilipad-upkeep`, live
  2026-10-06 — see [Upkeep](#upkeep-water-cleaner-deep-clean)). That covers the
  low-reservoir warning too: water is one of its three tasks, fired off the device's
  own `is_water_low` rather than a date.
- The nightly rating prompt went live 2026-10-07 — see
  [The rating prompt](#the-rating-prompt-live) below.
- **No pre-alarm warm-up.** the user turned it down, 2026-10-06. The midpoint lift is
  a mid-night rise for sleep quality, not a wake aid; the pad simply switches off
  after he is up.
- **No briefing line.** He left it out.
- **No proactive temperature changes.** Same rule as the lights
  (`feedback-govee-light-commands`): an explicit request is carried out without
  confirming it, and nothing is ever triggered unasked.

## The rating prompt (LIVE)

`rate_prompt.py`. The user approved the exact wording and the wake+60 trigger on
**2026-10-07**; `chilipad-rate` was created the same day. Changing the wording or the
trigger needs his sign-off again, and two tests make that hard to do by accident: one
pins the text character for character, the other pins `morning_offset_minutes: 60` and
that `jobs.rate` exists. `rate_prompt.enabled: false` takes it offline without deleting
the cron.

> 🛏 How did you sleep at 80°F?

with **✅ Good · 🔥 Too hot · 🧊 Too cold**. One line, naming the temperature so he is
rating a number rather than a vague memory — he cut the upkeep prompt's second line
himself, so this one never had one.

**Why it is worth a message at all.** The trial records his verdict against the
temperature actually slept at, but only if he remembers to run `trial.py --rate`. Over
a 15-night ladder that is 15 remembered commands, and an unrated night is not neutral:
`advance()` steps the rung anyway, so the ladder walks on with nothing recorded against
it.

**When it would ask:** `morning_offset_minutes` (60) after the Hatch wake time, read
live the same way every other job here reads it. Morning rather than evening because
rating a night fourteen hours later is recall, not experience. It must also land
**before `night_schedule.py --advance` at 9:20pm**, since `trial.rate()` files against
`tonight_base()` — the rung currently set. A morning answer lands on the rung he
actually slept on; an answer after 9:20pm would be filed against the next one.

Four things stop it, all in `should_ask()`, which is pure so the tests drive it without
a subprocess that might send a real message: the `enabled` flag (checked first), a
stopped trial, being away from home (he did not sleep in this bed), and a rating
already filed today — by the prompt or by his own `trial.py --rate`, since asking twice
would file two ratings for one night.

Taps go through `confirm:chilipad-rate-good|hot|cold` in
`plugins/callback-dispatch/dispatch-table.json` and edit the prompt in place, printing
`NO_REPLY`, so a tap never produces a second message and a second tap cannot file a
duplicate.

To show him: `python3 scripts/rate_prompt.py --dry-run`.

**It is retimed nightly like every other job.** `night_plan.build()` returns `rate` as
a fifth moment (wake + the offset) and `night_schedule.py` edits `jobs.rate` along with
the rest, so it follows the alarm rather than sitting on a clock time. It is placed
after the morning-off on purpose: a "how did you sleep" that arrives while the bed is
still running is asking about a night that has not finished.

**A missing `jobs.rate` is skipped, not fatal.** The four bed phases are mandatory —
without them the night does not happen — but the prompt is an extra, and a config that
predates it must not take the whole retime down. The report names what it skipped.

Unlike the bed jobs it takes **no `--skip-if-away` or misfire flags**: `control.py` and
`ramp.py` parse those and `rate_prompt.py` does not, so `argv_for()` returns its argv
bare. Its own equivalents live in `should_ask()`. There is a test for that, because
`cron edit --command-argv` replaces the whole argv and a flag it cannot parse would
make every run exit non-zero.

## Upkeep (water, cleaner, deep clean)

sleep.me's manual states that **"inability to follow maintenance instructions voids
the customer warranty"**, so `maintenance.py` tracks the three jobs. One daily cron,
`chilipad-upkeep` (7:00pm), travel-gated and silent unless something is due. **Live
2026-10-06**; both dated clocks start unseeded, because the user had not deep cleaned it in
a while, so the deep clean is genuinely the first thing it will ask for.

| Job | Trigger | What it asks for |
|---|---|---|
| water | **the device's own `is_water_low`**, not a date | Top off with **distilled** water until the droplet icon clears |
| cleaner | every 30 days | ¼ cup (2 oz) hydrogen peroxide, cap off ~20 min, then top off with distilled |
| deep | every 182 days | Hose off, rinse reservoir, fresh distilled + peroxide, drain with the drain key, wash the pad cold/gentle + tumble medium |

**Peroxide, not the cleaner packets** — the user's choice, 2026-10-06, so the packet
alternative is not offered. Each prompt is ONE line: he cut the explanatory second
line himself ("dont need the entire second line"). Distilled water is still named in
all three because the manual is explicit that tap-water minerals accumulate and cut
the cooling, and it is the easiest instruction to get wrong. There is a test for that.

- **One prompt at a time**, most urgent first (water → deep → cleaner). Three
  messages at once is the pile-up the user has asked not to get.
- **Per-task cooldown** — 2 days for water, 4 for the dated jobs — so an
  unanswered prompt nags on a known cadence rather than daily, and a held job
  never blocks the others.
- **✅ Done / Later** buttons (`confirm:chilipad-maint` / `skip:chilipad-maint` in
  `plugins/callback-dispatch/dispatch-table.json`). The tap edits the prompt in
  place and clears its keyboard — no second message, and no re-logging the same
  job twice. The handler resolves which task was prompted from the *sender's* own
  state file, which is what the button contract requires.
- **Seed the clocks** or the first run will claim both dated jobs are overdue:
  `maintenance.py --set-last cleaner --date 2026-09-20`. A seeded entry is logged
  as "told to me, not observed".
- `maintenance.py --status` / `--log` to see where things stand. `--check --dry-run`
  prints the exact message it would send without sending it.

## Data

Per `workspace/Documentation/rules/32-where-data-lives.md`, all four files are
registered in `shared/data_paths.py` and reached through `path()`:

| File | Kind | What it is |
|---|---|---|
| `config/chilipad_config.json` | config | Entry and floor temperatures, the REM lift, the minute offsets, the trial ladder, the four job ids |
| `config/chilipad_devices.json` | config | nickname → sleep.me device id |
| `config/chilipad_raw_devices.json` | config | The discovery dump, for reference |
| `data/chilipad_nights.json` | data | Which rung the trial is on, and the user's rating of each night |
| `data/chilipad_maintenance.json` | data | When each upkeep job was last done, when each was last asked about, and the log |
| `state/chilipad_maintenance_prompt.json` | state | The upkeep prompt awaiting a tap; safe to delete |
