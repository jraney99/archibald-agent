import assert from "node:assert/strict";
import { rewriteCalendarRead } from "./index.mjs";

const cfg = { defaultDays: 21, minDays: 10, minSearchDays: 30 };
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
const W = "python3 ~/.openclaw/workspace/Skills/shared/fetch_calendar.py";

// --- the four real 2026-09-01 incident commands ---

t("invented --start-date/--end-date -> wrapper, widened to minDays", () => {
  const out = rewriteCalendarRead(
    "gog calendar list --account owner@example.com --start-date 2026-09-01 --end-date 2026-09-03 -j",
    cfg,
  );
  assert.equal(out, `${W} --from 2026-09-01 --to 2026-09-11`);
});

t("raw gog list piped to head -> wrapper, default window", () => {
  const out = rewriteCalendarRead(
    "gog calendar list --account owner@example.com -j | head -40",
    cfg,
  );
  assert.equal(out, `${W} --days 21`);
});

t("fetch_calendar.py --json piped to jq (wrong schema) -> plain wrapper, --days floored to 10", () => {
  const out = rewriteCalendarRead(
    `python3 ~/.openclaw/workspace/Skills/shared/fetch_calendar.py --days 7 --json 2>/dev/null | jq '.events[]'`,
    cfg,
  );
  assert.equal(out, `${W} --days 10`);
});

t("raw gog list piped to jq+head -> wrapper", () => {
  const out = rewriteCalendarRead(
    `gog calendar list --account owner@example.com -j | jq -r '.events[]' | head -20`,
    cfg,
  );
  assert.equal(out, `${W} --days 21`);
});

// --- windows ---

t("explicit --days below floor is raised", () => {
  assert.equal(rewriteCalendarRead("gog cal ls --days 3", cfg), `${W} --days 10`);
});

t("explicit --days above floor kept", () => {
  assert.equal(rewriteCalendarRead("gog cal ls --days 45", cfg), `${W} --days 45`);
});

t("--from with wide --to kept as-is", () => {
  assert.equal(
    rewriteCalendarRead("gog cal events --from 2026-09-01 --to 2026-10-01", cfg),
    `${W} --from 2026-09-01 --to 2026-10-01`,
  );
});

t("--from only gets a minDays tail", () => {
  assert.equal(
    rewriteCalendarRead("gog cal events --from 2026-12-24", cfg),
    `${W} --from 2026-12-24 --to 2027-01-03`,
  );
});

// --- queries ---

t("--query with no dates gets minSearchDays window", () => {
  assert.equal(
    rewriteCalendarRead(`gog calendar search --query "dentist"`, cfg),
    `${W} --days 30 --query dentist`,
  );
});

t("--query with multiword quoted value preserved & shell-quoted", () => {
  assert.equal(
    rewriteCalendarRead(`gog calendar search --query "team lunch" --days 14`, cfg),
    `${W} --days 14 --query 'team lunch'`,
  );
});

// --- must NOT touch ---

for (const [name, cmd] of [
  ["create", `gog calendar create primary --summary "X" --from 2026-09-01T10:00:00-05:00 --to 2026-09-01T11:00:00-05:00`],
  ["update", `gog calendar update primary abc123 --summary "Y"`],
  ["delete", `gog calendar delete primary abc123 -y`],
  ["quickadd", `gog calendar quickadd primary "lunch tomorrow noon"`],
  ["add_calendar_event wrapper", `python3 ~/.openclaw/workspace/Skills/shared/add_calendar_event.py --who Bob`],
  ["deliberate single --calendar", `gog cal ls --calendars owner@work.example --from 2026-09-01 --to 2026-09-08 -j`],
  ["clean wrapper call, no pipe", `python3 ~/.openclaw/workspace/Skills/shared/fetch_calendar.py --days 7`],
  ["--help", `gog calendar list --help`],
  ["chained with real work", `gog cal ls -j > /tmp/c.json && python3 process.py /tmp/c.json`],
  ["unrelated command", `git status`],
  ["gmail read, not calendar", `gog gmail list --query "from:boss" -j`],
]) {
  t(`untouched: ${name}`, () => {
    assert.equal(rewriteCalendarRead(cmd, cfg), null);
  });
}

console.log(`\n${pass} passed`);
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";
await runRuntimeFixtures("calendar-read-guard");
