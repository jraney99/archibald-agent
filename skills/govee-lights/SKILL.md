---
name: govee-lights
description: Control the configured Govee lights, scenes, brightness, and schedules.
---

# Govee Lights

Control the configured Govee strips through the existing scripts. Use friendly
nicknames from `config/devices.json`—currently `console_light`, `bedroom`,
or `all`—and never invent or expose raw device IDs.

## Routine control

Run commands from this skill directory, or use their absolute paths.

```bash
python3 scripts/status.py --light all
python3 scripts/control.py --light bedroom --on
python3 scripts/control.py --light console_light --off
python3 scripts/control.py --light all --brightness 40 --color "255,180,60"
python3 scripts/control.py --light bedroom --temp 3500
python3 scripts/control.py --list-scenes --light bedroom
```

`control.py` accepts multiple flags in one invocation and applies them in
sequence. Prefer one call per light or `all`, rather than several separate
calls.

Flat power, brightness, color, and color-temperature commands try the LAN
path first and confirm against the device. They can fall back to the Govee
cloud. `status.py` labels each row:

- `source: device` is direct device state.
- `source: cloud` is only the cloud's claim; never describe it as physically
  verified.

Dynamic scenes are cloud-only and cannot be read back reliably. Report that
limitation rather than claiming visual confirmation.

### Held evening modes and scenes

Evening modes must survive scheduled winddown/fade jobs, so use
`evening_hold.py`:

```bash
python3 scripts/evening_hold.py on --mode night_light
python3 scripts/evening_hold.py on --mode movie
python3 scripts/evening_hold.py on --scene "War Films"
python3 scripts/evening_hold.py on --scene Reading --lights bedroom
python3 scripts/evening_hold.py status
python3 scripts/evening_hold.py off
```

A hold on `all` also drives the living-room Zengge light (`Skills/zengge-lights`,
since 2026-10-12) for colour/brightness modes; scene holds leave it alone because
it has no scenes. `--lights living_room` holds it on its own.

A user-named scene such as “Movie,” “Thrillers,” or “Reading” is a held scene,
not a bare `control.py --scene` call. The hold stands down covered evening
jobs and normally ends with lights off at 10:45pm; late-night requests have
their existing fail-safe cutoff. An explicit light request should be executed
without asking for redundant confirmation.

### Stop a sunrise

Turning the bedroom light off must also stop an active sunrise ramp:

```bash
python3 scripts/sunrise_ramp.py --light bedroom --stop
```

A normal `control.py --light bedroom --off` writes the same stop flag. Do not
turn the light back on after the user says “stop,” “I'm up,” or otherwise asks
for it off.

## Operational invariants

- Manual/chat commands omit schedule-only flags and remain available while
  travelling.
- Scheduled on-jobs use `--skip-if-away`; scheduled off-jobs continue while
  away so lights cannot remain on in an empty home.
- Fixed-clock jobs carry `--expected-start HH:MM`. If a job is retimed,
  update that argument in the same cron edit or the misfire guard may skip the
  legitimate run.
- The bedroom sunrise and morning-off jobs are retimed from the Hatch wake
  plan by `../shared/sync_bedroom_sunrise.py`. Do not change one side of that
  dependency without checking the other.
- Fixed recurring light operations remain command-type cron jobs, not
  agent-turn jobs.
- Never use live devices for a test unless the user explicitly authorizes it.
  Existing unit tests mock network/device behavior.

## Load detailed guidance only when needed

Read [references/full-guide.md](references/full-guide.md) before any of these:

- adding or rediscovering a device, changing credentials, or troubleshooting
  LAN/cloud behavior;
- creating, deleting, retiming, or diagnosing cron jobs;
- changing the evening curve, fade, sunrise, Hatch bridge, Zoom lighting, or
  travel/misfire behavior;
- investigating historical failures, device-specific capabilities, API rate
  limits, or scene/status limitations.

For an ordinary on/off, brightness, color, status, held mode, held scene, or
sunrise-stop request, this entrypoint is sufficient; do not load the full
guide.

## Safe verification

Use the relevant local tests without touching devices:

```bash
python3 scripts/test_evening_hold.py
python3 scripts/test_govee_lan.py
python3 scripts/test_sunrise_stop.py
python3 scripts/test_skip_if_away.py
```
