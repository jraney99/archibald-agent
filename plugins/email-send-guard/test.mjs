import assert from "node:assert/strict";
import { isBareCheckmark, formatSendReply, inspectGmailSend, dispatchCheckmark, pendingProposals } from "./index.mjs";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";

let pass = 0;
const t = (name, fn) => {
  const ok = () => { pass++; console.log(`  ok  ${name}`); };
  const bad = (e) => {
    console.error(`FAIL  ${name}\n      ${e.message}`);
    process.exitCode = 1;
  };
  try {
    const result = fn();
    // An async case must be awaited by its caller, or a rejection lands as an
    // uncaught exception long after this line has already printed "ok".
    if (result && typeof result.then === "function") return result.then(ok, bad);
    ok();
  } catch (e) {
    bad(e);
  }
  return Promise.resolve();
};

// --- isBareCheckmark ---------------------------------------------------

t("bare ✅ matches", () => assert.equal(isBareCheckmark("✅"), true));
t("bare ✅ with whitespace matches", () => assert.equal(isBareCheckmark("  ✅  "), true));
t("✅ with variation selector matches", () => assert.equal(isBareCheckmark("✅️"), true));
t("✅ plus other text does NOT match (ambiguous, stays with the agent)", () =>
  assert.equal(isBareCheckmark("✅ looks good"), false));
t("'OK' does not match -- RULES.md: only the literal symbol counts", () =>
  assert.equal(isBareCheckmark("OK"), false));
t("empty string does not match", () => assert.equal(isBareCheckmark(""), false));
t("callback_data token does not match (leave it to callback-dispatch)", () =>
  assert.equal(isBareCheckmark("confirm:email"), false));

// --- formatSendReply -----------------------------------------------------

t("SENT: -> '✅ Sent to {email}.'", () => {
  const r = formatSendReply("SENT: 18f2a9c1 → dr.physician@example.com");
  assert.equal(r, "✅ Sent to dr.physician@example.com. 🎩");
});
t("NO_PENDING -> the exact RULES.md line, no hat appended", () => {
  assert.equal(formatSendReply("NO_PENDING: no pending drafts"), "No pending draft to send.");
});
t("unexpected output -> an honest failure, never claims sent", () => {
  const r = formatSendReply("Traceback ... some Gmail API error");
  assert.match(r, /Couldn't send/);
  assert.doesNotMatch(r, /Sent to/);
});
t("empty output -> honest failure, not a silent success", () => {
  const r = formatSendReply("");
  assert.match(r, /Couldn't send/);
});

// --- inspectGmailSend ------------------------------------------------------

for (const verb of ["send", "reply", "autoreply"]) {
  t(`blocks ad-hoc gog gmail ${verb}`, () => {
    const r = inspectGmailSend(`gog gmail ${verb} --to x@y.com --body "hi"`, {});
    assert.ok(r?.block);
    assert.equal(r.verb, verb);
    assert.match(r.blockReason, /BANNED/);
    assert.match(r.blockReason, /send_pending/);
  });
}

t("does not block --help", () => {
  assert.equal(inspectGmailSend("gog gmail send --help", {}), null);
});

t("does not block a cron/job-driven run (ctx.jobId set)", () => {
  assert.equal(inspectGmailSend("gog gmail send --to x@y.com", { jobId: "abc-123" }), null);
});

t("does not touch unrelated gog gmail subcommands (messages, search, etc.)", () => {
  assert.equal(inspectGmailSend("gog gmail messages search --account x newer_than:1d", {}), null);
});

t("does not touch send_pending.py itself", () => {
  assert.equal(inspectGmailSend("python3 send_pending.py --latest", {}), null);
});

t("empty/non-string command is a no-op, not a crash", () => {
  assert.equal(inspectGmailSend("", {}), null);
  assert.equal(inspectGmailSend(undefined, {}), null);
});

const config = { channels: { telegram: { allowFrom: ["123"] } } };
const owner = { content: "✅", senderId: "123", channel: "telegram", isGroup: false };
let calls = [];
const run = async (_cmd, args) => {
  calls.push(args);
  return args.includes("--message-id")
    ? { stdout: JSON.stringify({ id: "a".repeat(32), kind: "cal" }) }
    : { stdout: "NO_REPLY" };
};
await dispatchCheckmark(owner, { accountId: "default" }, config, run);
t("unbound checkmark never executes a pending email", () => assert.equal(calls.length, 0));
await dispatchCheckmark({ ...owner, replyToId: "42" }, { accountId: "demo" }, config, run);
t("demo account cannot approve main-account actions", () => assert.equal(calls.length, 0));
const result = await dispatchCheckmark({ ...owner, replyToId: "42" }, { accountId: "default" }, config, run);
t("calendar reply resolves only to the calendar handler", () => {
  assert.match(calls[1][2], /confirm_cal_event\.py$/);
  assert.equal(calls[1].includes("--latest"), false);
  assert.deepEqual(result, { handled: true });
});
// A bare ✅ says WHICH case it is. 2026-09-20: Archibald typed its own "✅ to confirm"
// under a plain chat message, so no proposal existed — and this told the user to tap a button
// that was nowhere on screen.
const OWNER = [{ channelId: "telegram", accountId: "default", senderId: "10001" },
               { channels: { telegram: { allowFrom: ["tg:10001"] } } }];
const bareTick = { content: "✅", channel: "telegram", senderId: "10001", isGroup: false };
const emptyRoot = mkdtempSync(`${tmpdir()}/esg-none-`);
await t("bare ✅ with nothing pending says nothing is waiting", async () => {
  const r = await dispatchCheckmark(bareTick, ...[OWNER[0]], OWNER[1], undefined, emptyRoot);
  assert.match(r.text, /Nothing is waiting for approval/);
  assert.match(r.text, /never registered it as a proposal/);
  assert.doesNotMatch(r.text, /tap its approval button/);
});
rmSync(emptyRoot, { recursive: true, force: true });
const waitingRoot = mkdtempSync(`${tmpdir()}/esg-waiting-`);
writeFileSync(`${waitingRoot}/pending_cal_event.json`, JSON.stringify({ proposal_text: "x", gog_events: [] }));
await t("bare ✅ with a proposal waiting points at that proposal, and still does not act", async () => {
  const r = await dispatchCheckmark(bareTick, ...[OWNER[0]], OWNER[1], undefined, waitingRoot);
  assert.match(r.text, /specific proposal/);
  assert.match(r.text, /tap its approval button/);
  assert.doesNotMatch(r.text, /Nothing is waiting for approval/);
});
rmSync(waitingRoot, { recursive: true, force: true });
t("pendingProposals sees a waiting calendar proposal, and ignores an empty file", () => {
  const dir = mkdtempSync(`${tmpdir()}/esg-`);
  assert.deepEqual(pendingProposals(dir), []);
  writeFileSync(`${dir}/pending_cal_event.json`, "");
  assert.deepEqual(pendingProposals(dir), []);
  writeFileSync(`${dir}/pending_cal_event.json`, JSON.stringify({ proposal_text: "x", gog_events: [] }));
  assert.deepEqual(pendingProposals(dir), ["pending_cal_event.json"]);
  rmSync(dir, { recursive: true, force: true });
});

console.log(`\n${pass} passed, ${process.exitCode ? "some failed" : "0 failed"}`);
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";
await runRuntimeFixtures("email-send-guard");
