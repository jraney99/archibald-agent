# The deterministic-guard pattern

Seven plugins now follow this shape: `calendar-read-guard`, `calendar-write-guard`,
`callback-dispatch`, `email-send-guard`, `constitution-guard`, `memory-health-guard`,
`outbound-guard`. Written up 2026-09-11 (system audit, cross-skill
pass) so the *next* dangerous-action guard gets built the same way instead of reinvented.

## When this is the right tool

A recurring, specific failure where the model — usually on a fast/low-context config — either
misreads a deterministic signal it should never have to interpret (a button tap, a literal
"✅"), or hand-constructs a risky raw command instead of using the sanctioned wrapper (an
invented `gog calendar` flag, a banned `gog gmail send`). If RULES.md already has a prose rule
for it and that rule has been violated more than once, that's the signal — a paragraph a model
might skip is not the same enforcement tier as code that runs before the model sees the
decision at all.

Not the right tool for: something that genuinely needs judgment every time (there's no
deterministic rule to encode), or a one-off mistake with no recurrence.

## The two hook shapes

- **`before_tool_call`** — fires before tool execution, including structured writes.
  The handler receives `(event, context)` as separate arguments. Inspect the actual
  `event.params` schema (`command` for exec; `path`/`edits[]` for deployed edits);
  return `{ block: true, blockReason }` to stop it outright (self-
  correctable — tell the model exactly what to use instead) or
  `{ requireApproval: {...} }` to pause for a real approval decision (state that persists
  across the session, e.g. `calendar-write-guard`'s recreate-loop detector).
  Read session identity from the separate context. Do not introduce guard exceptions
  based on assumed runtime fields; verify the deployed contract first.
- **`before_dispatch`** — fires before the agent turn on an inbound message, chat or a
  Telegram callback alike. Inspect `event.content`/`event.body`; return
  `{ handled: true, text }` to answer it yourself and keep the agent from ever seeing it, or
  return nothing to fall through unchanged. This is the right hook for "a specific literal
  string means a specific deterministic action" — button taps (`callback-dispatch`) and a
  bare "✅" (`email-send-guard`) are the same shape at different granularity.

## Structure to copy

```
plugins/<name>/
  openclaw.plugin.json   # id, name, description, activation: {onStartup: true, onCapabilities: ["hook"]}
  index.mjs              # register(api) { api.on("before_tool_call" | "before_dispatch", handler, {priority, timeoutMs}) }
  test.mjs               # pure unit tests AND mandatory registered-hook runtime fixture replay
  README.md              # why it exists, what it does, what it deliberately doesn't touch
```

Extract the actual decision logic into small, named, **exported, pure** functions
(`inspectCalendarWrite`, `isBareCheckmark`, `formatSendReply`, `inspectGmailSend`) that take
plain data in and return a plain decision object out — no `api`, no network calls. The hook
handler itself should be a thin wrapper: call the pure function, act on its return value, log
it. Pure tests are necessary but insufficient: exercise the registered handler with
captured deployed argument shapes too. See `../test-fixtures/README.md` for provenance,
sanitization, known coverage limits, and the required `node test-fixtures/test-all.mjs` check.

The recurring failure mode is testing a convenient stand-in instead of the real
contract. On file moves/renames/splits, apply the same principle to consumers:
search the whole workspace, plugins, and scripts for old paths AND literal headings
or phrases; update every dependent reader or provide a tested compatibility path.
Test those dependents, not just edited files. Record relocations explicitly as
"moved from X to Y" in the changelog.

## Wiring it in

1. Write the plugin directory as above.
2. Add the id to `openclaw.json` → `plugins.allow` (array) and `plugins.entries.<id>`
   (`{"enabled": true}`).
3. `openclaw gateway restart` — plugins load once at gateway start; an edit to an *existing*
   plugin's `index.mjs` also needs this, same as `callback-dispatch`'s dispatch-table.json.
4. `openclaw plugins list` / `openclaw plugins doctor` to confirm it loaded and registered its
   handler with no errors.
5. Add a short backstop note at the RULES.md line(s) the plugin now enforces — "(Backstop: the
   `X` plugin does `Y` — but do not rely on it; do `Z` directly.)" — the plugin is a net, not a
   replacement for the underlying rule. See RULES.md's calendar-read section and the
   ✅-alone / gog-gmail-send lines for the exact phrasing convention.

## Deliberately out of scope for a guard

Don't try to make a guard cover every possible phrasing of a risky action — a `✅` alongside
other text, or a subtly different invented flag, should still fall through to the agent
following the underlying rule. A guard earns its keep on the narrow, high-frequency,
exact-match case; broadening it past that just adds false-positive risk for marginal coverage.
