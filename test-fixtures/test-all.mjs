import { readdirSync, existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { coveredPlugins, verifyRuntimeContract, runtimePinSkipped } from "./runtime-fixtures.mjs";

// Run every suite even when an earlier one fails. Before 2026-10-03 one
// environment-dependent calendar assertion stopped the harness before the
// remaining hook and drift checks, making the daily result falsely incomplete.
const failures = [];
let reached = 0;
let passed = 0;
let expectedFailures = 0;
const safeEnv = {
  ...process.env,
  TELEGRAM_SEND_DISABLED: "1",
  PYTHONPYCACHEPREFIX: process.env.PYTHONPYCACHEPREFIX || "/tmp/openclaw-test-pyc",
};

function result(label, ok, detail = "") {
  reached += 1;
  if (ok) {
    passed += 1;
    console.log(`HARNESS PASS: ${label}`);
  } else {
    failures.push({ label, detail });
    console.error(`HARNESS FAIL: ${label}${detail ? ` — ${detail}` : ""}`);
  }
  return ok;
}

function check(label, fn) {
  try {
    fn();
    return result(label, true);
  } catch (error) {
    return result(label, false, error?.stack || String(error));
  }
}

function run(label, command, args, timeout = 60000) {
  const child = spawnSync(command, args, { stdio: "inherit", timeout, env: safeEnv });
  const detail = child.error?.message || child.signal || `exit ${child.status}`;
  result(label, child.status === 0, child.status === 0 ? "" : detail);
  return child;
}

// The runtime pin needs the reviewed OpenClaw install. Without it, set
// OPENCLAW_SKIP_RUNTIME_PIN=1 and the result is reported as SKIPPED, never PASS.
let pinSkipped = 0;
if (runtimePinSkipped) {
  pinSkipped = 1;
  console.log("HARNESS SKIPPED: mandatory runtime fixtures — OPENCLAW_SKIP_RUNTIME_PIN=1, installed runtime not verified");
} else {
  check("mandatory runtime fixtures", verifyRuntimeContract);
}
run("fixture harness regression", process.execPath,
    [fileURLToPath(new URL("./test-runtime-fixtures.mjs", import.meta.url))]);
run("fixture sanitizer", "python3",
    [fileURLToPath(new URL("./test_capture.py", import.meta.url))]);
run("daily self-check alert logic", "python3",
    [fileURLToPath(new URL("./test_nightly_guard_check.py", import.meta.url))]);
// A blocked action must hand back something runnable: on 2026-10-13 the email
// guard described a hand-assembly procedure and one short note cost $1.01 in
// exploratory turns. See test_guards_name_a_command.py.
run("guards name a command", "python3",
    [fileURLToPath(new URL("./test_guards_name_a_command.py", import.meta.url))]);
// Not run here (need the live deployment, not shipped in this repo): the job-drift and
// retimed-job guard checks read the live cron store (state/openclaw.sqlite) and skills
// that are not published; the data-layout check reads a baseline of the full workspace.

for (const [file, label] of [
  ["../skills/shared/test_agent_usage.py", "usage accounting: transcripts only"],
  ["../skills/shared/test_usage_audit_v2.py", "usage ledger: dedupe, coverage gaps, daily rollup"],
  ["../skills/shared/test_compact_session.py", "session compaction: no-op is success, size trigger"],
  ["../skills/shared/test_check_bootstrap_budget.py", "bootstrap budget alert says what is happening"],
  // The living-room Zengge light is Bluetooth-only and its firmware ignores the classic
  // colour command (2026-10-12): the bytes, the read-back parsing of its own captured
  // frames, and confirm-or-say-so are checked without opening a radio.
  ["../skills/zengge-lights/scripts/test_zengge_ble.py", "zengge light: wire format + confirmed apply"],
  // OpenTable's checkout varies per restaurant and the flow was mapped against only
  // one (2026-10-06: a required terms checkbox carried no ref of its own, so the
  // Complete click was refused). These parsers were never in the daily suite until then.
  ["../skills/restaurant-booking/scripts/test_parsing.py",
   "OpenTable: agreement checkbox, fee terms, refusal vs unknown"],
  // Golf booking (2026-10-11): the approved list is the gate, GolfNow's tee sheet and the
  // pre-login page parse from real captures, and confirm refuses on any change of slot,
  // price, total or card. Dry-run stays the default until the checkout is mapped.
  ["../skills/golf-booking/scripts/test_golf_booking.py",
   "golf booking: approved list, GolfNow parsing, proposal, confirm guards"],
  ["../skills/ai-wardrobe-steward/tests/test_calendar_outfit_watch.py",
   "outfit watcher: header sent once, failed board retryable"],
  ["../skills/ai-wardrobe-steward/tests/test_shopping_store.py",
   "wardrobe shopping: atomic JSON store, loud corruption, scripted repair"],
  ["../skills/ai-wardrobe-steward/tests/test_store_catalog.py",
   "wardrobe shopping: Shopify catalog search/changes, source-health aliases"],
  ["../skills/ai-wardrobe-steward/tests/test_pinterest_board_watch.py",
   "wardrobe inspiration: Pinterest board feed import, dedupe, dry-run"],
  ["../skills/ai-wardrobe-steward/tests/test_pinterest_digest.py",
   "wardrobe inspiration: pin descriptions, Sunday digest template, Add/Skip callback"],
  ["../skills/ai-wardrobe-steward/tests/test_wardrobe_loop.py",
   "wardrobe shopping: purchase loop, fit prompt, closet looks, plan progress, cost per wear"],
  ["../skills/ai-wardrobe-steward/tests/test_log_wear.py",
   "wardrobe: typed wear logging matches colour/brand/category or refuses"],
  ["../skills/ai-wardrobe-steward/tests/test_triage_golf.py",
   "wardrobe triage: indoor golf and tours never read as a real round"],
  ["../skills/ai-wardrobe-steward/tests/test_belt_pick.py",
   "wardrobe: the board picks a belt that matches the shoes, or none"],
  ["../skills/ai-wardrobe-steward/tests/test_board_presentation.py",
   "wardrobe board: photos-only image, caption names the pieces, four buttons"],
  ["../skills/ai-wardrobe-steward/tests/test_catalog_intake.py",
   "wardrobe intake: the store catalog supplies the photo, price and product link"],
  ["../skills/ai-wardrobe-steward/tests/test_find_feedback.py",
   "wardrobe shopping: 👍/👎 on a scan find records taste, not a purchase"],
  ["../skills/ai-wardrobe-steward/tests/test_find_preferences.py",
   "wardrobe shopping: taps aggregate into guidance the next scan reads"],
  ["../skills/ai-wardrobe-steward/tests/test_pinned_looks.py",
   "wardrobe outfits: a pinned look tilts the scorer, capped and best-look-only"],
  ["../skills/ai-wardrobe-steward/tests/test_plan_wants.py",
   "wardrobe shopping: plan gaps with no want, reported not auto-added"],
  ["../skills/ai-wardrobe-steward/tests/test_retirement_lessons.py",
   "wardrobe: retiring records why, and repeats become a purchase lesson"],
  ["../skills/ai-wardrobe-steward/tests/test_color_from_image.py",
   "wardrobe shopping: garment colour measured from the product photo, cream on white included"],
  // 2026-10-12: a button prompt nothing tracks sits in the chat, and after 48h
  // Telegram refuses the bot's own delete — 13 October messages were already stuck.
  ["./test_prompt_tracking_contract.py",
   "every button prompt has an answered signal"],
]) {
  run(`contract: ${label}`, "python3",
      ["-W", "ignore", fileURLToPath(new URL(file, import.meta.url))]);
}

// The alarm decides when the user gets up, and nothing ran these until 2026-10-13:
// the calendar guard on the manual alarm path (test_alarm_calendar_guard.py) and
// the Hatch rate-limit cache (test_alarm_cache.py) both live here.
run("hatch-restore unittest discovery", "python3",
    ["-m", "unittest", "discover", "-s",
     fileURLToPath(new URL("../skills/hatch-restore/scripts/", import.meta.url)),
     "-p", "test_*.py"], 300000);
run("email-monitor unittest discovery", "python3",
    ["-m", "unittest", "discover", "-s",
     fileURLToPath(new URL("../skills/email-monitor/scripts/", import.meta.url)),
     "-p", "test_*.py"], 300000);

for (const entry of readdirSync(new URL("../plugins/", import.meta.url), { withFileTypes: true })) {
  if (!entry.isDirectory()) continue;
  const directory = new URL(`../plugins/${entry.name}/`, import.meta.url);
  if (!existsSync(new URL("openclaw.plugin.json", directory))) continue;
  if (!check(`plugin fixture coverage: ${entry.name}`, () => {
    if (!coveredPlugins.includes(entry.name)) throw new Error(`Add registered-hook fixtures for ${entry.name}`);
  })) continue;
  const tests = readdirSync(directory).filter((name) => /^test(?:-.*)?\.mjs$/.test(name));
  if (!check(`plugin suite presence: ${entry.name}`, () => {
    if (!tests.length) throw new Error(`Missing test suite: ${entry.name}`);
  })) continue;
  for (const name of tests) {
    const file = new URL(name, directory);
    if (!check(`plugin runtime fixture declaration: ${entry.name}/${name}`, () => {
      if (!readFileSync(file, "utf8").includes(`await runRuntimeFixtures("${entry.name}")`)) {
        throw new Error("required fixture check missing");
      }
    })) continue;
    run(`plugin suite: ${entry.name}/${name}`, process.execPath, [fileURLToPath(file)]);
  }
}

const drift = spawnSync("python3", [
  fileURLToPath(new URL("../skills/shared/gateway_drift.py", import.meta.url)),
  "--require-current",
], { stdio: "inherit", timeout: 60000, env: safeEnv });
reached += 1;
if (drift.status === 0) {
  passed += 1;
  console.log("HARNESS PASS: gateway/plugin runtime drift");
} else if (drift.status === 1 && !drift.error && !drift.signal) {
  expectedFailures += 1;
  console.log("HARNESS EXPECTED_FAIL: gateway/plugin runtime drift — restart requires owner approval");
} else {
  failures.push({ label: "gateway/plugin runtime drift", detail: drift.error?.message || drift.signal || `exit ${drift.status}` });
  console.error("HARNESS FAIL: gateway/plugin runtime drift could not be evaluated");
}

console.log(`HARNESS SUMMARY: reached=${reached} passed=${passed} failed=${failures.length} expected_fail=${expectedFailures} skipped=${pinSkipped}`);
for (const failure of failures) console.error(` - ${failure.label}: ${failure.detail}`);
if (failures.length) process.exitCode = 1;
