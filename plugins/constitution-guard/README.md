# constitution-guard

A `before_tool_call` hook on the `exec` tool that blocks Archibald from (1)
writing, deleting, or unlocking his own governing files — SOUL.md,
IDENTITY.md, SECURITY.md; (2) directly tampering with the Hard Stop
mechanism's own state (`hard_stop_pending.json`, `yubikey_credential.json`);
or (3) changing OpenClaw's own settings — `openclaw.json`,
`exec-approvals.json`, or the `openclaw` CLI commands that change config,
plugins (including switching off this guard), command approvals, channels,
pairing and devices. He proposes changes to the user in conversation; the user applies
them, directly or by approving Archibald to apply them under his explicit
direction. Never on Archibald's own initiative, never as a side effect of
another task.

(2) and (3) were added 2026-09-12 after a Hard Stop list gap review found
that protecting only the constitution files left an obvious way to defeat
the whole Hard Stop system at once: fake an "approved" status by editing
`hard_stop_pending.json` directly, swap in a controlled key via
`yubikey_credential.json`, or just turn a guard plugin off in config. All
three are now caught the same way.

## Defense in depth — two independent layers (on the constitution files only)

1. **This plugin** — a deterministic `exec` command inspector, same pattern
   as `calendar-write-guard` / `email-send-guard`. Catches redirection, `tee`,
   `cp`/`mv` into the file, `sed -i`, `rm`/`trash`, Python `open(...,'w')` /
   `write_text`, `jq -i`, and — importantly — `chflags` attempts to clear the
   flag below.
2. **`chflags uchg` on SOUL.md, IDENTITY.md, SECURITY.md** (set 2026-09-12).
   A real OS-level write/delete block, independent of this plugin and immune
   to however cleverly a write is phrased. The one thing it doesn't stop on
   its own: the user owns these files, so `chflags nouchg` on them succeeds
   without sudo — which is exactly why this plugin also blocks that specific
   command.

Neither layer alone is complete. The plugin can have a gap in its patterns;
the OS flag can be cleared by the file's owner. Together, clearing the flag
requires a command this plugin blocks, and any write this plugin somehow
missed still fails at the filesystem level.

The Hard Stop state files and `openclaw.json` are **not** chflags-locked —
the state files need to stay writable for `yubikey_auth.py`'s own normal
operation, and `openclaw.json` has other legitimate writers (plugin
installs, `openclaw configure`) this plugin doesn't try to block. Only the
narrow, dangerous edits are caught; see the table below.

## What it does

| Situation (exec command) | Action |
| --- | --- |
| A write-like command that also mentions `SOUL.md`, `IDENTITY.md`, or `SECURITY.md` | **block**, propose the change to the user instead |
| A write-like command that also mentions `hard_stop_pending.json` or `yubikey_credential.json` | **block**, use `yubikey_auth.py`'s own subcommands instead |
| A write-like command that mentions `openclaw.json` or `exec-approvals.json` (including `.last-good`/backup copies) | **block** |
| Any `openclaw` CLI call in a settings group (`config`, `plugins`, `approvals`, `exec-policy`, `channels`, `pairing`, `devices`, `nodes`, `hooks`, `agents`, `secrets`, `mcp`, `skills`, `models`, `gateway`/`daemon`, `update`, `webhooks`) other than its read-only subcommands; `configure`/`onboard`/`setup`/`reset`/`uninstall`/`crestodian`/`migrate`; `doctor`/`security` with `--fix`/`--repair`/`--yes`/`--force` | **block** |
| Any mention of `scripts/rotate_secret.py` (the user's own secret-swap tool) | **block** |
| Text typed into a running session with the `process` tool (`write`, `paste`, `send-keys` incl. `keys`/`hex`) | same checks as `exec` |

"Write-like" = `>`, `>>`, `tee`, `cp`, `mv`, `sed -i`, `rm`, `trash`,
`chflags`, `open(...,'w')`, `.write(`, `.write_text(`, `truncate`, `jq -i`.

## What it leaves alone

Reads of any protected file or state file (`cat`, `grep`, `head`, `wc`, the
Read tool), `python3 .../yubikey_auth.py <subcommand>` (its own writes never
name the state file on the command line, so they're invisible to this
check), read-only `openclaw` commands (`config get`, `plugins list`,
`approvals get`, any `--help`), everything in `cron`, `browser`, `message`
and other non-settings groups, and writes to every other file in the repo — including RULES.md, MEMORY.md,
and everything under `Skills/`, which Archibald is meant to keep maintaining
himself.

**Deliberately not exempted:** cron/job runs. Every other guard in this repo
skips `ctx.jobId` because cron-driven calendar/email writes are common and
legitimate. There is no legitimate cron job that should ever rewrite
Archibald's own constitution, so an unattended attempt is treated the same as
an ad-hoc one.

## Scope, honestly

Pattern matching, not a sandbox. A deliberately disguised command
(base64-decoded, assembled from shell variables, or typed in pieces across
several `process` calls) can get past it; it stops the plain, planted
instruction, which is the realistic prompt-injection case. Archibald's
`exec` runs with `security=full ask=off` (`openclaw approvals get`), so this
plugin is the only check on those commands. `openclaw.json` is not
chflags-locked because the gateway itself rewrites it (e.g. `.last-good`
promotion). `dispatch-table.json` is still not covered.

## Re-locking a file after the user edits it

`chflags uchg` has to be lifted for anyone — including the user — to edit the
file, then re-applied:

```
chflags nouchg ~/.openclaw/workspace/SOUL.md
# ... edit it ...
chflags uchg ~/.openclaw/workspace/SOUL.md
```

## Test

```
node test.mjs
```

The core logic is the pure `inspectConstitutionWrite(cmd)` export.

## Structured Tools (2026-09-14)
The registered hook now also inspects deployed `write(path, content)`, `edit(path, edits[])`, legacy file parameters, and `apply_patch` add/update/delete/move paths. Direct file-tool edits to constitution files, Hard Stop state, OpenClaw config and guard-plugin code are blocked. This is defense in depth, not an OS sandbox or protection against all possible arbitrary-code indirection. Existing immutable constitution flags remain intact.

## OpenClaw settings (2026-09-16)
Found while reviewing the Telegram token swap: `openclaw plugins disable
constitution-guard` and `openclaw config set channels.telegram...allowFrom`
both passed the old check, which only caught file edits naming a guard plugin
id. The CLI rule is an allowlist per group (`SETTINGS_COMMANDS` in
`index.mjs`), so a subcommand added by a future OpenClaw is blocked until
reviewed. Nested calls are caught too (`openclaw cron add --command-argv
'["openclaw","plugins","disable",...]'`). Replaying Archibald's 33,759 past
exec commands: everything the old rule blocked is still blocked, and the only
newly blocked command was one `openclaw models set`. The user makes settings
changes himself, in his own terminal.
