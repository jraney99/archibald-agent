---
name: nest-thermostat
description: Control the Nest thermostat — temperature and mode on demand, a night setback that follows the user's lights and alarm, and an eco setback while he is away.
---

# Nest Thermostat Skill

Controls the user's Nest through Google's Smart Device Management (SDM) API: read the
house, set a target or a mode, drop it overnight on the same anchors the ChiliPad
uses, and put it on eco while he is travelling.

**Status 2026-10-06: built, not live.** Blocked on Google's console setup — see
[Setup](#setup). The four cron jobs are created disabled.

## Why the setup is so much worse than the ChiliPad's

sleep.me hands out a bearer token from a web page. Google requires a Device Access
project (**one-time US$5**), a Google Cloud project with the Smart Device
Management API enabled, a Web Application OAuth client, and a refresh token
obtained through Nest's **Partner Connection Manager** rather than the ordinary
Google consent screen. It also insists on a **consumer** Google account —
Workspace accounts are refused outright.

After that it is plain REST, and `setup_oauth.py` reduces the last step to one
command.

### Its own OAuth client, deliberately

This must never share the OAuth client that `gog` and the email daemon use. On
2026-10-02 those two shared one client and revoking it killed both at once
(`feedback-google-oauth-rotation-lessons`). A thermostat is not worth putting
calendar and mail at risk, so this carries `NEST_CLIENT_ID` /
`NEST_CLIENT_SECRET` of its own and can be revoked alone.

### Three API rules, each an unexplained 400 if you miss it

1. **A setpoint command must match the mode** — `SetHeat` needs HEAT, `SetCool`
   needs COOL, `SetRange` needs HEATCOOL. `set_temperature_f()` reads the mode and
   picks the command rather than guessing.
2. **Eco mode rejects setpoint commands outright.** Eco is cleared first, and the
   result says so.
3. **In HEATCOOL, cool must be strictly above heat.** The target is bracketed by a
   spread, and the bracket is widened if rounding would collapse it.

Plus: **every SDM temperature is Celsius**, whatever the thermostat displays.
Conversion happens at the edges of `nest_client.py` and nowhere else — a raw
Celsius number in front of the user is a bug.

### The quota rules out polling

Google's per-device ceiling is **5 queries per minute and 100 per hour**, enforced
across all projects to protect the thermostat. The hourly cap is the binding one:
one read a minute would exhaust it in under two hours. The client holds both
windows at 4/min and 90/hour, **refuses** rather than sleeping out the hourly one
(an hour-long sleep inside a cron job would hold it open to its timeout), and
nothing in this skill polls. `control.py --temp` spends two calls, because it has
to read the mode before it can pick the right command.

## Setup

1. **Console work** (not scriptable, needs a consumer Google account):
   - [console.nest.google.com/device-access](https://console.nest.google.com/device-access)
     — accept the terms, pay the one-time **US$5**.
   - Google Cloud → a project → enable the **Smart Device Management API**.
   - Credentials → OAuth client ID → **Web application**, authorized redirect URI
     exactly `https://www.google.com`.
   - Device Access Console → Create project → paste the OAuth **Client ID** → it
     returns a **Project ID** (UUID).
2. **`python3 scripts/setup_oauth.py`** — prompts for the Project ID, Client ID and
   Secret (secret not echoed), prints the Partner Connection Manager URL, takes the
   redirect URL back, exchanges the code, **verifies it by listing the devices**,
   and writes all four secrets into `openclaw.json`'s `env.vars` atomically with a
   backup. `openclaw.json` is gitignored (`.gitignore` line 108).
   `--check` reports what is installed, redacted.
3. **`python3 scripts/discover_devices.py`** — writes `config/nest_devices.json`
   (nickname `house`). Every script resolves through that map rather than typing the
   long `enterprises/…/devices/…` name, so this is the only file to update if the
   Device Access project is ever recreated.
4. **Set the temperatures** in `config/nest_config.json`. 67/70°F are starting
   guesses, not the user's stated preference.
5. **Enable the four cron jobs** — see [Automation](#automation).

## Mechanics

- **`control.py`** — `--temp 70` (Fahrenheit), `--preset sleep|day` (from config,
  resolved at fire time so a config change needs no re-scheduling),
  `--mode heat|cool|heatcool|off`, `--eco on|off`. Guards for scheduled callers:
  `--skip-if-away`, `--expected-start HH:MM`, `--late-grace-min N`.
- **`status.py [--json]`** — ambient, mode, hvac, humidity, setpoints, eco, online,
  all in Fahrenheit.
- **`away.py --check | --status [--dry-run]`** — the travel setback. See below.
- **`night_schedule.py [--dry-run]`** — the nightly retime. See below.
- **`setup_oauth.py`**, **`discover_devices.py`** — setup.
- **`test_nest.py`** — 33 tests, no network, no cron, no live state.

## Automation

All four jobs are **silent**. Nothing in `nest_config.json` is a clock time.

| Job | When | What |
|---|---|---|
| `nest-night` | lights-dark | house to `sleep_temp_f` (67°F) |
| `nest-morning` | wake − `morning_lead_minutes` (45) | house to `day_temp_f` (70°F) |
| `nest-night-schedule` | 9:25pm | recompute both and retime them |
| `nest-away-check` | 9am and 9pm | eco on while away, off on return |

**The same two anchors the bed uses**, read live:

- **The lights** — `chilipad/scripts/night_plan.lights_dark_time()`, which reads the
  Govee bedroom fade job's schedule *and* its duration. **Imported, not copied**, so
  there is exactly one definition of "when the room goes dark"; two copies would
  drift and the bed and the house would stop agreeing. There is a test asserting it
  is literally the same function. The cost is that this skill depends on the
  chilipad folder being present — the deliberate trade, and
  `shared/sync_bedroom_sunrise.py` already imports across skills the same way.
- **The alarm** — `shared/sync_bedroom_sunrise.read_plan()`, the one place anything
  learns the calendar-aware wake time.

**This is the point of it:** the bed and the house now cool and warm off the same
two signals, so the ChiliPad is not pulling heat out of the user while the furnace
runs, or vice versa.

**Why the morning LEADS the alarm.** A house takes 30–45 minutes to move a few
degrees. Restoring the temperature *at* the wake time means waking in a cold house
that comes right while he is in the shower. The lead is clamped so it can never
reach back past bedtime and warm the house up in the middle of the night.

**9:25pm is not safe to move.** Tomorrow's wake time has to already be on the
device, which `hatch-restore`'s `nightly_wake_and_workout_sync.py` does at 9:00pm;
9:25 sits just behind the chilipad retime at 9:20pm. Reading the Hatch earlier
reads yesterday's wake time and reports a confident `{"status": "ok"}`.

### What stops, moves or refuses a run

- **Away from home** (`--skip-if-away`): the setback jobs no-op, because `away.py`
  owns the house while he is travelling.
- **A gateway catch-up** (`--expected-start` → `shared/misfire_guard.near_slot()`).
  Both guards are re-listed in `argv_for()` on every retime, because
  `cron edit --command-argv` replaces the whole argv.
- **A switched-off alarm** moves the morning to 10:00am rather than firing at a wake
  time he is not keeping.
- **A disabled cron job is not success** — `cron edit` returns 0 on a disabled job,
  so the enable state is checked separately and reported as `"will_not_run"`.

### The travel setback only ever undoes its own work

`away.py` records in `state/nest_setback.json` when **it** turned eco on, and on
return clears eco only if that record says so. Eco that the user switched on himself in
the Nest app is left alone — the same restraint `sync_bedroom_sunrise` shows in
never re-enabling a cron someone switched off by hand. Without that flag, coming
home would silently cancel a setting he had chosen. The whole truth table is
`away.decide()`, which is pure and fully tested.

Eco rather than a setpoint, deliberately: it is Nest's own away mode, it keeps the
house inside safe bounds rather than a number this skill invents, and it is one
reversible switch.

### Deliberately not built

- **No Telegram messages at all.** Silent like the Govee jobs.
- **No proactive temperature changes** beyond the scheduled setbacks. An explicit
  request is carried out without confirming it; nothing is triggered unasked
  (`feedback-govee-light-commands`).
- **No polling.** The hourly quota forbids it, and there is nothing to watch for.

## Data

Registered in `shared/data_paths.py`, reached through `path()`:

| File | Kind | What |
|---|---|---|
| `config/nest_config.json` | config | Sleep/day temperatures, morning lead, the two job ids |
| `config/nest_devices.json` | config | nickname → SDM device name |
| `config/nest_raw_devices.json` | config | The discovery dump |
| `state/nest_setback.json` | state | Whether *this script* set the current eco; safe to delete |
