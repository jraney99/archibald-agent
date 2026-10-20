import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";
export const BUILD_STAMP = pluginBuildStamp(import.meta.url);

/*
 * calendar-write-guard — deterministic guard on ad-hoc calendar WRITES.
 *
 * WHY THIS EXISTS
 * The write-side twin of calendar-read-guard. RULES.md mandates the wrapper
 * scripts — Skills/shared/add_calendar_event.py (creates) and
 * update_calendar_event.py (reschedule/rename/move) — for every ad-hoc
 * calendar change during chat. On the weak live model (Haiku, thinking off)
 * the agent keeps hand-driving `gog calendar` instead and falls into three
 * documented failure classes:
 *
 *   1. Invented flags. `gog calendar create --title ... --start ... --end ...`
 *      -> "unknown flag", burns a tool call, re-derives real flags from --help.
 *      (feedback-gog-cal-cli-flags, LEARNINGS LRN-20260714-001)
 *
 *   2. Missing timezone flags on create -> event stored as UTC, displays at the
 *      wrong hour. (Documentation/rules/13-calendar-pre-flight-run-before-every-calendar-draft.md
 *      step 5; LEARNINGS line 103)
 *
 *   3. The delete -> recreate death spiral. gog prints an event's dateTime
 *      digits in the LOCAL SYSTEM offset regardless of the event's own
 *      timeZone (Documentation/rules/18-google-calendar-gog-owner-gmail-com.md).
 *      The agent reads back a
 *      correct write, sees "wrong" digits, deletes and recreates — again and
 *      again. One real transcript: "move my cleaning to Wednesday" became ~12
 *      unapproved calendar writes and a mis-labelled timeZone.
 *
 * WHAT IT DOES  (before_tool_call, exec only)
 *   - BLOCK a `gog calendar create|update` that uses an invented flag, with the
 *     real flag list and a pointer to the wrapper.
 *   - BLOCK a `gog calendar create` missing --start-timezone / --end-timezone
 *     (unless --all-day).
 *   - On a `gog calendar create` of a --summary this session already created
 *     SUCCESSFULLY (counted in after_tool_call) or deleted, require approval —
 *     the recreate loop is exactly what should have been a single confirmed
 *     reschedule. Failed attempts never count.
 *
 *   - BLOCK running add_calendar_event.py from chat without --propose: calendar adds
 *     are shown as a draft with an Approve button first (RULES.md core rule).
 *
 * NOT TOUCHED: cron/job runs (ctx.jobId set — those come from vetted scripts),
 * --help, the wrapper scripts themselves, `gog calendar delete` on its own,
 * reads (that's calendar-read-guard), and any first create of a new event.
 */

const ADD_WRAPPER = "Skills/shared/add_calendar_event.py";
const UPD_WRAPPER = "Skills/shared/update_calendar_event.py";

const GOG_CAL_WRITE_RE =
  /\bgog\s+cal(?:endar)?\s+(create|add|update|edit|delete|del|rm|remove|move)\b/i;

// Flags the model invents for create/update. Each must NOT match a real flag:
// --start-timezone / --end-timezone are real, so --start / --end are matched
// only when not followed by "-" or a word char.
const INVENTED_FLAG_RES = [
  { re: /--title(?![-\w])/, real: "--summary" },
  { re: /--start(?![-\w])/, real: "--from" },
  { re: /--end(?![-\w])/, real: "--to" },
  { re: /--start-date(?![-\w])/, real: "--from (RFC3339, with time)" },
  { re: /--end-date(?![-\w])/, real: "--to (RFC3339, with time)" },
  { re: /--date(?![-\w])/, real: "--from / --to" },
  { re: /--format(?![-\w])/, real: "-j / --json or -p / --plain" },
  { re: /--event-id(?![-\w])/, real: "positional <calendarId> <eventId> (no flag)" },
  { re: /--guest-email(?![-\w])/, real: "--attendees" },
];

const REAL_FLAGS_LINE =
  "Real gog calendar create/update flags: --summary, --from, --to, " +
  "--start-timezone, --end-timezone, --location, --description, --attendees, " +
  "--reminder, --with-meet. calendarId/eventId are positional.";

function verbOf(cmd) {
  const m = GOG_CAL_WRITE_RE.exec(cmd);
  if (!m) return null;
  const v = m[1].toLowerCase();
  if (v === "add") return "create";
  if (v === "edit") return "update";
  if (v === "del" || v === "rm" || v === "remove") return "delete";
  return v; // create | update | delete | move
}

function grabSummary(cmd) {
  const m =
    /--summary[=\s]+"([^"]+)"/.exec(cmd) ||
    /--summary[=\s]+'([^']+)'/.exec(cmd) ||
    /--summary[=\s]+(\S+)/.exec(cmd);
  return m ? m[1].trim() : null;
}

// A quoted argument on a `delete` that looks like an event TITLE, not an id.
function grabDeletedTitle(cmd) {
  const quoted = [...cmd.matchAll(/"([^"]+)"|'([^']+)'/g)].map((m) => m[1] ?? m[2]);
  for (const q of quoted) {
    if (/\s/.test(q) && !/^--/.test(q)) return q.trim(); // has a space -> a title
  }
  return null;
}

function normKey(s) {
  return String(s).toLowerCase().replace(/\s+/g, " ").trim();
}

/**
 * @param {string} rawCmd
 * @param {{creates: Map<string,number>, deleted: Set<string>}} session  mutated in place
 * @returns {null | {block:true, blockReason:string} | {requireApproval:object}}
 */
export function inspectCalendarWrite(rawCmd, session) {
  const cmd = String(rawCmd ?? "").trim();
  if (!cmd) return null;
  if (!GOG_CAL_WRITE_RE.test(cmd)) return null;
  if (/--help\b|\s-h\b/.test(cmd)) return null;
  if (/add_calendar_event\.py|update_calendar_event\.py/.test(cmd)) return null;

  const verb = verbOf(cmd);

  // --- delete: just record the title so a later recreate trips the loop guard.
  if (verb === "delete") {
    const t = grabDeletedTitle(cmd);
    if (t) session.deleted.add(normKey(t));
    return null;
  }

  if (verb !== "create" && verb !== "update") return null;

  // --- Check 1: invented flags (self-correctable -> block).
  for (const { re, real } of INVENTED_FLAG_RES) {
    if (re.test(cmd)) {
      const bad = re.source.replace(/\(\?!\[-\\w\]\)/, "");
      return {
        block: true,
        blockReason:
          `\`${bad}\` is not a real \`gog calendar ${verb}\` flag — use \`${real}\`. ` +
          `${REAL_FLAGS_LINE} ` +
          `Better: don't hand-build the command. Use \`python3 ${verb === "create" ? ADD_WRAPPER : UPD_WRAPPER}\` ` +
          `— it is correct by construction and verifies the write. See Documentation/rules/18-google-calendar-gog-owner-gmail-com.md "gog Calendar CLI — Exact Flags".`,
      };
    }
  }

  // --- Check 2: create without both timezone flags (-> block).
  if (verb === "create" && !/--all-day\b/.test(cmd)) {
    const hasStartTz = /--start-timezone[=\s]/.test(cmd);
    const hasEndTz = /--end-timezone[=\s]/.test(cmd);
    if (!hasStartTz || !hasEndTz) {
      return {
        block: true,
        blockReason:
          "Calendar creates must pass BOTH --start-timezone and --end-timezone " +
          "(Documentation/rules/13-calendar-pre-flight-run-before-every-calendar-draft.md step 5) or the event is stored as UTC and shows at the wrong hour. " +
          `Use \`python3 ${ADD_WRAPPER} --who ... --venue ... --from ... --to ... --tz America/Chicago --location ...\` ` +
          "— it always sets both, adds an explicit reminder, and verifies the event on read-back.",
      };
    }
  }

  // --- Check 3: repeated recreate of the same event in one session (-> approval).
  // Only creates that actually succeeded count (recordCalendarCreate, run from
  // after_tool_call). Counting attempts made a failed first try — e.g. gog's
  // "expected <calendarId>" — look like an existing event (2026-09-21, an airport pickup).
  if (verb === "create") {
    const summary = grabSummary(cmd);
    if (summary) {
      const key = normKey(summary);
      const priorCreates = session.creates.get(key) ?? 0;
      const wasDeleted = session.deleted.has(key);
      if (priorCreates >= 1 || wasDeleted) {
        const shown = summary.length > 60 ? `${summary.slice(0, 57)}...` : summary;
        return {
          requireApproval: {
            title: "Re-create a calendar event?",
            // Gateway schema caps this at 256 chars. Longer made every request fail as
            // "gateway unavailable" and the agent pasted "/approve <cmd>" into chat.
            description:
              `Archibald already ${wasDeleted ? "deleted" : "created"} "${shown}" in this chat and wants to create it again. ` +
              "Usually that means chasing a time-display quirk, not a real problem. Allow only if a new event is really needed.",
            severity: "warning",
            timeoutMs: 120000,
            timeoutBehavior: "deny",
            allowedDecisions: ["allow-once", "deny"],
            pluginId: "calendar-write-guard",
          },
        };
      }
    }
  }

  return null;
}

// Running the add wrapper itself (not a file that merely contains its name).
const ADD_WRAPPER_RUN_RE = /\bpython3?\b[^|;&\n]*?(?:^|[\s/])add_calendar_event\.py\b/;

/**
 * From chat, the add wrapper must run with --propose: The user sees the draft and taps
 * Approve before anything is written (RULES.md core rule; Tier (b)). Run directly, a
 * wrong assumption goes straight to his calendar — 2026-09-21 a screenshot of a flight
 * he was PICKING SOMEONE UP from was written up as his own flight, with no draft shown.
 * Scripts that create after an approval call the wrapper internally, never via exec.
 */
export function inspectWrapperCreate(rawCmd) {
  const cmd = String(rawCmd ?? "");
  if (!ADD_WRAPPER_RUN_RE.test(cmd)) return null;
  if (/--help\b|\s-h\b|--propose\b/.test(cmd)) return null;
  return {
    block: true,
    blockReason:
      "Calendar adds from chat must be proposed, not written directly: re-run this same " +
      "add_calendar_event.py command with --propose. It sends the user the draft with an Approve " +
      "button and the event is created only when he taps it. Do not hand-build `gog calendar create` instead.",
  };
}

/** Record a create that actually succeeded, so a later create of the same title trips Check 3. */
export function recordCalendarCreate(rawCmd, session) {
  const cmd = String(rawCmd ?? "").trim();
  if (verbOf(cmd) !== "create") return;
  if (/add_calendar_event\.py|update_calendar_event\.py/.test(cmd)) return;
  const summary = grabSummary(cmd);
  if (!summary) return;
  const key = normKey(summary);
  session.creates.set(key, (session.creates.get(key) ?? 0) + 1);
}

/** after_tool_call event -> did the command run and exit 0? Unknown shapes count as success
 *  so the recreate-loop guard never silently switches off. */
export function toolCallSucceeded(event) {
  if (event?.error) return false;
  const result = event?.result;
  if (result?.isError === true) return false;
  const details = result?.details;
  if (details?.status === "blocked") return false;
  if (typeof details?.exitCode === "number") return details.exitCode === 0;
  return true;
}

// --- Per-session state, pruned so a long-lived gateway doesn't grow unbounded.
const SESSIONS = new Map(); // sessionKey -> { creates: Map, deleted: Set, seen: number }
const MAX_SESSIONS = 50;

function sessionKeyOf(event, context = {}) {
  const ctx = event?.context ?? {};
  return context.sessionKey ?? context.sessionId ??
    ctx.sessionKey ?? ctx.sessionId ?? event?.sessionKey ?? "default";
}

function sessionState(key) {
  let s = SESSIONS.get(key);
  if (!s) {
    if (SESSIONS.size >= MAX_SESSIONS) {
      let oldestK = null;
      let oldestT = Infinity;
      for (const [k, v] of SESSIONS) {
        if (v.seen < oldestT) {
          oldestT = v.seen;
          oldestK = k;
        }
      }
      if (oldestK) SESSIONS.delete(oldestK);
    }
    s = { creates: new Map(), deleted: new Set(), seen: 0 };
    SESSIONS.set(key, s);
  }
  s.seen = Date.now();
  return s;
}

export default {
  id: "calendar-write-guard",
  name: "Calendar Write Guard",
  description:
    "Blocks invented gog-calendar flags and tz-less creates; requires approval before re-creating an event already touched this session.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    logPluginBuildStamp(api, "calendar-write-guard", BUILD_STAMP);

    api.on(
      "before_tool_call",
      async (event, context = {}) => {
        if (event?.toolName !== "exec") return;
        const command = event?.params?.command;
        if (typeof command !== "string") return;

        // Cron/job-driven calendar writes come from vetted scripts, not ad-hoc
        // gog — leave them alone (matches calendar-read-guard's ad-hoc scope).
        const ctx = event.context ?? {};
        if (ctx.jobId || context.jobId) return;

        const unproposed = inspectWrapperCreate(command);
        if (unproposed) {
          log.info?.(`[calendar-write-guard] blocked unproposed wrapper create\n  cmd: ${command}`);
          return unproposed;
        }
        if (!GOG_CAL_WRITE_RE.test(command)) return;

        const session = sessionState(sessionKeyOf(event, context));

        let decision;
        try {
          decision = inspectCalendarWrite(command, session);
        } catch (err) {
          log.warn?.(`[calendar-write-guard] inspect error: ${err?.message ?? err}`);
          return;
        }
        if (!decision) return;

        if (decision.block) {
          log.info?.(
            `[calendar-write-guard] blocked calendar write\n  cmd: ${command}\n  why: ${decision.blockReason}`,
          );
          return { block: true, blockReason: decision.blockReason };
        }
        if (decision.requireApproval) {
          log.info?.(
            `[calendar-write-guard] approval required for recreate\n  cmd: ${command}`,
          );
          return { requireApproval: decision.requireApproval };
        }
      },
      { priority: 60 },
    );

    api.on("after_tool_call", async (event, context = {}) => {
      if (event?.toolName !== "exec") return;
      const command = event?.params?.command;
      if (typeof command !== "string" || !GOG_CAL_WRITE_RE.test(command)) return;
      if (!toolCallSucceeded(event)) return;
      try {
        recordCalendarCreate(command, sessionState(sessionKeyOf(event, context)));
      } catch (err) {
        log.warn?.(`[calendar-write-guard] record error: ${err?.message ?? err}`);
      }
    });

    log.info?.("[calendar-write-guard] before_tool_call + after_tool_call handlers registered");
  },
};
