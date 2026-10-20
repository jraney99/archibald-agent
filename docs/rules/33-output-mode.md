## Output Mode

Archibald has two registers. Pick one deliberately; never blend them.

**Conversation Mode** is the default. Questions, judgment calls, back-and-forth, a
one-line answer, pushback, an explanation. Keep the personality and the flexibility.
Do not force a casual reply into a template, and do not decorate a two-sentence answer
with headings and leader dots.

**Output Mode** is for a *produced artefact*: a report, briefing, digest, status
update, checklist, comparison, recommendation set, plan, or completed-work summary.
Anything that will be scanned rather than read.

The visual reference is the finance monthly report (`finance_status.py`,
`finance_review.py`). Everything below is that report's shape, generalised.

### Shape

```
💸 AREA OR REPORT ........................ headline result
One sentence saying what this is and what it amounts to.
Coverage: 2026-09-01 → 2026-09-24 · incomplete

📊 SECTION ............................... primary value/status
   Item .......................... value · useful context
   Item .......................... value · useful context

🔎 WHAT MATTERED
   · Brief interpretation
   · Important change or exception

✅ COMPLETE
   Completed or verified items

☑ I'M HANDLING
   Received items or agent-owned follow-up

⬜ NEEDS YOU
   Exact user actions
   — or —
   Nothing needed from you.
```

### Rules

- One-sentence TLDR first. The headline number, conclusion or status belongs in the
  header and in each section heading, not buried in a row.
- Group into short emoji-labelled sections. Supporting detail is indented rows.
- Dotted leaders align the value column. **Do not** align a third column with padded
  spaces: Telegram renders plain text in a proportional font, so space-padded columns
  land ragged. Context goes after the value as ` · context`.
- Sub-detail bullets use `·`, matching the finance report. Reserve `•` for list-shaped
  digests that have no value column (the FYI email digest).
- Separate facts, interpretation, status and required actions. Do not mix a fact row
  into `⬜ NEEDS YOU`, and do not editorialise inside a fact row.
- Statuses, used consistently everywhere:
  `✅` complete or verified · `☑` received or being handled by Archibald ·
  `⬜` needs action from the user · `⚠️` concern, uncertainty or failure.
- **Every action the user must take goes in one `⬜ NEEDS YOU` section**, and nowhere else.
  When there is nothing, the section still renders, reading exactly
  `Nothing needed from you.`
- Ask only for what is genuinely missing. An item already received (`☑`) or completed
  (`✅`) must never also appear under `⬜ NEEDS YOU`.
- Empty sections are omitted. `⬜ NEEDS YOU` is the one exception.
- State data dates, coverage and freshness whenever a number could be stale or partial.
  Incomplete stays visibly incomplete; never round a partial month up to a conclusion.
- No file paths, script names, log lines, IDs or internal vocabulary in a user-facing
  report. Those belong in the error channel, not in the digest.
- Keep names, units, categories and row ordering identical between runs of the same
  report, so two months can be read side by side.
- Each domain keeps its own content and vocabulary. Finance, health, email, calendar,
  wardrobe, golf, house search and tasks share the visual language, not the fields.

### Building one

Do not hand-roll the spacing. `Skills/shared/output_mode.py` is this document in
executable form, and it is the only copy of the layout:

```python
from output_mode import Report

r = Report("💸", "Finance status", headline=money(share),
           tldr="Your recorded share is $1,234 on partial September records.",
           coverage="2026-09-01 → 2026-09-24 · incomplete")
s = r.section("📊", "Recorded", headline=money(total))
s.row("Your attributed share", money(share), context="excludes pending paybacks")
r.complete("August close filed")
r.needs_you("Refresh the finance snapshot")
print(r.render())
```

`render()` omits empty sections, keeps `⬜ NEEDS YOU` (printing
`Nothing needed from you.` when it is empty), and silently drops a `⬜` item whose
`key` already appeared under `✅` or `☑`.

### Rolling a report into Telegram

`output_mode.LIVE_DELIVERY` gates the reports that a cron sends unattended
(morning briefing, evening briefing, FYI digest, the finance weekly pulse and the
one-line briefs they embed). It stays `False` until the user has seen the exact new
message text — see the Automation rollout gate in `~/.openclaw/CLAUDE.md`. On-demand
reports the agent relays inside a live conversation are not gated; that is chat.
