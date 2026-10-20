import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";
export const BUILD_STAMP = pluginBuildStamp(import.meta.url);

/*
 * calendar-read-guard — deterministic reroute of ad-hoc calendar reads.
 *
 * WHY THIS EXISTS
 * RULES.md and TOOLS.md both mandate `Skills/shared/fetch_calendar.py` for every
 * ad-hoc schedule read — it is the only path that spans all three calendars
 * (Personal + Work + Side project). The live agent, on the weak model config
 * (Haiku, thinking off), keeps ignoring the wrapper and hand-driving `gog`:
 *
 *   2026-09-01  "what do we need to catch up on?"
 *     1. gog calendar list --start-date ... --end-date ...   -> unknown flag, hard error
 *     2. gog calendar list ... -j | head -40                 -> Personal only, JSON truncated to 1 event
 *        => reported just today's appointment, then asked the user how long the trip was
 *           (the return flight on 9/8 was sitting on the calendar)
 *   same session, "Check my calendar for duration"
 *     3. fetch_calendar.py --days 7 --json | jq '.events[]|select(.start.dateTime...)'
 *                                                            -> wrapper schema is start_iso, not
 *                                                               .start.dateTime => (no output)
 *     4. gog calendar list ... -j | jq ... | head -20        -> finally worked, Personal only
 *
 * Documented pattern, not a one-off: memory feedback-gog-cal-cli-flags,
 * feedback-gog-datetime-display-confusion ("model-adherence gap, not a docs gap").
 *
 * WHAT IT DOES
 * Registers a `before_tool_call` hook. When an `exec` command is an ad-hoc
 * calendar READ — a raw `gog cal(endar) list|ls|events|search|agenda`, or a
 * `fetch_calendar.py` call wrapped in a pipe — it replaces the whole command
 * with a clean `python3 fetch_calendar.py <window> [--query ...]` in text mode.
 * Date hints in the original (--from/--to/--days, and the flags the model likes
 * to invent: --start-date/--end-date/--start/--end/--date) are carried over; a
 * too-narrow or missing window is widened so a read can't miss an event a week
 * out again.
 *
 * NOT TOUCHED: calendar writes (create/update/delete/move/respond), reads that
 * already name --calendar(s) explicitly (deliberate single-calendar), --help,
 * anything chained with && / ; / ||, and clean fetch_calendar.py calls with no pipe.
 */

const WRAPPER = "~/.openclaw/workspace/Skills/shared/fetch_calendar.py";

const DEFAULTS = { defaultDays: 21, minDays: 10, minSearchDays: 30 };

// Raw gog calendar READ subcommands / aliases.
const GOG_READ_RE = /\bgog\s+cal(?:endar)?\s+(?:list|ls|events|event|search|agenda|show)\b/i;
// gog calendar WRITE verbs — never rewrite these.
const GOG_WRITE_RE = /\bgog\s+cal(?:endar)?\s+(?:create|add|update|edit|delete|del|rm|remove|move|respond|accept|decline|rsvp|import|quickadd)\b/i;
// A fetch_calendar.py call that's been piped into jq/head/grep/etc.
const WRAPPER_PIPE_RE = /fetch_calendar\.py\b[^\n]*\|/;
// The wrapper/event-adder scripts we must not clobber.
const OTHER_CAL_SCRIPT_RE = /(?:add|update)_calendar_event\.py/;

const DATE_RE = "(\\d{4}-\\d{2}-\\d{2})";

function grab(cmd, flags) {
  for (const f of flags) {
    const m = new RegExp(`${f}[=\\s]+${DATE_RE}`).exec(cmd);
    if (m) return m[1];
  }
  return null;
}

function grabInt(cmd, flags) {
  for (const f of flags) {
    const m = new RegExp(`${f}[=\\s]+(\\d{1,3})\\b`).exec(cmd);
    if (m) return parseInt(m[1], 10);
  }
  return null;
}

function grabQuery(cmd) {
  const m =
    /--query[=\s]+"([^"]+)"/.exec(cmd) ||
    /--query[=\s]+'([^']+)'/.exec(cmd) ||
    /--query[=\s]+(\S+)/.exec(cmd) ||
    /--search[=\s]+"([^"]+)"/.exec(cmd) ||
    /--search[=\s]+'([^']+)'/.exec(cmd);
  return m ? m[1] : null;
}

function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function daysBetween(a, b) {
  return Math.round(
    (new Date(`${b}T00:00:00Z`) - new Date(`${a}T00:00:00Z`)) / 86400000,
  );
}

function shSingleQuote(s) {
  return `'${String(s).replace(/'/g, `'\\''`)}'`;
}

/**
 * Given the model's exec command, return a replacement command string, or null
 * to leave it alone.
 */
export function rewriteCalendarRead(rawCmd, cfg = DEFAULTS) {
  const cmd = String(rawCmd ?? "").trim();
  if (!cmd) return null;

  const isGogRead = GOG_READ_RE.test(cmd);
  const isWrapperPipe = WRAPPER_PIPE_RE.test(cmd);
  if (!isGogRead && !isWrapperPipe) return null;

  // Hard skips.
  if (GOG_WRITE_RE.test(cmd)) return null;
  if (OTHER_CAL_SCRIPT_RE.test(cmd)) return null;
  if (/--help\b|-h\b/.test(cmd)) return null;
  if (/&&|\|\||;/.test(cmd)) return null; // chained with real other work — hands off
  // Deliberate single-calendar read (RULES.md explicitly allows this).
  if (isGogRead && /--calendars?[=\s]/.test(cmd)) return null;

  const { defaultDays, minDays, minSearchDays } = { ...DEFAULTS, ...cfg };

  let from = grab(cmd, ["--from", "--start-date", "--start", "--after", "--since", "--date", "--day"]);
  let to = grab(cmd, ["--to", "--end-date", "--end", "--before", "--until", "--through"]);
  const days = grabInt(cmd, ["--days", "--next", "--for"]);
  const query = grabQuery(cmd);

  const args = [];

  if (query) {
    // Targeted lookup — respect the window the model asked for, just widen a
    // bare search so "when is my next dentist" reaches far enough.
    if (from) {
      args.push("--from", from);
      if (to) args.push("--to", to);
      else args.push("--to", addDays(from, minSearchDays));
    } else if (days != null) {
      args.push("--days", String(days));
    } else {
      args.push("--days", String(minSearchDays));
    }
    args.push("--query", query);
  } else if (from) {
    if (!to || daysBetween(from, to) < minDays) to = addDays(from, minDays);
    args.push("--from", from, "--to", to);
  } else if (days != null) {
    args.push("--days", String(Math.max(days, minDays)));
  } else {
    args.push("--days", String(defaultDays));
  }

  const quoted = args.map((a) => (/^[-\w:.\/]+$/.test(a) ? a : shSingleQuote(a)));
  return `python3 ${WRAPPER} ${quoted.join(" ")}`;
}

export default {
  id: "calendar-read-guard",
  name: "Calendar Read Guard",
  description:
    "Reroutes raw gog-calendar reads and broken fetch_calendar.py pipes to a clean multi-calendar fetch_calendar.py call.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    logPluginBuildStamp(api, "calendar-read-guard", BUILD_STAMP);

    api.on(
      "before_tool_call",
      async (event) => {
        if (event?.toolName !== "exec") return;
        const params = event.params ?? {};
        const original = params.command;
        if (typeof original !== "string") return;

        const cfg = { ...DEFAULTS, ...(event.context?.pluginConfig ?? api.config ?? {}) };

        let next;
        try {
          next = rewriteCalendarRead(original, cfg);
        } catch (err) {
          log.warn?.(`[calendar-read-guard] rewrite error: ${err?.message ?? err}`);
          return;
        }
        if (!next || next === original.trim()) return;

        log.info?.(
          `[calendar-read-guard] rerouted calendar read\n  from: ${original}\n  to:   ${next}`,
        );
        return { params: { ...params, command: next } };
      },
      { priority: 60 },
    );

    log.info?.("[calendar-read-guard] before_tool_call handler registered");
  },
};
