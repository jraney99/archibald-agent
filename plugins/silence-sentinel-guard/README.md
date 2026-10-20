# Silence Sentinel Guard

## Why it exists

2026-10-09: The user received a wall of the email daemon's internal `[Cal]` log lines, ending:

```
NO_REPLY 🎩
```

`NO_REPLY` is the gateway's silence signal and is matched **exactly**. The trailing hat
broke the match, so what should have been silence was delivered to his phone — daemon
logs and all.

Two rule files already forbade this:

- `AGENTS.md` — *"output exactly `NO_REPLY` with no 🎩 and no other text"*
- `Documentation/rules/15-communication-style.md` — *"never add 🎩 to silence sentinels …
  Adding 🎩 breaks the gateway's silence detection and delivers the message to Telegram."*

A rule written down twice and still violated is the signal for a guard
(`plugins/GUARD-PATTERN.md`). Ending a Telegram message with a hat is the trained
reflex; the sentinel is the rare exception. That is precisely the shape that keeps
getting got wrong.

## What it does

`before_agent_finalize`. If the reply is a sentinel carrying anything else, it sends it
back once with an instruction to emit the bare token. Two cases:

- **decorated** — `NO_REPLY 🎩`, `"NO_REPLY"`, `**NO_REPLY**`, `NO_REPLY.`
- **trailing** — the sentinel alone on the last line, under other output (the Cal dump)

## What it deliberately does not touch

**Prose about the sentinel.** A reply explaining what `NO_REPLY` is, or quoting the rule
that forbids the hat, is left alone. The first version tested whether the reply *ended
with* the token and fired on:

> never add 🎩 to silence sentinels like NO_REPLY or HEARTBEAT_OK

A guard that cannot survive a discussion of its own subject is not shippable, so the
trailing case now requires the sentinel to be its **own last line**.

**Whitespace padding.** `"  NO_REPLY  "` trims to the bare sentinel and there is no
evidence the gateway rejects it. Firing there would cost a revision pass for something
that probably already works.

## Tests

`node plugins/silence-sentinel-guard/test.mjs` — the real Cal-log message, every
decoration, and four pieces of prose about sentinels that must pass untouched.
