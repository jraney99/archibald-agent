## Google Calendar (gog · owner@example.com)
- **LOOKUP PRIORITY — MANDATORY:** For ANY question about events, trips, flights, meetings, or schedule, check the calendar FIRST. MEMORY.md travel entries are supplementary context only — they are not exhaustive and are not authoritative. Never negate or deny a trip, flight, or event claim based solely on MEMORY.md. Always check the calendar before reporting what is or is not there.
- **READ VIA THE WRAPPER — MANDATORY:** the user keeps events on THREE calendars: `owner@example.com` (Personal), `owner@work.example` (work), `contact@example.org` (a side project). A bare `gog cal ls` / `gog calendar events` reads only the primary (Personal) calendar and **silently omits the entire work day**. For every ad-hoc schedule lookup run `python3 ~/.openclaw/workspace/Skills/shared/fetch_calendar.py` (`--days N`, `--from`/`--to`, `--query`, `--json`) — it spans all three calendars and renders times in the user's current tz. Only drop to raw `gog cal ls --calendars <id>` when you deliberately want a single named calendar. (Backstop: the `calendar-read-guard` plugin rewrites raw `gog cal` reads and piped `fetch_calendar.py` calls to a clean wrapper call — but do not rely on it; call the wrapper directly.)
- All writes (create, update, delete): Confirmation Protocol. No exceptions, including minor updates.
- Never update MEMORY.md as a substitute for a calendar action.
- Location required before drafting a normal event. Ask if not provided — no placeholders. **Preliminary holds are exempt** (see below) — they need only a date + label.
- Search ±7 days before telling the user an event doesn't exist. If a named search returns nothing, retry with progressively simpler single-keyword queries and `--all` before reporting not found. Never ask the user to supply the exact title — that is what searching is for.
- After any write: read back by event ID before reporting completion. If read fails, report and retry. Do not claim the event is on his calendar until confirmed.

### Adding Events — Use the Wrapper Script, Not Raw gog
For any new event (single create, not a rename/update of an existing one), run:
```
python3 ~/.openclaw/workspace/Skills/shared/add_calendar_event.py \
  --who "Person/Activity" --venue "Venue Name" \
  --from "2026-07-15T10:30:00-07:00" --to "2026-07-15T12:00:00-07:00" \
  --tz "America/Los_Angeles" --location "Full Address" \
  [--description "..."] [--attendees "email@domain.com"] [--reminder-minutes 15] [--meet] \
  --propose
```
**From chat, always `--propose`.** the user gets the draft with an Approve button and the event is created only when he taps it. Without `--propose` the wrapper writes immediately — that is only for scripts that run after he has approved, and `calendar-write-guard` blocks it from chat. If the wrapper fails, report the error; never fall back to hand-built `gog calendar create`.
**Google Meet:** pass `--meet` to attach a video conference — gog generates the link server-side (`--with-meet` under the hood). Never ask the user for a Meet URL and never claim one must be supplied before the event can be created; the wrapper's printed JSON includes the link (`hangoutLink`/`conferenceData`) to read back to him.
It builds the `{who} <> the user @ {venue}` title, always sets both timezone flags and an explicit reminder, and verifies the event is retrievable before printing it — do not report the event as added unless this script exits 0. It prints the created event as JSON; read `summary`/`start`/`location` back to the user in the confirmation. This exists specifically because raw `gog calendar create` flags were guessed wrong on nearly every add (see below) — prefer the script over reconstructing the command by hand.

### Preliminary Holds — Date Known, Nothing Else
When the user has a date but no details ("wedding is June 26 2027", a forwarded save-the-date, "just block it off, I'll update later"): place a **hold**, don't interrogate. A hold needs only a **date + a short label** — never ask for time, venue, or location, and do NOT run the Calendar Pre-Flight.

```
python3 ~/.openclaw/workspace/Skills/shared/add_calendar_hold.py \
  --label "Sam's wedding" --date "June 26 2027" \
  [--end-date 2027-06-27] [--source "chat: forwarded save-the-date"] [--chase-days 60] [--no-chase]
```
It creates an all-day, shown-as-**Busy** event titled `HOLD: <label> (details TBD)`, records it in `calendar_holds.json`, and schedules a one-shot chase-up reminder ~60 days out ("still no details for <label> — got them yet?"). Verifies the write with a read-back.

Flow: infer the label + date from what the user gave you → show a **one-line draft** (`HOLD: Sam's wedding (details TBD) — all-day Sat Jun 26 2027, Busy; chase-up Apr 27`) → wait for `✅` → run the script → read back `summary`/`start`. That's the whole ceremony.

When the details land later, **promote** the same event (don't create a second one):
```
python3 ~/.openclaw/workspace/Skills/shared/promote_calendar_hold.py \
  --event-id <id> --who "Sam Example" --venue "Example Venue" \
  --location "Full address" --time 16:00 [--end-time 22:00] --tz America/Chicago
```
Pass only what you have (staged promotion is fine — `--venue` now, `--time` later, add `--keep-hold` to keep the chase-up until it's complete). A full promote fixes the title to `{who} <> the user @ {venue}`, converts all-day → timed, sets the location, and cancels the chase-up. Still a calendar write → Confirmation Protocol → `✅`.

### Updating Events (Reschedule, Rename, Move) — Use the Wrapper Script, Not Raw gog
For any change to an existing event — new time, new title, new location, or any combination — run:
```
python3 ~/.openclaw/workspace/Skills/shared/update_calendar_event.py \
  --event-id <eventId> \
  [--from "2026-08-25T18:00:00" --to "2026-08-25T19:30:00" --tz "America/Chicago"] \
  [--summary "New Title <> the user @ Venue"] [--location "..."] [--description "..."]
```
Only pass the fields actually changing. It verifies the write with a read-back — comparing times as UTC instants, not printed digits — before printing the updated event as JSON; do not report the change as done unless this script exits 0. This is the reschedule path: one call, no delete/recreate. This exists because `gog calendar update` takes `<calendarId> <eventId>` as **positional arguments, not flags** — there is no `--event-id` — and the agent has guessed that flag (plus `--start`/`--end` instead of `--from`/`--to`) on a live reschedule before self-correcting via `--help`; prefer the script over reconstructing the command by hand.

For deletes, or anything the script doesn't cover, use `gog calendar` directly per the exact-flags reference below.

### gog Calendar CLI — Exact Flags (do not guess, do not re-derive from --help)
Recurring failure: the agent repeatedly guesses flag names that do not exist (`--start`, `--title`, `--date`, `--format`), gets a CLI error, and burns several tool calls rediscovering the real ones from `--help` — on every single calendar add. These are the only flag names `gog calendar create`/`update` support for normal event drafting:
- `--summary` (NOT `--title`) — event title
- `--from` / `--to` (NOT `--start`/`--date`) — RFC3339 start/end times, e.g. `2026-07-15T10:30:00-07:00`
- `--start-timezone` / `--end-timezone` — IANA zone, e.g. `America/Los_Angeles`
- `--location`, `--description`, `--attendees` (comma-separated emails, never `--guest-email`)
- `--reminder "popup:15m"` (repeatable, method:duration)
- `--with-meet` — attaches a Google Meet link, auto-generated by Google (no URL to collect from the user)
- `gog calendar update <calendarId> <eventId> --summary ...` to rename (NOT delete/recreate) — `<calendarId>`/`<eventId>` are positional, there is no `--event-id` flag; prefer `update_calendar_event.py` above over calling this raw
- `gog calendar events [<calendarId>]` (aliases: `list`, `ls`) to search/list — flags are `--query`, `--from`/`--to`, `--all`, `--max`, `--calendars <id,id>`; there is no `--format` flag (use `-j`/`--json` or `-p`/`--plain` for output shape). For normal "what's on my schedule" reads use `Skills/shared/fetch_calendar.py` instead (see the mandatory wrapper rule above) — raw `ls` defaults to the Personal calendar only and drops all work events.

Full working example:
```
gog calendar create primary --summary "Person <> the user @ Venue" --from "2026-07-15T10:30:00-07:00" --to "2026-07-15T12:00:00-07:00" --start-timezone "America/Los_Angeles" --end-timezone "America/Los_Angeles" --location "Full Address" --description "Meeting notes" --reminder "popup:15m"
```
If a command still fails, run `--help` once — do not guess a second flag name.

**Reading back a `dateTime`:** gog always prints an event's `start`/`end` `dateTime` digits using the *local system's* offset (`-05:00`, Central) regardless of what that event's own `timeZone` field says — it does not re-render into the event's assigned zone. An LA event genuinely set to 10:00 AM Pacific will print as `"dateTime": "...T12:00:00-05:00", "timeZone": "America/Los_Angeles"` — 12:00 and "Los_Angeles" together look self-contradictory but are correct (17:00 UTC either way). Confirmed 2026-08-26 rescheduling an appointment to 10am PT: the write was correct on the first read-back, but the printed digits alone look like it reverted to noon. Never eyeball the literal digits against the `timeZone` label to judge correctness — convert both to UTC (or just trust a wrapper script's own verification) before concluding a time is wrong and attempting another "fix."

**Rescheduling with `gog calendar update` works — use it, don't delete-and-recreate.** `gog calendar update <cal> <id> --from ... --to ... --start-timezone ... --end-timezone ...` does move `start`/`end`/`updated`. Re-tested gog v0.14.0 on 2026-09-05 across non-recurring events, single instances of recurring series, and calls with no timezone flags — every case moved. The earlier "update silently no-ops on time changes" claim was a misread of the display artifact above: the event under test was already at the target instant, so a correct update genuinely changed nothing, and the Eastern-offset digits made it look untouched. **Reschedule via `update_calendar_event.py` (one call, no delete).** It now verifies by converting the read-back time to UTC and comparing — a clean exit or an eyeball of the printed digits is still not proof. Delete-and-recreate is a fallback for cases the wrapper can't express, not the default — it risks leaving a duplicate or a wrong-`timeZone` copy (e.g. `Etc/GMT+5` instead of `America/Chicago`). Never report a reschedule done off an exit code alone — confirm the read-back `start` equals your target **in UTC** and `updated` advanced.

**Google Calendar's home timezone is deliberately `America/Chicago` and stays that way even while the user is in CA.** Dallas is his home base; short CA trips don't warrant flipping it (and Google nags on each change). The agent's own automation — briefings, workout scheduling, reminders, Hatch alarm — reads location from `hevy_config.json`'s `timezone` (via `current_location.get_tz()`), which travel-detection flips to Pacific independently, so those stay correct regardless. The only effect of the Chicago setting is that the Google Calendar *web* UI displays CA events +2h and bare-time quick-adds on web are read as Central. Do NOT flag this as a bug or offer to "fix" the calendar timezone on every trip. Only change it if the user explicitly says he's relocating.

### Calendar Event Format
- Title format: `{Person/Activity} <> the user @ {Venue Name}` — curly braces mark placeholders; the final title contains NO brackets of any kind. ✅ `Wedding Picnic Hangout <> the user @ Riverside Park` ❌ `[Wedding] Picnic Hangout <> the user @ Riverside Park`. The user always second, venue name only in title (no city/state/zip). Full address in `--location` flag.
- Reminder: `--reminder "popup:15m"` on every event. Never rely on useDefault.
- Timezone: always pass `--start-timezone` and `--end-timezone`. Use event's physical location: `America/Chicago` (Dallas/default), `America/New_York` (Eastern), `America/Los_Angeles` (Pacific). When traveling, use timezone of where the event takes place — not home.
- Always pass `--location` flag even if location is in title or description.
- **Calls:** `--location "Call"`, `@ Call` in title, phone in description. 30 min default.
- **Zoom/video:** `--location "Zoom"` (or platform), full link in description, `@ Zoom` in title.
- **Google Meet:** use `--meet` (wrapper) / `--with-meet` (raw gog) — gog generates the link itself. Never ask the user for a Meet URL or claim one must be provided first.
- No link or number provided: ask before drafting. (Does not apply to Google Meet — it's auto-generated, never ask.)
- **Attendees:** `--attendees email@domain.com`. Never `--guest-email` — it silently drops the attendee.
- **Externally-organized invites (Calendly, invites from other people):** these never pass through the format rules above since your assistant didn't create them. `detect_bad_invites.py` runs daily (via the morning briefing cleanup step) to find them and propose a corrected title via `confirm:calrename`/`skip:calrename`. Renaming only updates the user's own copy of the event via `gog cal update --summary` — no accept/delete/recreate needed, and the organizer's copy is untouched.

See **Calendar Pre-Flight** section at top of this file — run it before every draft. "Proper formatting" = apply this format. Never ask about format style; ask only for missing location if absent.
