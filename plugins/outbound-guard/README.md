# outbound-guard

A `before_tool_call` hook on every tool that blocks the routes a
prompt-injected Archibald could use to send the user's data off the Mac, and asks
the user before a calendar invite goes to someone new. Built 2026-09-17 (✅ P1)
after an injection review.

## Why

Archibald holds private data (finance, health, email), reads untrusted text all
day (email bodies, calendar invites from outsiders, web pages), and runs shell
commands with exec `security=full ask=off`. The only mechanical send check was
`email-send-guard`, which matches `gog gmail send|reply|autoreply` only. Tested
before this plugin, all of these passed:

- `gog gmail forward` / `fwd`, the `gog mail` / `gog email` aliases, top-level `gog send`
  (Archibald's history has 40 sends through the `email` alias, from before the ✅ flow)
- `gog gmail drafts send|post`
- Gmail forwarding, auto-forward, filters, delegates, send-as, vacation settings
- `gog drive share`, calendar invites to strangers
- `curl`/inline code to any website

## What it does

| Situation | Action |
| --- | --- |
| A live secret (from `openclaw.json`: env.vars, bot tokens, gateway token) in any tool call's arguments — command, file content, URL, sub-agent task | **block** |
| gog send/reply/autoreply/forward/fwd under `gmail`/`mail`/`email`, `gog send`, `drafts send|post` | **block** — email leaves only via the ✅ flow |
| Gmail `settings`/`watch`/`track` writes; calendar `acl` writes; `drive share`, Drive/Docs `comments` writes; Google Chat writes; `appscript`, `auth`, `config`, `admin`, `groups` other than reads; `login`/`logout`; `--access-token` | **block** |
| Calendar guests (`--attendees`, `--add-attendee`, on gog or the calendar wrappers) not in `known_invitees.json` or the user's own addresses | **ask the user** (allow-once / deny; denied on timeout or if no approval surface is available) |
| `openclaw` message/agent/cron with `--to`/`--target` other than the user's chat | **block** |
| `curl`/`wget` without a literal host in `EGRESS_HOSTS`, or inline Python/Node/shell code (heredoc, `-c`, `-e`) using network libraries or curl the same way | **block** |
| nc, ncat, netcat, socat, telnet, ftp, sftp, scp, rsync, ssh, mail, sendmail, mutt, msmtp, dig, nslookup, host, ping, gh; `git push`, `git remote add|set-url`; `shortcuts run`; `smtplib`; AppleScript sending via Mail/Messages; `open mailto:/sms:/imessage:` | **block** |
| web_fetch / browser / image URL: `api.telegram.org`, query+fragment ≥ 120 chars, an unbroken token ≥ 48 chars, a host label ≥ 40 chars | **block** |
| Text typed into a session with the `process` tool | same checks as exec |

## What it leaves alone

Reads of anything; web_fetch of ordinary pages on any site; skill scripts (their
network calls are fixed code); `send_telegram.py` (hard-coded to the user's chat);
the ✅ `send_pending.py` path; RSVPs; gog writes to the user's own data (labels,
archive, drafts, tasks, contacts, events without new guests); URLs written into
files. Cron/job runs are not exempt — agent-turn crons read untrusted mail too.

## Evidence it doesn't get in the way

Replaying Archibald's 33,759 past exec commands (plus ~6,000 structured tool
calls) blocked only: 348 raw gmail sends that `email-send-guard` already blocks,
40 sends through the `gog email` alias, 14 one-time `gog auth` setup commands,
and the 2026-08-25 PlayByPoint script that pasted the password into a file. All
138 past calendar invites went to the 3 addresses seeded into
`known_invitees.json`. Archibald's 274 past network commands only reached hevy,
todoist and open-meteo; its web_fetch URLs peaked at a 92-char query, a 38-char
token and a 19-char host label.

## Maintaining it

- **A new regular invitee:** the user adds the address to `known_invitees.json`.
  Archibald can't: `constitution-guard` blocks writes under `plugins/<guard-id>/`
  and to that file by name.
- **A new service Archibald must call ad hoc:** add its host to `EGRESS_HOSTS`
  deliberately. Prefer a skill script (fixed code) over ad-hoc calls.
- Any edit needs a gateway restart (`launchctl kickstart -k gui/$UID/ai.openclaw.gateway`).

## Limits

Pattern matching, not a sandbox. A short secret in a normal-looking URL, a
command assembled from variables or encoded, or data split across many requests
can get past it. It stops the plain planted instruction. Stronger options:
a more injection-resistant model for the main agent, and moving secrets out of
`openclaw.json` into OpenClaw SecretRefs (`openclaw doctor --lint` recommends
this) so Archibald can't read them at all.

## Test

```
node plugins/outbound-guard/test.mjs
node test-fixtures/test-all.mjs
