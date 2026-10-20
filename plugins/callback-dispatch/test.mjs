import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";

await runRuntimeFixtures("callback-dispatch");

// Unit suites. test-all.mjs only discovers test*.mjs, so *.test.mjs files that are
// not listed here are never run by anything.
await import("./callback-token.test.mjs");
await import("./reply-nudge.test.mjs");
await import("./status-question.test.mjs");
await import("./pending-context.test.mjs");
await import('./finance-intake.test.mjs');
await import('./crypto-sweep.test.mjs');

// What senders SEND vs what this plugin ACCEPTS (see button_contract_test.py).
const contract = spawnSync("python3", [fileURLToPath(new URL("./button_contract_test.py", import.meta.url))], { stdio: "inherit", timeout: 120000 });
assert.equal(contract.status, 0, "button contract failed: a script sends a button that never reaches a handler");
