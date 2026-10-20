import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";
export const BUILD_STAMP = pluginBuildStamp(import.meta.url);

/*
 * memory-health-guard — deterministic guard against health-tracking data
 * (meals, supplements, recovery movement) being written into
 * workspace/MEMORY.md instead of the real tracking scripts.
 *
 * WHY THIS EXISTS
 * 2026-09-13, twice within a few hours: The user free-text-reported real events
 * ("had dinner + a supplement," then "breakfast was food+coffee,
 * snack was fruit, did a 20min swim") to the live
 * Telegram agent. RULES.md's "Daily Reminder/Check-in Replies in Free
 * Text" section already covers exactly this case (route to
 * confirm_meal.py/log_food.py, log_supplement.py, log_movement.py,
 * add_note.py) — and had already been confirmed re-read hours earlier —
 * but both times the model just did `read workspace/MEMORY.md` ->
 * `edit workspace/MEMORY.md`, appending a dated bullet under its own
 * pre-existing "## Health" section instead. Traced the actual tool calls;
 * this is a model-adherence miss, not a missing rule or a stale cache.
 *
 * A prose cross-reference in a different file ("see RULES.md") wasn't
 * enough to stop a model that had already decided MEMORY.md's Health
 * section was the right landing spot — so this plugin puts the stop sign
 * at the point of attraction: the `edit`/`write` tool call itself, before
 * it lands.
 *
 * WHAT IT DOES (before_tool_call, edit + write only)
 * Blocks an edit/write to workspace/MEMORY.md when the text being written
 * (a) is anywhere near the "## Health" section (the edit's old_string,
 * new_string, or a full-file write's content mentions "## Health" or one
 * of that section's stable anchor phrases), AND (b) reads like a health
 * tracking log entry rather than a durable structural fact — a
 * bold-dated bullet ("**2026-09-13 ... log:**") naming a meal window,
 * supplement, or movement/activity, or looser food/supplement/movement
 * vocabulary paired with a report-style verb ("ate," "had," "took,"
 * "logged," "swam," a duration in minutes).
 *
 * Narrow and exact-match on purpose (see plugins/GUARD-PATTERN.md) — a
 * genuine structural edit to the Health section (a new provider, an
 * updated "latest panel" pointer) doesn't match this shape and passes
 * through untouched. Under-catching some future phrasing is an accepted
 * cost of not false-positiving on legitimate Health-section maintenance.
 *
 * SECOND GUARD (2026-09-18) — the health LOG FILES themselves
 * That morning the user typed a breakfast report; the agent logged a "shake" he
 * never mentioned, and when corrected it opened
 * Skills/nutrition/config/food_log.json and rewrote the entry with the
 * `edit` tool (after first trying a `python3 -c "...json.dump..."` one-liner
 * via exec). Dr. Example's report is rendered straight from these files; a
 * hand-edit skips every check the log scripts make (dedupe, schema, locking,
 * the Telegram message update) and can put words in the record the user never
 * said. The user approved blocking it (2026-09-18).
 * - edit / write / apply_patch whose target is one of HEALTH_LOG_FILES -> block.
 * - exec whose command names one of those files together with a write
 *   construct (json.dump, open(..., "w"/"a"), > / >> redirect, tee, sed -i,
 *   mv/cp/rm/truncate) -> block. Reading them (cat, jq, python read) passes.
 * The block reason names the sanctioned route: Skills/dr-physician/scripts/log_day.py
 * (and confirm_meal.py --what to correct a meal).
 *
 * WHAT IT LEAVES ALONE
 * Every other file. Reads of MEMORY.md. Edits to MEMORY.md that don't
 * touch the Health section or don't look like a log entry (e.g. renaming
 * a golf course, updating a contact). Cron/job-driven writes
 * (`event.context.jobId` set) — no legitimate cron writes MEMORY.md's
 * Health section, so this is belt-and-suspenders, not a real exemption.
 */

const MEMORY_MD_BASENAME = "MEMORY.md";

const HEALTH_ANCHOR_RE = new RegExp(
  [
    "## Health\\b",
    "HARD RULE: no medical interpretation",
    "NEVER log a meal/supplement/movement/intake report here",
    "Health domain lives at",
  ].join("|"),
);

// A bolded date immediately followed (within ~100 chars) by log-shaped
// vocabulary -- the exact shape of both real incidents ("**2026-09-13
// intake & activity log:**", "**2026-09-13 intake log:**"). Covers every
// category RULES.md's "Daily Reminder/Check-in Replies in Free Text"
// section names (meals, supplements, hydration, medication, movement),
// not just the two that have actually misfired so far.
const BOLD_DATE_LOG_RE =
  /\*\*(?:19|20)\d{2}-\d{2}-\d{2}[^*]{0,100}?(log|intake|activity|dinner|breakfast|lunch|snack|supplement|movement|workout|swim|sauna|hydration|medication|meds?\b)/i;

// Looser catch: food/supplement/hydration/medication/movement vocabulary
// paired with a report-style verb, even without a bolded date prefix.
// Checked in both orders ("took my meds" / "meds ... taken") since real
// phrasing isn't consistently noun-then-verb. Deliberately NOT including a
// bare "\d+ min"/"\d+ oz" here -- a static goal ("Hydration goal: 128oz/
// day") contains a number+unit too; only an actual report verb counts.
const LOOSE_VOCAB = "dinner|breakfast|lunch|snack|supplements?|vitamins?|sauna|hydration|water|medication|meds?\\b|dose|pill";
const LOOSE_VERB = "ate|had|took|logged|swam|swimming|drank|drink";
const LOOSE_LOG_RE = new RegExp(
  `\\b(?:${LOOSE_VOCAB})\\b[\\s\\S]{0,80}\\b(?:${LOOSE_VERB})\\b|\\b(?:${LOOSE_VERB})\\b[\\s\\S]{0,80}\\b(?:${LOOSE_VOCAB})\\b`,
  "i",
);

// Exact basenames, not a pattern: a new log gets added here on purpose.
export const HEALTH_LOG_FILES = new Set([
  "food_log.json",
  "supplement_log.json",
  "breakfast_anchor_log.json",
  "readiness_log.json",
  "restrictions_log.json",
  "movement_log.json",
  "premeal_break_log.json",
  "feeling_log.json",
  "journal_notes.json",
  "hydration_log.json",
  "medication_log.json",
]);

const HEALTH_LOG_BLOCK_REASON =
  "Health log files are never edited by hand — Dr. Example's report is rendered straight from them, and a " +
  "direct edit skips every check the log scripts make (2026-09-18: a hand-edit followed a logged \"shake\" " +
  "the user never mentioned). To log or correct something the user said, run " +
  "`python3 Skills/dr-physician/scripts/log_day.py` with one flag per thing he said (`--help` lists them); " +
  "`--meal <window> \"<his words>\"` on a window that is already logged corrects it. If an entry needs a " +
  "fix those commands can't make, tell the user plainly that it needs a Claude Code session — do not work around this.";

const EXEC_WRITE_RE =
  /json\.dump|\.write_text\(|\.write\(|open\([^)]*,\s*["'](?:w|a|r\+|w\+|a\+)|(?<![0-9&=<>-])>>?\s*(?!&|\/dev\/null)[^\s>]|\btee\b|\bsed\s+(?:-[a-zA-Z]*\s+)*-i|\b(?:mv|cp|rm|truncate|install)\b/;

export function inspectHealthLogPath(filePath) {
  return HEALTH_LOG_FILES.has(basename(filePath)) ? { block: true, blockReason: HEALTH_LOG_BLOCK_REASON } : null;
}

export function inspectHealthLogExec(command) {
  if (typeof command !== "string" || !command) return null;
  const namesLog = [...HEALTH_LOG_FILES].some((name) => command.includes(name));
  if (!namesLog || !EXEC_WRITE_RE.test(command)) return null;
  return { block: true, blockReason: HEALTH_LOG_BLOCK_REASON };
}

function basename(p) {
  if (typeof p !== "string") return "";
  const parts = p.split("/");
  return parts[parts.length - 1] || "";
}

function looksLikeHealthLogEntry(text) {
  if (!text) return false;
  return BOLD_DATE_LOG_RE.test(text) || LOOSE_LOG_RE.test(text);
}

/**
 * @param {{filePath?: string, oldString?: string, newString?: string, content?: string}} args
 * @returns {null | {block: true, blockReason: string}}
 */
export function inspectMemoryHealthWrite({ filePath, oldString, newString, content } = {}) {
  if (basename(filePath) !== MEMORY_MD_BASENAME) return null;

  const addedText = newString ?? content ?? "";
  const nearHealthSection = HEALTH_ANCHOR_RE.test(`${oldString ?? ""}\n${newString ?? ""}\n${content ?? ""}`);
  if (!nearHealthSection) return null;
  if (!looksLikeHealthLogEntry(addedText)) return null;

  return {
    block: true,
    blockReason:
      "This looks like a meal/supplement/movement report, not a structural note — MEMORY.md's Health " +
      "section isn't read by the Dr. Example journal, so it would just sit here doing nothing (this happened " +
      "twice on 2026-09-13). Route it instead: nutrition/scripts/confirm_meal.py or log_food.py (meals), " +
      "log_supplement.py (supplements), hevy-workout/scripts/log_movement.py (recovery movement), " +
      "shared/add_note.py (extra detail those scripts' fixed fields can't hold, e.g. an exercise duration). " +
      "See RULES.md \"Daily Reminder/Check-in Replies in Free Text.\"",
  };
}

export function inspectToolWrite(toolName, params = {}) {
  const filePath = params.path ?? params.file_path;
  if (toolName === "exec") return inspectHealthLogExec(params.command);
  if (toolName === "write" || toolName === "edit") {
    const logDecision = inspectHealthLogPath(filePath);
    if (logDecision) return logDecision;
  }
  if (toolName === "apply_patch") {
    const patchText = params.input ?? params.patch ?? "";
    for (const m of patchText.matchAll(/^\*\*\* (?:Update|Add|Delete) File: (.+)$/gm)) {
      const logDecision = inspectHealthLogPath(m[1].trim());
      if (logDecision) return logDecision;
    }
  }
  if (toolName === "write") {
    return inspectMemoryHealthWrite({ filePath, content: params.content });
  }
  if (toolName === "edit") {
    const edits = params.edits ?? [{ oldText: params.old_string, newText: params.new_string }];
    for (const edit of edits) {
      const decision = inspectMemoryHealthWrite({
        filePath, oldString: edit.oldText, newString: edit.newText,
      });
      if (decision) return decision;
    }
  }
  if (toolName === "apply_patch") {
    const patch = params.input ?? params.patch ?? "";
    for (const block of patch.split(/(?=\*\*\* (?:Update|Add) File: )/)) {
      const target = block.match(/^\*\*\* (?:Update|Add) File: (.+)/)?.[1];
      if (!target) continue;
      const decision = inspectMemoryHealthWrite({
        filePath: target.trim(), oldString: block,
        newString: block.split("\n").filter(line => line.startsWith("+")).map(line => line.slice(1)).join("\n"),
      });
      if (decision) return decision;
    }
  }
  return null;
}

export default {
  id: "memory-health-guard",
  name: "Memory Health Guard",
  description:
    "Blocks meal/supplement/recovery-movement reports from being written into workspace/MEMORY.md's Health " +
    "section instead of the real tracking scripts (confirm_meal.py, log_food.py, log_supplement.py, " +
    "log_movement.py, add_note.py) — that section isn't read by the Dr. Example weekly journal.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    logPluginBuildStamp(api, "memory-health-guard", BUILD_STAMP);

    api.on(
      "before_tool_call",
      async (event) => {
        const toolName = event?.toolName;
        if (!["edit", "write", "apply_patch", "exec"].includes(toolName)) return;

        let decision;
        try {
          decision = inspectToolWrite(toolName, event?.params);
        } catch (err) {
          log.warn?.(`[memory-health-guard] inspect error: ${err?.message ?? err}`);
          return;
        }
        if (!decision) return;

        log.warn?.(`[memory-health-guard] blocked ${toolName}: ${decision.blockReason.slice(0, 60)}…`);
        return { block: true, blockReason: decision.blockReason };
      },
      { priority: 60 },
    );

    log.info?.("[memory-health-guard] before_tool_call handler registered");
  },
};
