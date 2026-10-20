---
name: zengge-lights
description: Control the Zengge (ZENGGE app) Bluetooth light in the living room — power, brightness, colour, white — and read back what it is showing.
---

# Zengge Light (living room)

Two lights, both in the living room: `living_room` and `living_room_2` (`all`
covers both), Bluetooth-only strips added on 2026-10-12. It is driven by the Mac mini's own Bluetooth radio, so it has to
be within range of the Mac and the ZENGGE phone app must not be holding it.
Use the nicknames from `config/zengge_devices.json`; never expose the raw
Bluetooth name or address.

## Routine control

Run from this skill directory, or by absolute path.

```bash
python3 scripts/status.py --light living_room
python3 scripts/control.py --light living_room --on
python3 scripts/control.py --light living_room --off
python3 scripts/control.py --light living_room --brightness 40
python3 scripts/control.py --light living_room --color "255,170,90" --brightness 30
python3 scripts/control.py --light living_room --white --brightness 60
python3 scripts/control.py --light all --off
```

`control.py` accepts several flags in one call. Every command is sent over
Bluetooth and then confirmed against the light itself:

- `"status": "ok"` — the light reports the change (`source: device`).
- `"status": "unverified"` — it was reached but reports something else; say so,
  do not claim it worked.
- `"status": "error"` — it could not be reached (off at the switch, out of the
  Mac's Bluetooth range, or the phone app has the connection).

What it cannot do: it has one white channel, so there is **no colour
temperature** — "warm white" / "cool white" / a Kelvin value is not a thing
here; use `--white` at a brightness. Scenes and effects are not wired up.
It follows the Govee lights' evening (since 2026-10-12): on to Sunset Gold at
8:00pm, a 45-minute fade to off from 10:00pm, and the night-light / movie holds
(`govee-lights/scripts/evening_hold.py`, whose `all` covers it; a scene hold
leaves it alone, a colour hold applies). It is NOT in the Govee `--light all`
for manual commands, not on the console's daytime curve or Zoom lighting, and
not part of the bedroom sunrise.

## How the radio is reached

macOS grants Bluetooth to the top process of a tree, and under the gateway that
is node, which has no grant. So the scripts hand every command to
`scripts/ble_helper.py`, a launchd job (`ai.openclaw.zengge-ble-helper`) that
runs as Apple's signed Python, the identity the user granted on 2026-10-12. It idles
between commands and never messages the user. If every call says the helper did not
answer or the light is unreachable, check it first:

```bash
launchctl print gui/$(id -u)/ai.openclaw.zengge-ble-helper | head -5
tail -20 ~/.openclaw/logs/zengge-ble-helper.log
```

Its plist lives in `launchd/`; after editing the helper, `launchctl kickstart -k
gui/$(id -u)/ai.openclaw.zengge-ble-helper` restarts it. Without a helper the
scripts use the radio directly, which works from an interactive shell only.

## Operational invariants

- Manual/chat commands omit the schedule-only flags (`--skip-if-away`,
  `--skip-if-held`, `--expected-start`). Two cron jobs use them:
  `zengge-winddown-living-room` (8:00pm, `control.py --light all`) and
  `zengge-winddown-fade-off-living-room` (10:00pm, `evening_fade.py --light all --detach`,
  the Govee curve imported from `winddown_fade.py`). The 10:45pm hold-off and
  1am sweep are the Govee jobs; they reach this light through `evening_hold.py`.
- Scheduled on-jobs carry `--skip-if-away`; the fade-off runs while away so the
  light cannot stay on in an empty home.
- Never use the live light for a test unless the user explicitly authorises it.
  `scripts/test_zengge_ble.py` opens no radio.
- Bluetooth is not available to a sandboxed process; if every call says
  "Bluetooth is not usable from this process", that is the sandbox, not the
  light.

## Adding or troubleshooting a light

`python3 scripts/discover_devices.py` lists every Zengge light the Mac can hear
with the name to put in `config/zengge_devices.json`. `python3
scripts/zengge_ble.py` prints each configured light's real state. The protocol
and this unit's quirks are documented at the top of `scripts/zengge_ble.py`.
