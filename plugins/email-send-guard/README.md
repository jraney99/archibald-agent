# Email Send Guard

Deterministic twin of `callback-dispatch` / `calendar-write-guard` for the email-send boundary. Built 2026-09-11 off a system audit finding: RULES.md's single most emphasized line — a bare `✅` sends the pending email draft, "non-negotiable, overrides every other rule" — was enforced purely by the model correctly reading a free-text Telegram message. The write-side ban on `gog gmail send/reply/autoreply` had the same gap: documented, prose-only, no code backstop, despite a real 2026-09-10 incident where it was used anyway and falsely reported as sent.

## What it does

- **`before_dispatch`**: only the owner in the default private Telegram chat may approve. A bare checkmark must reply to the exact proposal; unbound checkmarks ask for a target. Buttons/replies resolve a 15-minute, single-use approval ID bound to kind, target and payload hash. Changed/expired/consumed approvals fail closed. Legacy generic buttons are stale. Reissue with `workspace/Skills/shared/show_pending_proposal.py email --id <gmail-id>` or `cal`; never select the latest draft.
- **`before_tool_call`** (exec) — blocks ad-hoc `gog gmail send|reply|autoreply` outside a cron/job run, pointing at the sanctioned draft + ✅ flow instead.

## Deliberately not touched

- A `✅` alongside other text (e.g. "✅ looks good") — ambiguous enough (could be approving something else entirely) that catching it here risks a false-positive send; still handled by the agent following RULES.md directly.
- Cron/job-driven `gog gmail` calls (`ctx.jobId` set) — vetted scripts, same scope `calendar-write-guard` uses.
- `--help`, and `send_pending.py` itself.

## Tests

```
node test.mjs
```

Pure logic (`isBareCheckmark`, `formatSendReply`, `inspectGmailSend`) is exported from `index.mjs` and tested directly — no live Telegram/Gmail calls.
