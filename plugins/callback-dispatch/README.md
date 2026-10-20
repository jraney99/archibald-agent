# callback-dispatch

Deterministic handler for Telegram inline-button callbacks (`confirm:*` / `skip:*`).

## The problem it fixes

A button tap arrives at OpenClaw as a synthetic plain-text message
(`callback_data: confirm:hydration-32`) and, with nothing claiming it, is routed
to the agent like any typed message. The agent then has to:

1. recognise a bare token dropped into the middle of an unrelated conversation, and
2. match it against the prose table in `workspace/RULES.md`

…on the weakest model config (Haiku, `thinking: off`), sometimes on a
continuation turn where `contextInjection: "continuation-skip"` means RULES.md
isn't even in the system prompt. It misfires a few percent of the time —
"the callback appears stale", re-answering the previous topic, or a false
"logged" with no script run. Documented incidents: 2026-07-13 (`confirm:cal`),
2026-08-17 / 08-20 / 08-21 (`confirm:hydration-32` ×several), 2026-08-26
(`confirm:cal` again), 2026-08-30 (`confirm:hydration-32`, blamed on an unrelated topic).

Every prior fix (RULES.md hardening, the per-skill reconciler cron, the
`hooks/hydration-dispatcher` internal hook) was a patch on the same hole: a
structured UI event downgraded to text and re-parsed by an LLM.

## How it works

Registers a **`before_dispatch`** claiming hook (priority 100). It runs **before
the agent turn**. If the inbound text is a known `confirm:` / `skip:` callback it
runs the mapped script itself and returns `{ handled: true }`, so the agent
never sees it. No model, no thinking budget, nothing to misread.

Anything that isn't a `confirm:` / `skip:` token is ignored and falls through to
the agent unchanged.

## Files

- `dispatch-table.json` — the callback → command map. **Source of truth.** Keep
  it in sync with the table in `workspace/RULES.md` ("Inline Button Callbacks").
- `index.mjs` — the hook.
- `openclaw.plugin.json` — manifest.

### dispatch-table.json entry shape

```jsonc
"confirm:vacation":      { "argv": ["hevy-workout/scripts/confirm_vacation.py"], "reply": "report" }
"skip:vacation":         { "unlink": "pending_vacation.json", "reply": "Skipped. 🎩" }
```

- `argv[0]` is relative to `~/.openclaw/workspace/Skills`; run as
  `python3 -W ignore <script> <args…>`.
- `unlink` (optional) — path relative to `~/.openclaw`, deleted best-effort.
- `timeoutMs` (optional) — per-entry exec timeout; default 20 000.
- `reply`:
  - `"script"` — run, relay stdout, **unless stdout is exactly `NO_REPLY`**
    (the script edited its own Telegram message) → stay silent.
  - `"report"` — run, relay stdout (with a trailing 🎩).
  - any other string — literal reply text, sent after `argv`/`unlink` run.

`patterns[]` entries are the same but keyed by a regex; `$1`, `$2` in `argv` are
replaced with capture groups (`confirm:hydration-(\d+)` → `--oz $1`).

Unknown `confirm:` / `skip:` tokens get RULES.md's generic reply
("No pending action found." / "Skipped.") and are still claimed — never passed
to the agent as conversation.

## Config

`openclaw.json`:

```jsonc
"plugins": {
  "allow": [ …, "callback-dispatch" ],
  "load":  { "paths": ["~/.openclaw/plugins"] },
  "entries": { "callback-dispatch": { "enabled": true } }
}
```

After editing `index.mjs` or the table: `openclaw gateway restart`
(hot config-reload picks up enable/disable, not code changes).

## Verify

```bash
openclaw plugins inspect callback-dispatch --runtime --json | \
  python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["typedHooks"])'
# -> [{'name': 'before_dispatch', 'priority': 100}]
```

Live: tap any quick-add button; the gateway log shows
`[callback-dispatch] <token> -> <script> ok` and the agent does **not** run
(`reason: before_dispatch_handled`).

## Backstop (transitional)

`workspace/Skills/hydration/scripts/check_hydration_callbacks.py` (cron
`hydration-callback-reconcile`, every 5 min) stays as a safety net. It dedupes
against `hydration_log.json` so it can't double-log. Remove it once this plugin
has a week of clean operation.

## Typed reply to a meal nudge (2026-09-18)

`reply-nudge.mjs` — if an inbound owner message is a Telegram *reply* to a message id listed in
`~/.openclaw/meal_nudge_index.json` (written by `nutrition/scripts/send_meal_reminder.py`), the hook
runs `nutrition/scripts/confirm_meal.py --date <d> --slot <n> --action eaten --what "<his words>"`
and claims the message. His words are passed as one argv item (no shell) and stored as typed. Replies
ending in `?`, replies to anything else, and non-replies fall through to the agent. Test:
`node reply-nudge.test.mjs`. Like the dispatch table, the plugin code loads at gateway start — restart
after editing it.
