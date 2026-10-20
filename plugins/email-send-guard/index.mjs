import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";
export const BUILD_STAMP = pluginBuildStamp(import.meta.url);

/*
 * Deterministic email/calendar approval routing before the agent turn.
 * Only an owner checkmark replying to a current proposal is executable.
 * Buttons and replies resolve an expiring, exact-payload approval ID; bare
 * unbound checkmarks never choose the latest pending email.
 * Also blocks ad-hoc raw gog gmail send/reply/autoreply commands.
 */

import { execFile } from "node:child_process";
import { statSync } from "node:fs";
import { promisify } from "node:util";
import { isOwnerMessage } from "../callback-dispatch/owner.mjs";

const execFileP = promisify(execFile);
const ROOT = "~/.openclaw";
const SEND_PENDING = `${ROOT}/workspace/Skills/email-monitor/scripts/send_pending.py`;
const TIMEOUT_MS = 30000;

const GOG_GMAIL_SEND_RE = /\bgog\s+gmail\s+(send|reply|autoreply)\b/i;

function withHat(text) {
  const s = String(text ?? "").trim();
  return /🎩\s*$/.test(s) ? s : `${s} 🎩`;
}

// --- Pure, testable decision logic (see test.mjs) ---------------------------

/** True only for an inbound message that IS "✅" and nothing else. */
export function isBareCheckmark(raw) {
  const s = String(raw ?? "").replace(/️/g, "").trim(); // strip variation selector
  return s === "✅";
}

/** send_pending.py's stdout -> the Telegram reply text, per RULES.md's two cases. */
export function formatSendReply(out) {
  const s = String(out ?? "").trim();
  if (s.startsWith("SENT:")) {
    const to = s.split("→").pop()?.trim() || "recipient";
    return withHat(`✅ Sent to ${to}.`);
  }
  if (s.startsWith("NO_PENDING")) return "No pending draft to send.";
  return withHat(`⚠️ Couldn't send — ${s.slice(0, 300) || "unknown error"}. Not confirmed sent, please check/retry.`);
}

/** Ad-hoc (non-job) gog gmail send/reply/autoreply -> {block, verb, blockReason} or null. */
export function inspectGmailSend(rawCmd, ctx) {
  const cmd = String(rawCmd ?? "").trim();
  if (!cmd) return null;
  const m = GOG_GMAIL_SEND_RE.exec(cmd);
  if (!m) return null;
  if (/--help\b|\s-h\b/.test(cmd)) return null;
  if ((ctx ?? {}).jobId) return null; // vetted cron/job scripts, matches calendar-write-guard's scope

  const verb = m[1].toLowerCase();
  const blockReason =
    `\`gog gmail ${verb}\` is BANNED (Documentation/rules/17-email-gog-owner-gmail-com.md: "Never use raw gog send/reply/autoreply or infer approval from recency") — ` +
    "it bypasses threading headers, the signature, and the confirmation gate, and has previously fumbled " +
    "quoting/newlines then been falsely reported as sent. Run this instead — it drafts, queues and " +
    "sends the user the ✅/❌; his tap is what sends:\n" +
    "  python3 Skills/email-monitor/scripts/propose_email.py --to <addr> --subject <s> --body <text> " +
    "[--thread-id <id>] [--flow note|followup|reengage]\n" +
    "Replying to mail the daemon already drafted is the other path: send_pending.py --approval-id <id> " +
    "(never --latest). 2026-10-13: this message used to describe BUILDING the gmail_pending entry by " +
    "hand — the agent spent 23 turns reverse-engineering that recipe for one 'running late' note, " +
    "$1.01 of context. A guard must hand back a command to RUN, not a procedure to assemble.";
  return { block: true, verb, blockReason };
}

/** Which proposal files are actually waiting — so a bare ✅ can say which case it is. */
export function pendingProposals(root = ROOT) {
  return ["pending_cal_event.json", "pending_cal_queue.json"].filter((name) => {
    try {
      return statSync(`${root}/${name}`).size > 0;
    } catch {
      return false;
    }
  });
}

export async function dispatchCheckmark(event, context, config, run = execFileP, root = ROOT) {
  if (!isOwnerMessage(event, context, config)) {
    return { handled: true, text: "Approvals are available only in the owner's private Telegram conversation." };
  }
  const replyTo = event.replyToId ?? context.replyToId;
  if (!replyTo || event.replyToIsQuote) {
    // Tell him which of the two it is. 2026-09-20: Archibald typed its own "✅ to confirm"
    // under a plain chat message (no proposal registered, no button), the user sent a bare ✅,
    // and this line told him to tap a button that did not exist anywhere.
    return { handled: true, text: pendingProposals(root).length
      ? "Reply with ✅ to the specific proposal, or tap its approval button, Master Wayne. 🎩"
      : "Nothing is waiting for approval, Master Wayne — that ✅ has nothing to act on. If Archibald "
        + "showed you a draft, it never registered it as a proposal; ask it to send the proposal again. 🎩" };
  }
  try {
    const env = { ...process.env, ...(config.env?.vars ?? {}) };
    const options = { env, timeout: TIMEOUT_MS, maxBuffer: 1024 * 1024 };
    const resolved = await run("python3", [`${ROOT}/workspace/Skills/shared/approval.py`, "--message-id", String(replyTo)], options);
    const proposal = JSON.parse(resolved.stdout);
    if (!proposal || !["email", "cal"].includes(proposal.kind)) {
      return { handled: true, text: "That message has no current approval. Ask me to show the proposal again, Master Wayne. 🎩" };
    }
    const script = proposal.kind === "email" ? SEND_PENDING : `${ROOT}/workspace/Skills/email-monitor/scripts/confirm_cal_event.py`;
    const args = ["-W", "ignore", script, "--approval-id", proposal.id];
    if (proposal.kind === "email") args.push("--from-callback");
    const { stdout } = await run("python3", args, options);
    const text = String(stdout ?? "").trim();
    return text === "NO_REPLY" ? { handled: true } : { handled: true, text: withHat(text) };
  } catch (err) {
    return { handled: true, text: "The approval could not be completed. Its result must be checked before retrying, Master Wayne. 🎩" };
  }
}

export default {
  id: "email-send-guard",
  name: "Email Send Guard",
  description:
    "Resolves checkmark replies to a specific proposal and blocks ad-hoc Gmail sends.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    logPluginBuildStamp(api, "email-send-guard", BUILD_STAMP);

    api.on(
      "before_dispatch",
      async (event, context = {}) => {
        const raw = event?.content ?? event?.body ?? "";
        if (!isBareCheckmark(raw)) return; // not a bare, alone ✅ -- hand back to the agent

        return dispatchCheckmark(event, context, api.config ?? {});
      },
      { priority: 100, timeoutMs: TIMEOUT_MS + 10000 },
    );

    api.on(
      "before_tool_call",
      async (event) => {
        if (event?.toolName !== "exec") return;
        const command = event?.params?.command;
        if (typeof command !== "string") return;

        const decision = inspectGmailSend(command, event.context);
        if (!decision) return;

        log.info?.(`[email-send-guard] blocked ad-hoc gog gmail ${decision.verb}\n  cmd: ${command}`);
        return { block: true, blockReason: decision.blockReason };
      },
      { priority: 60 },
    );

    log.info?.("[email-send-guard] before_dispatch + before_tool_call handlers registered");
  },
};
