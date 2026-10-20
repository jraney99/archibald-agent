---
name: hatch-restore
description: Manage the Hatch Restore sound machine and wake alarm, including travel-aware schedules.
---

# Hatch Restore

Control the configured Hatch Restore through the existing scripts. The device
nickname is `restore`. Hatch has no public control API; light and sound use
the existing MQTT-backed client, while alarms use the existing REST client.

## Routine control

Run commands from this skill directory, or use their absolute paths.

```bash
python3 scripts/status.py --device restore
python3 scripts/control.py --device restore --off
python3 scripts/control.py --device restore --color "255,120,60" --brightness 20
python3 scripts/control.py --device restore --sound "<favorite name>" --volume 30
python3 scripts/control.py --device restore --sound none
python3 scripts/control.py --device restore --clock on
```

Batch compatible flags into one `control.py` invocation. Hatch login limits
are tight and undocumented, so do not run status, control, and discovery
back-to-back or retry rapidly after a rate-limit response.

Sounds on this generation are saved Hatch favorites/routines. Match by name;
do not invent a sound catalog.

## Alarm operations

```bash
python3 scripts/alarm.py --list
python3 scripts/alarm.py --time 9am
python3 scripts/alarm.py --enable
python3 scripts/alarm.py --disable
python3 scripts/alarm.py --weekdays weekdays
```

Use the configured device and alarm defaults. Never type or remember a raw
alarm ID: it changes when the alarm is recreated, and
`config/alarm_config.json` is the source of truth.

A normal `alarm.py --time ...` change intentionally:

1. updates the Hatch alarm;
2. records the one-morning manual wake override so the nightly sync does not
   immediately undo it, unless an earlier calendar commitment wins;
3. retimes the dependent bedroom sunrise, morning-off light, breakfast
   reminder, and morning briefing.

Report every result line, especially any “NOT retimed” warning. Use
`--no-follow` only when the user explicitly wants a Hatch-only change that
does not move downstream routines.

`alarm.py` edits existing alarms; it does not create new ones. Its displayed
wake time is the effective wake moment after the alarm's sunrise lead time,
not the raw stored start timestamp.

## Operational invariants

- The nightly wake calculation defaults to 8:30am and may move earlier for
  calendar commitments. Golf and ordinary appointments use different arrival
  buffers defined in `config/alarm_config.json`.
- Workout scheduling is independent of wake time. Do not derive a workout
  time from an unusually early alarm.
- While away, the nightly workflow disables the physical Hatch and tells the
  user to set a phone alarm; travel-day backup nudges follow the existing
  workflow. It re-enables the Hatch after returning home.
- The user must receive a wake-time result or an explicit failure notice;
  never let a nightly sync fail silently.
- The bedroom Govee sunrise follows the effective Hatch wake time through
  `../shared/sync_bedroom_sunrise.py`.
- Tests that exercise `compute_and_apply` or `alarm.main()` must sandbox
  `wake_override.path`; an unsandboxed test can alter live wake behavior.
- Do not use the live Hatch, calendar, messaging, or cron system in tests
  unless the user explicitly authorizes those side effects.

## Load detailed guidance only when needed

Read [references/full-guide.md](references/full-guide.md) before any of these:

- setup, authentication, dependency changes, device discovery, or adding a
  replacement device;
- creating, deleting, retiming, or troubleshooting wake, workout, briefing,
  breakfast, travel-nudge, or Govee synchronization jobs;
- changing calendar/drive-time logic, travel behavior, conflict detection,
  sunrise calculations, or manual-override semantics;
- debugging MQTT/device-shadow behavior, alarm payloads, rate limits, stale
  alarm IDs, or historical failures.

For ordinary status, light/sound/clock control, alarm listing, or a standard
wake-time edit, this entrypoint is sufficient; do not load the full guide.

## Safe verification

Use fixture/mocked tests and keep all live state sandboxed:

```bash
python3 scripts/test_alarm_cache.py
python3 scripts/test_alarm_cli.py
python3 scripts/test_sync_alarm.py
python3 scripts/test_wake_override.py
python3 scripts/test_travel_wake_nudge.py
```
