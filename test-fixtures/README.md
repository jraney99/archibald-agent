# Deployed-contract regression fixtures

Recurring failure mode: testing a convenient model of a contract instead of the
deployed boundary. Pure inspector tests are useful, but cannot establish that a
registered hook receives the expected fields or that relocated rules still reach
their consumers.

## Required check

Run `node test-fixtures/test-all.mjs` from the repository root before deploying any
guard/hook change. Every existing plugin test entrypoint also invokes its runtime
fixtures. The aggregate check rejects plugin directories without fixture coverage,
suites without the required invocation, and unexercised registered hooks.

The harness calls the actual registered handlers; it never executes the tool calls.
Child-process, fetch/HTTP, and current async file-mutation APIs are trapped during
replay. These tripwires are not a general security sandbox. Use only safe rejection
and passthrough cases; do not add real approval IDs or success-path side effects.

## Provenance and limits

`deployed-tool-calls.json` contains four sanitized native `toolCall` blocks from
`agents/main/sessions/*.jsonl`, including one checkpoint file. Companion
`*.trajectory.jsonl` files contain summaries, not the tool-call arguments needed
here. Each fixture records its source basename, line, timestamp, original block
hash, and observed recursive shape. The edit fixture preserves both array entries.

Private IDs, paths, commands, and document text were replaced without renaming
keys, flattening arrays, or changing types. Adversarial scenarios in
`test-fixtures/runtime-fixtures.mjs` change only values on cloned captured shapes;
they are **crafted derivatives**, not claims that those exact actions occurred.

The installed runtime passes `{toolName, params, toolCallId}` and a **separate
context argument** to `before_tool_call`. Replay follows that boundary without
normalizing arguments. `before_dispatch` owner/unbound-approval scenarios are
contract-derived, not captured raw hook envelopes. Successful approval execution,
live delivery, optional tool parameters, and every runtime context field are not
covered by these four captures. Existing approval/pure-unit tests remain necessary.

`runtime-contract.json` pins the reviewed runtime package and boundary file hashes.
An upgrade fails the required check until its boundary is reviewed. For another
installation set `OPENCLAW_INSTALL_DIR`; missing runtime files are a failure, not
a silent skip. Do not refresh hashes merely to turn a failing check green.

## Refresh procedure

1. Select a native session line containing exactly one call of the desired tool.
2. Run `python3 test-fixtures/capture_tool_fixture.py SOURCE --line N --tool edit`.
   Output is sanitized JSON on stdout; source logs are never copied into fixtures.
3. Review the output before adding it. Unknown fields fail closed pending explicit
   sanitizer review. Preserve new observed variants rather than translating them
   into an existing convenient format; extend coverage and the fixture inventory.
4. Review installed hook call sites after runtime upgrades, then update the pin.
5. Run the full required check. Its mutation test verifies that the old memory
   inspector-only wiring fails the captured-shape replay.

## 2026-09-14 outcome

The session-isolation fixture failed before the calendar-write guard fix: separate
runtime sessions shared the fallback counter. The guard now reads the separate
context's session key. Approval binding, cron exceptions, and other guard logic
were not changed. No pre-existing files were relocated in this change. The new
shared test helpers live outside `plugins/` to avoid runtime plugin discovery.
