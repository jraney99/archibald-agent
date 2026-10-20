# calendar-write-guard

Write-side twin of [`calendar-read-guard`](../calendar-read-guard/). A
`before_tool_call` hook on the `exec` tool that catches the three documented
ways the live agent botches an ad-hoc calendar change instead of using the
wrapper scripts.

## What it does

| Situation (exec command) | Action |
| --- | --- |
| `gog calendar create/update` with an invented flag (`--title`, `--start`, `--end`, `--date`, `--format`, `--start-date`, `--end-date`, `--event-id`, `--guest-email`) | **block** with the real flag list + wrapper pointer |
| `gog calendar create` missing `--start-timezone` or `--end-timezone` (and not `--all-day`) | **block** — points at `add_calendar_event.py` |
| 2nd+ `gog calendar create` of the same `--summary` in one session, or a create of a title deleted earlier this session | **require approval** — the delete→recreate display-artifact spiral (RULES.md "Reading back a dateTime") |

## What it leaves alone

Cron/job runs (`ctx.jobId` set — those come from vetted scripts), the wrapper
scripts themselves, `--help`, a lone `gog calendar delete` (only recorded, for
the loop guard), a first create of a new event, reschedules that use the real
`gog calendar update --from/--to` flags, and all reads.

## Test

```
node test.mjs
```

The core logic is the pure `inspectCalendarWrite(cmd, session)` export.
