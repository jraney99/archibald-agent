import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";
export const BUILD_STAMP = pluginBuildStamp(import.meta.url);

/*
 * callback-dispatch — deterministic "confirm:" / "skip:" callback handler.
 *
 * WHY THIS EXISTS
 * A Telegram inline-button tap is delivered to OpenClaw as a synthetic plain-text
 * message ("callback_data: confirm:hydration-32") and, with no plugin claiming it,
 * routed to the agent like any typed message. The agent then has to recognise a
 * bare token mid-conversation and match it against a prose table in RULES.md —
 * on the weakest model config (Haiku, thinking off), sometimes on a
 * continuation turn where RULES.md is not even in the system prompt. It misfires
 * a few percent of the time: "the callback appears stale", re-answers the
 * previous topic, or claims success without running anything.
 *
 * This plugin registers a `before_dispatch` claiming hook — it runs BEFORE the
 * agent turn. If the inbound text is a known "confirm:" / "skip:" callback it
 * runs the mapped script itself and returns { handled: true }, so the agent
 * never sees it. Deterministic: a JSON table, not a paragraph a model might skip.
 *
 * One more deterministic path (2026-09-18): a typed REPLY to a meal nudge is
 * logged here too — see reply-nudge.mjs. Every other non-callback message is
 * ignored and falls through to the agent unchanged.
 *
 * Dispatch map: ./dispatch-table.json (mirrors RULES.md "Inline Button Callbacks").
 */

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { unlink } from "node:fs/promises";
import { readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isOwnerMessage } from "./owner.mjs";
import { mealReplyArgv } from "./reply-nudge.mjs";
import { statusQuestionArgv } from "./status-question.mjs";
import { pendingContext } from "./pending-context.mjs";
import { dispatchFinance, recordFinanceDelivery } from './finance-intake.mjs';
import { inspectFinanceWrite } from './finance-write-guard.mjs';
import { cryptoDestinationArgv } from './crypto-sweep.mjs';

const execFileP = promisify(execFile);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SKILLS_DIR = "~/.openclaw/workspace/Skills";
const OPENCLAW_DIR = "~/.openclaw";
const DEFAULT_TIMEOUT_MS = 20000;

const TABLE_PATH = path.join(HERE, "dispatch-table.json");
const TABLE = JSON.parse(readFileSync(TABLE_PATH, "utf8"));
// Captured at read time, not at register(): the module is cached across plugin
// re-registrations, so a later statSync would report a fresh mtime next to a stale
// in-memory table and make the fingerprint look current when it isn't.
const TABLE_MTIME = statSync(TABLE_PATH).mtimeMs;

// Accepts "confirm:foo", "skip:foo-bar", with or without a leading "callback_data: ".
// Other prefixes ("outfit:formal-<id>") count only when the table has an entry for them,
// so ordinary text containing a colon is never mistaken for a button tap.
const TOKEN_RE = /^(?:callback_data:\s*)?([a-z]+:[a-z][a-z0-9:_-]*)$/i;
// A tap on a button under a PHOTO arrives with the photo's placeholder line first:
//   "<media:image> [file_id:...]\ncallback_data: outfit:formal-4b7e3309d908"
// 2026-09-19: that line made every outfit-board button miss this plugin; the agent
// got the raw callback, answered NO_REPLY once and improvised the second time.
const MEDIA_LINE_RE = /^<media:[^>]*>.*$/;

export function callbackToken(raw) {
  const lines = String(raw ?? "").trim().split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  while (lines.length > 1 && MEDIA_LINE_RE.test(lines[0])) lines.shift();
  if (lines.length !== 1) return null;
  const match = TOKEN_RE.exec(lines[0]);
  if (!match) return null;
  const token = match[1].toLowerCase();
  return /^(?:confirm|skip):/.test(token) || resolveEntry(token) ? token : null;
}

// Exported for button_contract_test.py: "claimed" is not "handled" — an unknown
// confirm:/skip: is claimed and answered "No matching action found".
export function resolveEntry(token) {
  if (Object.prototype.hasOwnProperty.call(TABLE.exact, token)) {
    return { ...TABLE.exact[token] };
  }
  for (const pat of TABLE.patterns) {
    const m = new RegExp(pat.re).exec(token);
    if (!m) continue;
    const argv = pat.argv.map((a) => a.replace(/\$(\d+)/g, (_, n) => m[Number(n)] ?? ""));
    return { argv, reply: pat.reply, timeoutMs: pat.timeoutMs };
  }
  return null;
}

async function runScript(argv, env, timeoutMs) {
  const script = path.join(SKILLS_DIR, argv[0]);
  const rest = argv.slice(1);
  try {
    const { stdout } = await execFileP("python3", ["-W", "ignore", script, ...rest], {
      cwd: path.dirname(script),
      env,
      timeout: timeoutMs || DEFAULT_TIMEOUT_MS,
      maxBuffer: 1024 * 1024,
    });
    return { ok: true, out: String(stdout ?? "").trim() };
  } catch (err) {
    const combined = `${err?.stdout ?? ""}${err?.stderr ?? ""}`.trim();
    return {
      ok: false,
      out: combined || String(err?.message ?? err),
      timedOut: err?.killed === true || err?.signal === "SIGTERM",
    };
  }
}

function withHat(text) {
  const s = String(text ?? "").trim();
  if (!s) return "Done. 🎩";
  return /🎩\s*$/.test(s) ? s : `${s} 🎩`;
}

export default {
  id: "callback-dispatch",
  name: "Callback Dispatch",
  description:
    "Deterministically executes confirm:*/skip:* Telegram button taps before the agent turn.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    logPluginBuildStamp(api, "callback-dispatch", BUILD_STAMP);
    api.on('reply_dispatch', (event, context) => dispatchFinance(event, context, api.config,
      runScript, {...process.env, ...(api.config?.env?.vars ?? {})}), {priority: 70, timeoutMs: 65000});
    api.on('message_sent', event => recordFinanceDelivery(event, runScript,
      {...process.env, ...(api.config?.env?.vars ?? {})}), {priority: 70, timeoutMs: 12000});
    api.on('before_tool_call', event => inspectFinanceWrite(event.toolName, event.params), {priority: 70});

    // Startup fingerprint of the loaded table — the plugin reads dispatch-table.json
    // once at load, so a stale copy (e.g. gateway didn't cleanly restart after an
    // edit) otherwise only surfaces as a mystery "No pending action found" on a
    // real tap. warn-level so it lands in the file log.
    (log.warn ?? console.warn)(
      `[callback-dispatch] table loaded: ${TABLE.patterns.length} patterns, ` +
      `${Object.keys(TABLE.exact).length} exact, ` +
      `mtime ${new Date(TABLE_MTIME).toISOString()}` +
      (statSync(TABLE_PATH).mtimeMs !== TABLE_MTIME ? " — STALE: file changed on disk since load, restart the gateway" : ""),
    );

    // A typed answer to a question we asked (see pending-context.mjs). Adds context
    // only; the agent still interprets the message and runs the rule's script.
    api.on(
      "before_prompt_build",
      (_event, context = {}) => {
        try {
          const note = pendingContext(context);
          return note ? { prependContext: note } : undefined;
        } catch (err) {
          log.warn?.(`[callback-dispatch] pending-context failed: ${String(err?.message ?? err).slice(0, 200)}`);
          return undefined;
        }
      },
      { priority: 50, timeoutMs: 2000 },
    );

    api.on(
      "before_dispatch",
      async (event, context = {}) => {
        const token = callbackToken(event?.content ?? event?.body);
        if (!token) {
          const cryptoArgv = cryptoDestinationArgv(event);
          if (cryptoArgv && isOwnerMessage(event, context, api.config)) {
            const result = await runScript(cryptoArgv,
              {...process.env, ...(api.config?.env?.vars ?? {})}, DEFAULT_TIMEOUT_MS);
            try {
              const response = JSON.parse(result.out);
              if (result.ok && response.handled) return {handled: true, text: withHat(response.text)};
            } catch { /* ordinary replies continue normally */ }
          }
          const replyId = event?.replyToId ?? event?.replyToIdFull;
          if (replyId && /\$|\b(?:charge|purchase|transaction)\b/i.test(String(event.replyToBody ?? '')) &&
              isOwnerMessage(event, context, api.config)) {
            const result = await runScript(['afrexai-personal-finance/scripts/finance_reply.py',
              '--reply-id', String(replyId).split(':').at(-1), '--text', String(event.content ?? event.body ?? '')],
              {...process.env, ...(api.config?.env?.vars ?? {})}, DEFAULT_TIMEOUT_MS);
            try {
              const response = JSON.parse(result.out);
              if (result.ok && response.handled) return {handled: true, text: withHat(response.text)};
            } catch { /* ordinary replies continue normally */ }
          }
          // A typed reply to a meal nudge: log his words ourselves (see
          // reply-nudge.mjs). Anything else goes back to the agent.
          const mealArgv = mealReplyArgv(event);
          // A question about what is logged today: answer it from the logs ourselves
          // (see status-question.mjs). Read-only, so a failure says so and stops.
          const statusArgv = mealArgv ? null : statusQuestionArgv(event);
          if ((!mealArgv && !statusArgv) || !isOwnerMessage(event, context, api.config)) return;
          if (statusArgv) {
            const status = await runScript(statusArgv, { ...process.env, ...(api.config?.env?.vars ?? {}) }, DEFAULT_TIMEOUT_MS);
            log[status.ok ? "info" : "warn"]?.(
              `[callback-dispatch] status question -> today_status.py ` +
              (status.ok ? "ok" : "FAILED: " + status.out.slice(0, 200)),
            );
            if (!status.ok || !status.out) {
              const why = status.timedOut ? "timed out" : status.out || "no output";
              return { handled: true, text: withHat(`⚠️ I couldn't read today's logs — ${why}`) };
            }
            return { handled: true, text: status.out };
          }
          const mealEnv = { ...process.env, ...(api.config?.env?.vars ?? {}) };
          const logged = await runScript(mealArgv, mealEnv, DEFAULT_TIMEOUT_MS);
          log[logged.ok ? "info" : "warn"]?.(
            `[callback-dispatch] meal reply -> confirm_meal.py ${mealArgv.slice(1, 5).join(" ")} ` +
            (logged.ok ? "ok" : "FAILED: " + logged.out.slice(0, 200)),
          );
          if (!logged.ok) {
            const why = logged.timedOut ? "timed out" : logged.out || "failed";
            return { handled: true, text: withHat(`⚠️ That meal was NOT logged — ${why}`) };
          }
          if (!logged.out || logged.out === "NO_REPLY") return { handled: true };
          return { handled: true, text: withHat(logged.out) };
        }
        if (!isOwnerMessage(event, context, api.config)) {
          return { handled: true, text: "Approvals are available only in the owner's private Telegram conversation." };
        }

        const entry = resolveEntry(token);
        const env = { ...process.env, ...(api.config?.env?.vars ?? {}) };

        if (!entry) {
          // Mirror RULES.md: unknown confirm:/skip: is never conversation.
          log.warn?.(`[callback-dispatch] unknown callback ${token}`);
          return {
            handled: true,
            text: "No matching action found. Nothing was changed. 🎩",
          };
        }

        if (entry.unlink) {
          await unlink(path.join(OPENCLAW_DIR, entry.unlink)).catch(() => {});
        }

        let script = null;
        if (Array.isArray(entry.argv) && entry.argv.length > 0) {
          script = await runScript(entry.argv, env, entry.timeoutMs);
          log[script.ok ? "info" : "warn"]?.(
            `[callback-dispatch] ${token} -> ${entry.argv[0]} ${script.ok ? "ok" : "FAILED: " + script.out.slice(0, 200)}`,
          );
        }

        const literalReply =
          typeof entry.reply === "string" && entry.reply !== "script" && entry.reply !== "report";

        if (script && !script.ok) {
          const why = script.timedOut ? "timed out" : script.out || "failed";
          return { handled: true, text: withHat(`⚠️ ${token} — ${why}`) };
        }

        if (literalReply) return { handled: true, text: entry.reply };

        if (entry.reply === "script") {
          // NO_REPLY family: the script edits its own Telegram message on success.
          if (!script || !script.out || script.out === "NO_REPLY") return { handled: true };
          // Anything else means the script could NOT edit the tapped message and fell
          // back to text — so his prompt keeps its stuck buttons and this lands as a
          // SECOND message underneath. That is the thing the user keeps reporting
          // (2026-09-13, 2026-09-26), and until now it left no trace anywhere: the
          // edit failure is swallowed inside send_telegram.edit_message_text, and a
          // successful run and a fallback are both logged "ok". Name it in the log so
          // the next report has a cause attached instead of a guess.
          log.warn?.(
            `[callback-dispatch] ${token} -> ${entry.argv?.[0] ?? "?"} FELL BACK to a second ` +
            `message (no source_tg_msg_id, or Telegram refused the edit — messages over ` +
            `48h old cannot be edited): ${script.out.slice(0, 120)}`,
          );
          return { handled: true, text: withHat(script.out) };
        }

        // "report"
        return { handled: true, text: withHat(script?.out) };
      },
      // Ceiling for the whole hook. Must exceed the largest per-entry
      // timeoutMs in dispatch-table.json (playbypoint's browser booking =
      // 300000, 2026-09-15: raised from 180000 after a clean, successful
      // run measured at 230s — the flow's real wall-clock time, driven by
      // many sequential bounded browser RPCs plus the Stripe-popup refocus
      // check on every one, routinely exceeds 180s even with nothing wrong)
      // or a slow script is killed mid-run with no reply delivered.
      { priority: 100, timeoutMs: 330000 },
    );

    log.info?.("[callback-dispatch] before_dispatch handler registered");
  },
};
