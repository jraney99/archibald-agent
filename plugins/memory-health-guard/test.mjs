import assert from "node:assert/strict";
import plugin, { inspectMemoryHealthWrite } from "./index.mjs";

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

const HEALTH_SECTION_CONTEXT = [
  "## Health",
  "- Health domain lives at `Health/` (routing row in `ROUTING.md`).",
  "- **HARD RULE: no medical interpretation.** Transcribe reports...",
  "- The weekly physician journal is built from the log files, not from this section.",
].join("\n");

// --- The two real incidents -> block ---

t("blocks the real dinner incident (2026-09-13, first)", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    oldString: HEALTH_SECTION_CONTEXT,
    newString:
      HEALTH_SECTION_CONTEXT +
      "\n- **2026-09-13 intake log:** Out late, woke up late. Dinner ~11:30 PM: leftovers + a supplement. Now having breakfast. *(For doctor)*",
  });
  assert.ok(r?.block, "expected block");
  assert.match(r.blockReason, /log_food\.py|confirm_meal\.py/);
});

t("blocks the real breakfast/snack/activity incident (2026-09-13, second)", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    oldString: HEALTH_SECTION_CONTEXT + "\n- **2026-09-13 intake log:** ...dinner...",
    newString:
      HEALTH_SECTION_CONTEXT +
      "\n- **2026-09-13 intake & activity log:** Breakfast ~11:45 AM: food + coffee. Snack: fruit. Activity: ~20 min light swimming. *(For doctor)*",
  });
  assert.ok(r?.block, "expected block");
});

t("blocks even without a bolded date, on loose vocabulary + verb", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    oldString: HEALTH_SECTION_CONTEXT,
    newString: HEALTH_SECTION_CONTEXT + "\n- the user had dinner: leftover pasta, took vitamins.",
  });
  assert.ok(r?.block, "expected block");
});

t("blocks a hydration report (not yet a real incident, but same shape)", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    oldString: HEALTH_SECTION_CONTEXT,
    newString: HEALTH_SECTION_CONTEXT + "\n- **2026-09-14 hydration log:** Drank 32oz of water this morning.",
  });
  assert.ok(r?.block, "expected block");
});

t("blocks a medication report (not yet a real incident, but same shape)", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    oldString: HEALTH_SECTION_CONTEXT,
    newString: HEALTH_SECTION_CONTEXT + "\n- Took my morning meds around 8am, per the user's text.",
  });
  assert.ok(r?.block, "expected block");
});

t("blocks a whole-file write (write tool), not just edit", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    content: HEALTH_SECTION_CONTEXT + "\n- **2026-09-14 intake log:** Breakfast: eggs. Took my supplement.",
  });
  assert.ok(r?.block, "expected block");
});

t("blocks with a relative path ending in MEMORY.md", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "workspace/MEMORY.md",
    oldString: HEALTH_SECTION_CONTEXT,
    newString: HEALTH_SECTION_CONTEXT + "\n- **2026-09-15 intake log:** Had lunch, logged supplement.",
  });
  assert.ok(r?.block, "expected block");
});

// --- Not near the Health section -> pass through ---

t("does not block an unrelated MEMORY.md edit (contacts section)", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    oldString: "## Contacts\n- **Alex Example:** old@example.com",
    newString: "## Contacts\n- **Alex Example:** alex@example.org",
  });
  assert.equal(r, null);
});

t("does not block a legitimate Health-section structural edit (new panel pointer)", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    oldString: "- Latest panel: 2026-08-19 (lab order...).",
    newString: "- Latest panel: 2026-11-06 (lab order...).",
  });
  assert.equal(r, null);
});

t("does not block updating the guardrail bullet itself to fix a typo", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    oldString: "- **NEVER log a meal/supplement/movement/intake report here.** (Twice on 2026-09-13...)",
    newString: "- **NEVER log a meal/supplement/movement/intake report here.** (Three times now...)",
  });
  assert.equal(r, null);
});

t("does not block a static hydration-goal fact with no report verb", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/MEMORY.md",
    oldString: HEALTH_SECTION_CONTEXT,
    newString: HEALTH_SECTION_CONTEXT + "\n- Hydration goal per Dr. Example: 128oz/day, no specific timing required.",
  });
  assert.equal(r, null);
});

// --- Not MEMORY.md -> pass through ---

t("does not block the same content written to a different file", () => {
  const r = inspectMemoryHealthWrite({
    filePath: "~/.openclaw/workspace/Health/journal/2026-09-13_weekly.md",
    oldString: HEALTH_SECTION_CONTEXT,
    newString: HEALTH_SECTION_CONTEXT + "\n- **2026-09-13 intake log:** dinner, ate, took vitamins",
  });
  assert.equal(r, null);
});

t("does not block writes with no filePath", () => {
  const r = inspectMemoryHealthWrite({ newString: "dinner ate took" });
  assert.equal(r, null);
});

let hook;
plugin.register({ logger: { info() {}, warn() {} }, on(_name, handler) { hook = handler; } });
const logText = "## Health\n- **2026-09-14 intake log:** Had dinner and took vitamins.";
for (const [name, params] of [
  ["edit", { path: "workspace/MEMORY.md", edits: [
    { oldText: "## Contacts", newText: "## Contacts\n- Updated" },
    { oldText: "## Health", newText: logText },
  ] }],
  ["write", { path: "workspace/MEMORY.md", content: logText }],
  ["apply_patch", { input: "*** Begin Patch\n*** Update File: workspace/MEMORY.md\n@@\n ## Health\n+- Had dinner and took vitamins.\n*** End Patch" }],
]) {
  const result = await hook({ toolName: name, params });
  t(`registered hook blocks deployed ${name} schema`, () => assert.equal(result?.block, true));
}
const allowed = await hook({ toolName: "write", params: { path: "Health/journal/test.md", content: logText } });
t("registered hook permits the actual journal destination", () => assert.equal(allowed, undefined));
console.log(`\n${pass} passed`);
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";
await runRuntimeFixtures("memory-health-guard");

// --- 2026-09-18: the health log files themselves ---
import { inspectToolWrite, inspectHealthLogExec } from "./index.mjs";

const FOOD_LOG = "~/.openclaw/workspace/Skills/nutrition/config/food_log.json";

t("blocks the real 2026-09-18 incident: edit tool on food_log.json", () => {
  const r = inspectToolWrite("edit", { path: FOOD_LOG, edits: [{ oldText: '"choice": "shake"', newText: '"choice": "taco"' }] });
  assert.equal(r?.block, true);
  assert.match(r.blockReason, /log_day\.py/);
});

t("blocks the real 2026-09-18 incident: python one-liner rewriting the log via exec", () => {
  const cmd = `cd ~/.openclaw/workspace && python3 -c "\nimport json\nwith open('Health/nutrition/food_log.json', 'r') as f:\n    log = json.load(f)\nwith open('Health/nutrition/food_log.json', 'w') as f:\n    json.dump(log, f)"`;
  assert.equal(inspectToolWrite("exec", { command: cmd })?.block, true);
});

t("blocks write / apply_patch / shell redirect / sed -i / rm on any health log", () => {
  assert.equal(inspectToolWrite("write", { path: "~/.openclaw/hydration_log.json", content: "[]" })?.block, true);
  assert.equal(inspectToolWrite("apply_patch", { input: "*** Update File: Skills/shared/readiness_log.json\n+x" })?.block, true);
  assert.equal(inspectHealthLogExec("echo '{}' > Skills/nutrition/config/supplement_log.json")?.block, true);
  assert.equal(inspectHealthLogExec("sed -i '' 's/shake/taco/' food_log.json")?.block, true);
  assert.equal(inspectHealthLogExec("rm ~/.openclaw/medication_log.json")?.block, true);
});

t("reading a health log passes", () => {
  assert.equal(inspectHealthLogExec("cat Skills/nutrition/config/food_log.json"), null);
  assert.equal(inspectHealthLogExec("jq '.entries[0]' Skills/nutrition/config/food_log.json 2>/dev/null"), null);
  assert.equal(inspectHealthLogExec(`python3 -c "import json; print(json.load(open('food_log.json'))['entries'][0])" 2>&1`), null);
  assert.equal(inspectHealthLogExec("find ~/.openclaw/workspace -name food_log.json -type f"), null);
});

t("the sanctioned scripts and unrelated commands pass", () => {
  assert.equal(inspectHealthLogExec(`python3 Skills/dr-physician/scripts/log_day.py --meal breakfast "burrito" --supplement multivitamin`), null);
  assert.equal(inspectHealthLogExec(`python3 Skills/nutrition/scripts/confirm_meal.py --date 2026-09-19 --slot 1 --action eaten --what "eggs"`), null);
  assert.equal(inspectHealthLogExec("echo hi > /tmp/x.json"), null);
  assert.equal(inspectToolWrite("edit", { path: "~/.openclaw/workspace/Skills/nutrition/config/nutrition_config.json", edits: [{ oldText: "a", newText: "b" }] }), null);
  assert.equal(inspectToolWrite("write", { path: "~/.openclaw/workspace/notes/food_log_ideas.md", content: "x" }), null);
});

console.log(`\n${pass} passed (incl. health-log-file guard)`);
