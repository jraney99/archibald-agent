/**
 * A silence sentinel carrying anything else gets delivered instead of suppressed.
 *
 * 2026-10-09: The user received the email daemon's internal [Cal] log lines ending
 * "NO_REPLY 🎩". The gateway matches NO_REPLY exactly; the hat broke the match and the
 * whole thing went to his phone. Two separate rule files already forbade the hat.
 *
 * The hard part is not catching it. It is NOT catching a reply that merely talks about
 * the sentinel — which this very session did, at length, while diagnosing it.
 */
import assert from "node:assert/strict";
import { intendedSentinel, trailingSentinel, inspectFinalReply } from "./index.mjs";
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";

const verdict = (text) => inspectFinalReply({ lastAssistantMessage: text });

// ── the exact failure ─────────────────────────────────────────────────────────────────
assert.equal(intendedSentinel("NO_REPLY 🎩"), "NO_REPLY");
assert.equal(intendedSentinel("HEARTBEAT_OK 🎩"), "HEARTBEAT_OK");
assert.ok(verdict("NO_REPLY 🎩"), "the hat case must be caught");
assert.match(verdict("NO_REPLY 🎩").retry.instruction, /exactly NO_REPLY/);

// The real message he got: pages of log, sentinel at the end.
const calDump = `[18:00:33] [Cal] leave-for-airport block created: 8:31 AM (20 min drive)
[18:00:34] [Cal] check-in job scheduled: XX 1234 at Nov 18 9:45 AM
NO_REPLY 🎩`;
assert.equal(trailingSentinel(calDump), "NO_REPLY");
assert.ok(verdict(calDump), "a sentinel trailing real output must be caught");

// ── already correct: leave it completely alone ────────────────────────────────────────
for (const good of ["NO_REPLY", "HEARTBEAT_OK"]) {
  assert.equal(intendedSentinel(good), null, `${good} is already right`);
  assert.equal(verdict(good), undefined, `${good} must pass untouched`);
}

// ── prose ABOUT the sentinel is not an attempt at one ─────────────────────────────────
// This session wrote all of these while diagnosing the bug. A guard that revises them
// would make it impossible to discuss its own subject.
for (const prose of [
  "NO_REPLY is a silence sentinel — the agent outputs it alone to say stay quiet.",
  "Your agent output `NO_REPLY 🎩`. The hat broke the silence check.",
  "never add 🎩 to silence sentinels like NO_REPLY or HEARTBEAT_OK",
  "I checked whether NO_REPLY was being matched exactly, and it is. 🎩",
]) {
  assert.equal(verdict(prose), undefined, `must not fire on prose: ${prose.slice(0, 40)}`);
}

// ── ordinary replies ──────────────────────────────────────────────────────────────────
for (const normal of ["Lunch is at 2:00 PM. 🎩", "", "   ", "Done. 🎩"]) {
  assert.equal(verdict(normal), undefined, `must not fire on: ${JSON.stringify(normal)}`);
}

// ── other decoration the model might add ──────────────────────────────────────────────
for (const decorated of ['"NO_REPLY"', "`NO_REPLY`", "NO_REPLY.", "**NO_REPLY**"]) {
  assert.equal(intendedSentinel(decorated), "NO_REPLY", `decorated: ${decorated}`);
}
// Plain whitespace padding is NOT flagged: it trims to the bare sentinel, and there is
// no evidence the gateway rejects that. Flagging it would mean a revision pass for
// something that probably already works — a guard should only fire on a failure it can
// actually demonstrate.
assert.equal(intendedSentinel("  NO_REPLY  "), null);

// ── shape the runtime consumes ────────────────────────────────────────────────────────
const d = verdict("NO_REPLY 🎩");
assert.equal(d.action, "revise");
assert.equal(d.retry.idempotencyKey, "silence-sentinel");
assert.ok(d.retry.maxAttempts >= 1);
assert.match(d.reason, /will deliver instead/);

await runRuntimeFixtures("silence-sentinel-guard");
console.log("silence-sentinel-guard: all pure checks passed");
