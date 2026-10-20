# prompt-forgery-guard

The agent may not **type** a message that only a script may send.

## What happened (2026-09-26)

The user got this three times:

```
💳 Card A (••0000)
$45.00 · RIDESHARE* PENDING EXAMPLE CITY
2026-09-26 01:28 PM ET
→ Dining — what was the occasion?
```

No script sent any of them:

- the ledger row said `Groceries`, never `Dining`
- `.merchant_answers.json` held exactly one answer for that merchant — `Groceries`, 15:23:32
- `finance-txn-watch` reported `context_prompts: 0` on every run that afternoon
- `resolve_context()` cannot send a new message at all: it edits the existing one, and only
  for a social category (Groceries is not one)

The agent had read the rule file documenting this template earlier in the same session, then
reproduced it with a plausible-looking category filled in. It was running Haiku 4.5, which
also emitted byte-identical replies twice on three separate occasions in that session.

## Why a forgery is worse than a wrong answer

A forged prompt is indistinguishable from a real one, so it is trusted. It invites a tap or a
reply that answers a question nothing asked, about data that is wrong. The user reported it as a
finance bug, which is the reasonable reading — the message looked official.

## How it works

`before_agent_finalize`, priority 65 (after claim-guard's 60). A forgery needs the script's
**layout**, not merely its words: a `💳` header line **plus** either an owned question phrase
or a `→ <verdict>` line. Requiring both is what lets the agent still talk *about* these
prompts — "you got an occasion prompt for that charge" has no `💳` header and passes untouched.

On a hit it returns `{action: "revise"}` rather than blocking, so a reply mixing real content
with a forged prompt is rewritten instead of lost, and a false positive costs one extra model
pass instead of a dropped message.

`OWNED_PHRASES` is asserted against the scripts that own each phrase in `test.mjs`, so a
renamed prompt fails the suite instead of leaving this guard watching for text nothing sends.

## Relationship to callback-dispatch

This is its outbound twin. `callback-dispatch` stopped the model improvising on **inbound**
button taps by making them deterministic. This keeps script-owned message templates
deterministic on the way **out**. Neither is a watchdog over a flaky process; both draw a
boundary the model cannot cross.

## Tests

```
node plugins/prompt-forgery-guard/test.mjs
```
