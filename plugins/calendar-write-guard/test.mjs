import assert from "node:assert/strict";
import { inspectCalendarWrite, inspectWrapperCreate, recordCalendarCreate, toolCallSucceeded } from "./index.mjs";

let pass = 0;
const t = (name, fn) => {
  try {
    fn();
    pass++;
    console.log(`  ok  ${name}`);
  } catch (e) {
    console.error(`FAIL  ${name}\n      ${e.message}`);
    process.exitCode = 1;
  }
};
const fresh = () => ({ creates: new Map(), deleted: new Set() });

// --- Check 1: invented flags -> block ---

for (const cmd of [
  `gog calendar create primary --title "X" --from 2026-09-09T10:30:00 --to 2026-09-09T11:45:00 --start-timezone America/Chicago --end-timezone America/Chicago`,
  `gog calendar create primary --summary "X" --start "2026-09-09T10:30:00" --end "2026-09-09T11:45:00" --start-timezone America/Chicago --end-timezone America/Chicago`,
  `gog calendar update primary abc --event-id abc --from 2026-09-09T10:30:00`,
  `gog calendar create primary --summary "X" --date 2026-09-09 --start-timezone America/Chicago --end-timezone America/Chicago`,
  `gog calendar create primary --summary X --format json --start-timezone America/Chicago --end-timezone America/Chicago`,
]) {
  t(`block invented flag: ${cmd.slice(20, 60)}...`, () => {
    const r = inspectCalendarWrite(cmd, fresh());
    assert.ok(r?.block, "expected block");
    assert.match(r.blockReason, /wrapper|add_calendar_event|update_calendar_event/);
  });
}

t("real --start-timezone is NOT flagged as invented --start", () => {
  const r = inspectCalendarWrite(
    `gog calendar create primary --summary "X" --from 2026-09-09T10:30:00 --to 2026-09-09T11:45:00 --start-timezone America/Chicago --end-timezone America/Chicago`,
    fresh(),
  );
  assert.equal(r, null);
});

// --- Check 2: create missing tz flags -> block ---

t("create missing both tz flags -> block", () => {
  const r = inspectCalendarWrite(
    `gog calendar create primary --summary "X" --from 2026-09-09T10:30:00 --to 2026-09-09T11:45:00`,
    fresh(),
  );
  assert.ok(r?.block);
  assert.match(r.blockReason, /start-timezone and --end-timezone/);
});

t("create missing only --end-timezone -> block", () => {
  const r = inspectCalendarWrite(
    `gog calendar create primary --summary "X" --from a --to b --start-timezone America/Chicago`,
    fresh(),
  );
  assert.ok(r?.block);
});

t("--all-day create without tz -> allowed", () => {
  const r = inspectCalendarWrite(
    `gog calendar create primary --summary "X" --all-day --from 2026-09-09 --to 2026-09-10`,
    fresh(),
  );
  assert.equal(r, null);
});

t("well-formed create -> allowed (first time)", () => {
  const r = inspectCalendarWrite(
    `gog calendar create primary --summary "House Cleaning <> the user @ Home" --from 2026-09-09T10:30:00 --to 2026-09-09T11:45:00 --start-timezone America/Chicago --end-timezone America/Chicago --location Home --reminder popup:45m`,
    fresh(),
  );
  assert.equal(r, null);
});

// --- Check 3: recreate loop -> requireApproval ---

t("2nd create of same summary after a SUCCESSFUL 1st -> requireApproval", () => {
  const s = fresh();
  const c = `gog calendar create primary --summary "House Cleaning <> the user @ Home" --from a --to b --start-timezone America/Chicago --end-timezone America/Chicago`;
  assert.equal(inspectCalendarWrite(c, s), null); // 1st
  recordCalendarCreate(c, s); // it ran and exited 0
  const r = inspectCalendarWrite(c, s); // 2nd
  assert.ok(r?.requireApproval, "expected requireApproval");
  assert.match(r.requireApproval.description, /time-display quirk/);
});

t("2nd create after a FAILED 1st -> allowed (2026-09-21: missing calendarId counted as an event)", () => {
  const s = fresh();
  const summary = `--summary "XX0123 ATL→DAL (Airline)" --from 2026-09-22T12:59:00 --to 2026-09-22T14:05:00 --start-timezone America/Chicago --end-timezone America/Chicago`;
  assert.equal(inspectCalendarWrite(`gog calendar create ${summary}`, s), null); // gog: expected "<calendarId>"
  // no recordCalendarCreate: the call failed
  assert.equal(inspectCalendarWrite(`gog calendar create owner@example.com ${summary}`, s), null);
});

t("approval request fits the gateway schema (title <= 80, description <= 256)", () => {
  const s = fresh();
  const long = "X".repeat(300);
  const c = `gog calendar create primary --summary "${long}" --from a --to b --start-timezone America/Chicago --end-timezone America/Chicago`;
  recordCalendarCreate(c, s);
  const r = inspectCalendarWrite(c, s);
  assert.ok(r.requireApproval.title.length <= 80);
  assert.ok(r.requireApproval.description.length <= 256, `description is ${r.requireApproval.description.length}`);
  const del = fresh();
  inspectCalendarWrite(`gog calendar delete primary "${long} y"`, del);
  const r2 = inspectCalendarWrite(`gog calendar create primary --summary "${long} y" --from a --to b --start-timezone America/Chicago --end-timezone America/Chicago`, del);
  assert.ok(r2.requireApproval.description.length <= 256, `description is ${r2.requireApproval.description.length}`);
});

t("toolCallSucceeded reads the runtime's result shapes", () => {
  assert.equal(toolCallSucceeded({ result: { details: { status: "completed", exitCode: 0 } } }), true);
  assert.equal(toolCallSucceeded({ error: "expected \"<calendarId>\"", result: { details: { exitCode: 2 } } }), false);
  assert.equal(toolCallSucceeded({ result: { details: { status: "completed", exitCode: 1 } } }), false);
  assert.equal(toolCallSucceeded({ result: { details: { status: "blocked" } } }), false);
  assert.equal(toolCallSucceeded({ result: { isError: true } }), false);
  assert.equal(toolCallSucceeded({ result: { content: [] } }), true); // unknown shape: keep guarding
});

t("delete then recreate same title -> requireApproval on the recreate", () => {
  const s = fresh();
  inspectCalendarWrite(
    `gog calendar delete primary "House Cleaning <> the user @ Home" --calendar Personal`,
    s,
  );
  const r = inspectCalendarWrite(
    `gog calendar create primary --summary "House Cleaning <> the user @ Home" --from a --to b --start-timezone America/Chicago --end-timezone America/Chicago`,
    s,
  );
  assert.ok(r?.requireApproval);
});

t("two DIFFERENT events created in one session -> both allowed", () => {
  const s = fresh();
  const mk = (name) =>
    `gog calendar create primary --summary "${name}" --from a --to b --start-timezone America/Chicago --end-timezone America/Chicago`;
  assert.equal(inspectCalendarWrite(mk("Dinner <> the user @ Example Bistro"), s), null);
  assert.equal(inspectCalendarWrite(mk("Golf <> the user @ Example Golf Club"), s), null);
});

// --- add wrapper from chat must be proposed (2026-09-21 airport pickup: written directly, no draft) ---

for (const cmd of [
  `python3 ~/.openclaw/workspace/Skills/shared/add_calendar_event.py --who "The user" --venue "Flight" --from "2026-09-22T12:59" --to "2026-09-22T14:05" --tz "America/Chicago" --location "ATL → DAL"`,
  `cd ~/.openclaw/workspace && python3 -W ignore Skills/shared/add_calendar_event.py --who X --venue Y --from a --to b --tz America/Chicago --location Z`,
  `python ~/.openclaw/workspace/Skills/shared/add_calendar_event.py --who X --venue Y --from a --to b --tz America/Chicago --location Z`,
]) {
  t(`block unproposed wrapper create: ${cmd.slice(0, 50)}...`, () => {
    const r = inspectWrapperCreate(cmd);
    assert.ok(r?.block);
    assert.match(r.blockReason, /--propose/);
  });
}

for (const [name, cmd] of [
  ["proposed", `python3 Skills/shared/add_calendar_event.py --who X --venue Y --from a --to b --tz America/Chicago --location Z --propose`],
  ["help", `python3 Skills/shared/add_calendar_event.py --help`],
  ["its test file", `python3 Skills/shared/test_add_calendar_event.py`],
  ["reading it", `cat Skills/shared/add_calendar_event.py`],
  ["grepping it", `grep -n propose Skills/shared/add_calendar_event.py`],
  ["hold wrapper", `python3 Skills/shared/add_calendar_hold.py --label "Sam's wedding" --date "June 26 2027"`],
  ["golf confirm (calls wrapper internally)", `python3 Skills/playbypoint-golf/scripts/confirm_booking.py`],
]) {
  t(`wrapper check leaves alone: ${name}`, () => {
    assert.equal(inspectWrapperCreate(cmd), null);
  });
}

// --- must NOT touch ---

for (const [name, cmd] of [
  ["wrapper create", `python3 ~/.openclaw/workspace/Skills/shared/add_calendar_event.py --who Bob --venue X --from a --to b --tz America/Chicago --location Y`],
  ["wrapper update", `python3 ~/.openclaw/workspace/Skills/shared/update_calendar_event.py --event-id abc --from a --to b --tz America/Chicago`],
  ["plain delete by id", `gog calendar delete primary abc123 -y`],
  ["update summary only", `gog calendar update primary abc123 --summary "Renamed <> the user @ Venue"`],
  ["update reschedule with real flags", `gog calendar update primary abc123 --from 2026-09-09T10:30:00 --to 2026-09-09T11:45:00 --start-timezone America/Chicago --end-timezone America/Chicago`],
  ["--help", `gog calendar create --help`],
  ["a read", `gog calendar events primary --from 2026-09-01 --to 2026-09-08 -j`],
  ["unrelated", `git status`],
]) {
  t(`untouched: ${name}`, () => {
    assert.equal(inspectCalendarWrite(cmd, fresh()), null);
  });
}

console.log(`\n${pass} passed`);
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";
await runRuntimeFixtures("calendar-write-guard");
