# calendar-read-guard

Deterministic reroute of ad-hoc calendar **reads** to `Skills/shared/fetch_calendar.py`.

## The problem it fixes

RULES.md (§ Google Calendar) and TOOLS.md both mandate `fetch_calendar.py` for
every ad-hoc schedule read — it is the only path that spans all three calendars
(Personal + `owner@work.example` + `contact@example.org`). Raw `gog cal ls` reads
Personal only and silently drops the entire work day.

On the weak model config (Haiku, `thinking: off`) the live agent keeps ignoring
the wrapper and hand-driving `gog`. Documented: `feedback-gog-cal-cli-flags`
(2026-07-14), `feedback-gog-datetime-display-confusion` (2026-08-26, *"skipped an
existing, freshly-reloaded wrapper and hand-guessed raw gog flags anyway —
model-adherence gap, not a docs gap"*).

**2026-09-01, one session** — the user asks what's on the calendar / how long an upcoming
trip is:

1. `gog calendar list --start-date … --end-date … -j` → `unknown flag --start-date`, hard error
2. `gog calendar list … -j | head -40` → Personal only, JSON truncated to one event
   → reported just today's appointment, then **asked the user how long the trip was** —
   the return flight on 9/8 was on the calendar
3. `fetch_calendar.py --days 7 --json | jq '.events[] | select(.start.dateTime …)'`
   → wrapper's schema is `start_iso`, not `.start.dateTime` → `(no output)`
4. `gog calendar list … -j | jq … | head -20` → finally worked, Personal only

Prior fix attempts (RULES.md hardening, TOOLS.md notes) didn't take — same class
as the callback-dispatch hole: a structured need re-derived by a weak LLM each
time. Same remedy: a deterministic hook.

## How it works

Registers a **`before_tool_call`** hook (priority 60). For every `exec` call it
checks the command. If it's an ad-hoc calendar **read** —

- a raw `gog cal(endar) list|ls|events|search|agenda`, or
- a `fetch_calendar.py` call wrapped in a pipe (`| jq`, `| head`, …)

— it replaces the **whole** command with a clean
`python3 fetch_calendar.py <window> [--query …]` in text mode. Date hints in the
original are carried over, including the flags the model likes to invent
(`--start-date` / `--end-date` / `--start` / `--end` / `--date`). A missing or
too-narrow window is widened so a read can't miss an event a week out again:

| original names… | rewritten window |
|---|---|
| nothing | `--days 21` (`defaultDays`) |
| `--days N` | `--days max(N, 10)` (`minDays`) |
| `--from X` (no/near `--to`) | `--from X --to X+10` |
| `--from X --to Y`, span ≥ 10d | kept as-is |
| `--query Q`, no dates | `--days 30` (`minSearchDays`) + `--query Q` |
| `--query Q` + dates | dates kept, `--query Q` |

### Not touched

Calendar **writes** (`create` / `add` / `update` / `delete` / `move` / `respond`
/ `quickadd`), the `add_calendar_event.py` / `update_calendar_event.py` wrappers,
reads that already name `--calendar(s)` (deliberate single-calendar per RULES.md),
`--help`, commands chained with `&&` / `;` / `||`, clean `fetch_calendar.py` calls
with no pipe, and anything that isn't a calendar read.

## Files

- `index.mjs` — the hook + `rewriteCalendarRead()` (exported for tests).
- `test.mjs` — `node test.mjs`; covers the four real 2026-09-01 commands, window
  math, query handling, and the skip list.
- `openclaw.plugin.json` — manifest + config schema.

## Config

`openclaw.json`:

```jsonc
"plugins": {
  "allow":   [ …, "calendar-read-guard" ],
  "entries": { "calendar-read-guard": { "enabled": true } }
}
```

Optional tuning under `entries.calendar-read-guard.config`:
`defaultDays` (21), `minDays` (10), `minSearchDays` (30).

After editing `index.mjs`: `openclaw gateway restart` (hot-reload picks up
enable/disable, not code).

## Verify

```bash
node test.mjs
openclaw plugins inspect calendar-read-guard --runtime --json | \
  python3 -c 'import json,sys;d=json.load(sys.stdin);p=d.get("plugin",d);print(p["status"],p["hookCount"])'
# -> loaded 1
```

Live: the gateway log shows

```
[calendar-read-guard] rerouted calendar read
  from: gog calendar list … --start-date 2026-09-05 --end-date 2026-09-07 -j
  to:   python3 …/fetch_calendar.py --from 2026-09-05 --to 2026-09-15
```

whenever the agent hand-drives a calendar read.
