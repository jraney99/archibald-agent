# Archibald — a production personal AI agent

Archibald is the AI agent I run every day. It lives on my Mac, talks to me on Telegram (in the
voice of a butler, Alfred Pennyworth style), and handles real logistics: it triages my email,
drafts replies, schedules calendar events, books restaurants and tee times, runs my bedroom lights,
alarm, bed cooler and thermostat, and tells me when to leave for the airport.

This repository is a **curated, sanitized extract** of that deployment: the skills, the guard
plugins and the rulebook. It shows how I design, harden and operate an agent that acts in the
real world on someone's behalf. The emphasis is on *reliability*: everything here exists because
something went wrong in production, or because I wanted to stop it from going wrong.

> **Note:** This code is shown for reading, not cloning. It runs on the
> OpenClaw agent runtime with private config, credentials and data
> that are not included. Personal details have been replaced with placeholders (`the user`,
> `owner@example.com`, `Master Wayne`).

---

## What it shows

### 1. Code enforces the rules; prose describes them
A rule written in the prompt is a request. A model on a fast, low-context configuration will
eventually skip it. When a prose rule has been broken more than once, I move it into a
**deterministic guard**: a plugin that runs before the model's decision or before a tool
executes, and either handles the event itself or blocks it with a self-correcting message.

| Guard | What it stops |
|---|---|
| [`outbound-guard`](plugins/outbound-guard) | Prompt-injection exfiltration: email forward/share aliases, Gmail forwarding changes, unapproved hosts, data-carrying URLs, live secrets in tool calls |
| [`claim-guard`](plugins/claim-guard) | Replies that claim "saved/scheduled/logged" in a turn where no state-changing tool succeeded. Built after an audit found script-produced counts were right 10/10 and model-claimed ones were wrong every time |
| [`prompt-forgery-guard`](plugins/prompt-forgery-guard) | The model *typing* a prompt that only a script is allowed to send (a typed prompt records nothing, so the answer is lost) |
| [`callback-dispatch`](plugins/callback-dispatch) | Telegram button taps being misread as conversation; runs the confirm/skip script before the model sees it |
| [`email-send-guard`](plugins/email-send-guard) | Ad-hoc email sends; a bare ✅ deterministically sends only the pending, approved draft |
| [`constitution-guard`](plugins/constitution-guard) | The agent editing its own constitution, safety files or runtime config. *Archibald proposes; the human applies* |
| [`trading-guard`](plugins/trading-guard) | Any brokerage order without a one-time human "Allow" tap (deny on timeout, never from a cron) |
| [`calendar-read-guard`](plugins/calendar-read-guard) / [`calendar-write-guard`](plugins/calendar-write-guard) | Invented CLI flags, missing time zones, and delete-then-recreate loops on calendar events |
| [`silence-sentinel-guard`](plugins/silence-sentinel-guard) | A "say nothing" token with stray text attached, which would otherwise deliver raw logs to the user |
| [`memory-health-guard`](plugins/memory-health-guard) | Structured records being written into free-text memory instead of the scripts that own them |

The pattern itself is written up in [`plugins/GUARD-PATTERN.md`](plugins/GUARD-PATTERN.md):
pure, exported decision functions; a thin hook wrapper; and tests that replay the *real*
deployed hook argument shapes rather than a convenient stand-in.

### 2. Scripts own state; the model narrates
Anything that has to be true — a count, a schedule, a saved record — comes from a script and is
reported, not composed. The model chooses which script to run and phrases the result.
Examples: [`skills/shared/data_paths.py`](skills/shared/data_paths.py) is the only way any script
locates a data file (config / data / state per skill, enforced by a layout test);
[`skills/shared/approval.py`](skills/shared/approval.py) issues short-lived, single-use approvals
bound to a SHA-256 fingerprint of the exact pending action, so a ✅ can't approve something other
than what was shown.

### 3. Tiered actions and confirm-first by default
[`docs/rules/03-action-tiers.md`](docs/rules/03-action-tiers.md) and
[`04-confirmation-protocol.md`](docs/rules/04-confirmation-protocol.md) define what the agent may
do on its own, what needs a one-tap approval, and what it may never do. Every booking, send,
cancel and purchase in these skills is proposed with inline buttons and executed only on an
explicit tap. Buttons edit their message in place, so the chat doesn't fill with duplicates.

### 4. Cost-aware model use
Cheap models for high-volume, low-stakes classification (email triage on Haiku), stronger
models only where quality matters (reply drafting on Sonnet).
[`skills/shared/bounded_model.py`](skills/shared/bounded_model.py) caches only *validated*
classifications and refuses oversized inputs instead of silently truncating them.

### 5. Tests that can't hurt anyone
Agents that send messages need tests that provably can't. Test suites run under
`TELEGRAM_SEND_DISABLED=1`, and tripwires make any attempted send fail loudly. That rule was
added after a test once delivered a fake message to a real phone.

### 6. A rollout gate for anything user-facing
No new automated message goes live until the human has seen the exact text and the exact trigger
and approved both. Bug fixes that restore approved behavior are exempt; new wording or new
triggers are not.

---

## Repository map

```
plugins/            deterministic guard plugins (JS, OpenClaw hook API) + GUARD-PATTERN.md
docs/               the agent's rulebook: RULES.md index, SECURITY.md, AGENTS.md, rules/*.md
skills/shared/      infrastructure every skill uses: data paths, approvals, Telegram send,
                    proposals, misfire guard for cron timing, calendar fetch, drive time
skills/<skill>/     SKILL.md (what the model reads) + scripts/ (what actually runs) + tests
```

### Skills included

| Skill | What it does | Worth looking at |
|---|---|---|
| `email-monitor` | Long-running daemon: classify, file, draft, propose; ✅-to-send | Rulebook-generated Gmail filters, VIP bypass, prompt-injection handling, the largest test suite |
| `airport-timing` | When to leave for a flight, worked back from gate close | p85 security wait, per-terminal walk times, a feedback loop from logged trips |
| `flight-deal-tracker` | Watches fares for known trips and recurring routes | Calendar-driven trip detection, threshold alerts |
| `restaurant-booking` | OpenTable search / book / cancel via browser automation | Every booking gated behind a ✅ |
| `golf-booking` | Tee times at an approved course list | Dry-run by default, approved-list scope |
| `hatch-restore` | Sound machine + wake alarm, calendar- and travel-aware | Wake time drives the lights, bed and thermostat |
| `govee-lights`, `zengge-lights` | Sunrise/sunset scenes, on-demand overrides | Reconciling an unreliable device-state API; BLE control |
| `chilipad`, `nest-thermostat` | Bed cooler and thermostat schedules | **Hard-coded physical safety bounds** in the client, not in config |
| `reminders`, `to-do`, `loop-closer` | Capture → follow-up → verified completion | "Open loops": the agent chases what *it* asked for and never got |
| `golf-putting` | A nightly habit cue with one-tap logging and a streak | Small, complete example of a cue → log → streak loop |
| `ai-wardrobe-steward` | Closet inventory, outfit proposals, purchase advice | A learning loop from wear feedback; an eval harness |

Not included: skills covering health, finances, relationships, job search and contacts, which are
personal by nature, and skills that came from the community registry rather than from me.
Some code still references those skills by name.

---

## How I work with the agent

- **Incident → root cause → enforcement → test → note.** When the agent gets something wrong,
  I find out why it was *able* to (the missing guard, the ambiguous rule, the unowned file),
  fix that, add a regression test, and record the lesson so the next session starts from it.
  I don't patch symptoms with retries or watchdogs.
- **Ask what the human actually does.** The best fix to "the sunrise lights keep failing" was
  noticing I'd unplugged the lamp because it ignored my wake time.
- **Pair-build with coding agents, review as an owner.** Most of this was written in sessions
  with Claude Code, under standing rules for cost, approval and rollout. I set the design,
  review the diffs and own the outcome.
- **Measure before optimizing.** Token spend turned out to be dominated by cache writes of a
  growing session history, not by long prompts. The fix was size-triggered compaction, not
  shorter instructions.
