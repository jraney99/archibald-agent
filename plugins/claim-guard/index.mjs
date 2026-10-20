import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";
export const BUILD_STAMP = pluginBuildStamp(import.meta.url);

/**
 * claim-guard — a reply may not claim a save, a schedule, or a logged count
 * unless something that could have done it actually ran in that turn.
 *
 * Why (2026-09-20 accuracy audit, 366 owner turns): every wrong number and every
 * lost fact had the same shape — the model answered from its own head. "Logged
 * dinner… 5/5 windows" with no tool call (09-16); "2 of 5 meals (Earlier meal
 * logged)" two seconds after the question, no tool call (09-19); "Logged" for a
 * cash lunch that is in no file (09-01); "measurements are recorded"
 * (08-28) and "I'll keep that in mind" (08-23) with nothing written anywhere.
 * Counts that came from a script were right 10/10; counts from memory 0/4.
 * Rules telling the model to run the script already existed and were not read,
 * so this is enforced in code, one tier up (see ../GUARD-PATTERN.md).
 *
 * Hook: before_agent_finalize (embedded agent, verified in the installed
 * 2026.6.10 runtime: selection-*.js builds the event, embedded-agent-*.js honors
 * `revise` with one more model pass, hard cap 3). The event carries the final
 * visible reply and the whole session's messages in native transcript shape.
 *
 * Deliberately narrow: it fires only when the reply matches an explicit claim
 * pattern AND the turn contains zero successful state-capable tool calls. It
 * does not try to judge whether a tool that did run covered the whole claim —
 * that needs judgment, and a guard earns its keep on the exact-match case.
 * Button taps and nudge replies never reach it: callback-dispatch answers those
 * before the agent turn exists.
 */

/** Tools that cannot change anything. A turn with only these cannot back a claim. */
import { financeClaimDecision } from './finance-claims.mjs';
import { receiptClaimDecision } from './receipt-claims.mjs';
import { stateClaimDecision } from './state-claims.mjs';
export const READ_ONLY_TOOLS = new Set([
  "read", "get_goal", "memory_search", "memory_get", "web_search", "web_fetch",
  "image", "sessions_list", "sessions_history",
]);

const SAVE_VERBS =
  "logged|recorded|noted|saved|updated|added|filed|marked|scheduled|booked|cancell?ed|moved|removed|deleted|sent|done|locked in";

// A sentence that OPENS with a completed-save verb: "Logged: pizza $12.00", "Done. Bedroom lights off."
const LEAD = new RegExp(`^(?:understood and |got it[,.]? ?(?:and )?)?(${SAVE_VERBS})\\b`, "i");
// A sentence that opens with a short noun phrase and then the save verb: "Receipt logged.",
// "Purchase recorded — $123.45", "Split saved". 2026-09-22: "✅ Receipt logged. **Store #0000,
// $123.45** … Locked in." went out with zero tools run, and LEAD only matched a verb in first place.
// The first word must be a letter (a digit opens a data row) and the sentence must not negate.
const NOUN_VERBS = "logged|recorded|saved|filed|scheduled|booked|updated|added(?! up)|sent|cancell?ed|deleted|removed|locked in";
const NOUN_LEAD = new RegExp(`^\\p{L}[\\p{L}'’&.-]*(?:\\s+[\\p{L}'’&.-]+){0,2}\\s+(${NOUN_VERBS})\\b`, "iu");
const NEGATED = /\b(?:not|n't|nothing|never|no|none|isn't|wasn't|haven't|hasn't)\b/i;
// "I've logged…", "I have now set…"
const PERFECT = new RegExp(
  `\\bI(?:'ve|’ve| have)(?: just| already| now)? (${SAVE_VERBS}|set|created|changed)\\b`, "i");
// "Your measurements are recorded", "Alarm is now set to 9:00"
const PASSIVE =
  /\b(?:is|are|has been|have been)(?: now| already)? (logged|recorded|saved|noted|scheduled|booked|filed|updated|set to)\b/i;
// A standing promise that needs a job or a file behind it.
const PROMISE =
  /\bI(?:'ll|’ll| will) (remind|flag|ping|nudge|reroute|follow up|check back|circle back|remember|reference|keep (?:that|this|it|those|these) in mind|use (?:this|these|that))\b/i;
// A promise that USER'S OWN ACTION will make Archibald act again. Nothing in the
// runtime does that. An approval tap is answered by callback-dispatch, which runs a
// deterministic script (confirm_cal_event.py and friends) and returns its text — it
// never starts a new agent turn. So "approve this one and I'll send the next" is a
// loop with no mechanism, and the work after the first step is simply lost.
//
// 2026-10-09: asked for three golf-trip events, the reply was "One proposal at a
// time — the system serializes them... Tap Approve on the first one and I'll fire
// the next." The serialization was real (pending_cal_event.json is a single slot).
// The follow-through was not. The user approved the trip block; Rounds 1 and 2 were
// never proposed and the slot sat empty. The old PROMISE pattern missed it because
// it only listed remind/flag/ping/nudge/follow up/remember-shaped verbs.
const HANDOFF_AFTER_HIS_ACTION =
  /\b(?:once|after|when|as soon as)\b[^.?!]{0,70}\byou\b[^.?!]{0,30}\b(?:approve|tap|confirm|accept|say yes|reply)\w*\b[^.?!]{0,70}\bI(?:'ll|’ll| will)\b/i;
const HANDOFF_THE_REST =
  /\bI(?:'ll|’ll| will)\s+(?:then\s+)?(?:send|fire|queue|add|create|propose|do|start|trigger|kick off|follow with)\b[^.?!]{0,50}\b(?:next|second|third|rest|remaining|others|sequentially|one at a time|in order)\b/i;

// A logged count: "2 of 5 meals", "4/5 windows".
const COUNT =
  /\b(?:\d|zero|one|two|three|four|five|six)\s*(?:of|\/|out of)\s*\d\s*(?:core |eating |required )?(?:meals?|windows?)\b/i;

/** Messages of the current turn: everything after the last user message. */
export function turnSlice(messages) {
  const list = Array.isArray(messages) ? messages : [];
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i]?.role === "user") return list.slice(i + 1);
  }
  return list;
}

/** Tool calls in the turn that could change state AND came back without an error. */
export function stateChangingCalls(turn) {
  const failed = new Set();
  const answered = new Set();
  for (const m of turn) {
    if (m?.role !== "toolResult") continue;
    answered.add(m.toolCallId);
    if (m.isError === true) failed.add(m.toolCallId);
  }
  const calls = [];
  for (const m of turn) {
    if (m?.role !== "assistant" || !Array.isArray(m.content)) continue;
    for (const block of m.content) {
      if (block?.type !== "toolCall" || READ_ONLY_TOOLS.has(block.name)) continue;
      if (answered.has(block.id) && !failed.has(block.id)) calls.push(block.name);
    }
  }
  return calls;
}

/** First explicit claim in the reply, or null. Returns {kind, phrase}. */
export function findClaim(text) {
  const reply = String(text ?? "");
  // Sentence/line starts, with list markers, markdown and emoji stripped — but never digits
  // or quotes: "- 2026-09-16: 1 scheduled session done" is a data row, and a line opening
  // with a quotation mark is the model quoting an old message, not claiming a new save.
  let cursor = 0;
  for (const part of reply.split(/(?<=[.!?])\s+|\n+/)) {
    const at = reply.indexOf(part, cursor);
    cursor = at + part.length;
    // An odd number of quote marks before this sentence means it sits inside a quotation.
    if ((reply.slice(0, at).match(/["“”]/g) ?? []).length % 2 === 1) continue;
    const opening = part.replace(/^(?:[\s*_>#\-•~]|\p{Extended_Pictographic}|️)+/u, "");
    if (opening.match(LEAD)) return { kind: "save", phrase: part.trim().slice(0, 80) };
    if (opening.match(NOUN_LEAD) && !NEGATED.test(opening.split(/[.!?]/)[0]))
      return { kind: "save", phrase: part.trim().slice(0, 80) };
  }
  for (const [kind, pattern] of [["save", PERFECT], ["save", PASSIVE], ["count", COUNT]]) {
    const hit = reply.match(pattern);
    if (hit) return { kind, phrase: hit[0] };
  }
  // Checked before PROMISE and WITHOUT the question-mark exemption: the defect is
  // architectural, not a matter of whether Archibald is waiting on an answer.
  for (const pattern of [HANDOFF_AFTER_HIS_ACTION, HANDOFF_THE_REST]) {
    const hit = reply.match(pattern);
    if (hit) return { kind: "handoff", phrase: hit[0].trim().slice(0, 100) };
  }
  // A promise that ends in a question is waiting on the user ("I'll remind you — what time?").
  const promise = reply.match(PROMISE);
  if (promise && !reply.includes("?")) return { kind: "promise", phrase: promise[0] };
  return null;
}

export function retryInstruction(claim) {
  const what = {
    save: "says something was saved, logged, changed or scheduled",
    count: "states a logged count",
    promise: "promises a future reminder or to remember something",
    handoff: "promises to act again after Master Wayne approves or taps something",
  }[claim.kind];
  if (claim.kind === "handoff") {
    return (
      `Your reply ${what} ("${claim.phrase}"), and nothing in the runtime can do that. ` +
      `An approval tap is answered by callback-dispatch, which runs the confirm script and ` +
      `returns its output; it does NOT start a new agent turn, so no later step of yours will ` +
      `ever run. Fix it now: either do every step in THIS turn, or put the whole batch in one ` +
      `proposal (pending_cal_event.json's \`gog_events\` is a list — one approval can create ` +
      `several events), or tell Master Wayne plainly which parts are NOT done and that he must ` +
      `ask again for them. Never tell him you will continue after he taps.`
    );
  }
  return (
    `Your reply ${what} ("${claim.phrase}"), but no tool that could have done that completed ` +
    `successfully in this turn. Fix it now, one of two ways. (1) Run the script that owns it — ` +
    `ROUTING.md names it; for anything Master Wayne says he ate, took, drank or did today that is ` +
    `Skills/dr-physician/scripts/log_day.py — and reply with exactly what the script printed. ` +
    `(2) If no script or file exists for it, say plainly that you have NOT saved it. ` +
    `Never state a logged count or total unless a script printed it in this turn.`
  );
}

/** The whole decision, pure: plain event in, hook result (or undefined) out. */
export function inspectFinalReply(event) {
  const receipt = receiptClaimDecision(event, turnSlice(event?.messages));
  if (receipt) return receipt;
  const finance = financeClaimDecision(event, turnSlice(event?.messages));
  if (finance?.allow) return undefined;
  if (finance) return finance;
  // How the machine itself is doing — "your cron failed", "the daemon is fine". Needs a
  // successful read of that component this turn. Sits here because findClaim below only
  // ever looked for claims about what the AGENT did, never about what the SYSTEM is.
  const state = stateClaimDecision(event, turnSlice(event?.messages));
  if (state) return state;
  const claim = findClaim(event?.lastAssistantMessage);
  if (!claim) return undefined;
  if (stateChangingCalls(turnSlice(event?.messages)).length > 0) return undefined;
  return {
    action: "revise",
    reason: `claim-guard: ${claim.kind} claim with no state-changing tool call in the turn`,
    retry: {
      instruction: retryInstruction(claim),
      idempotencyKey: `claim-guard:${claim.kind}`,
      maxAttempts: 2,
    },
  };
}

export default {
  id: "claim-guard",
  name: "Claim Guard",
  description:
    "before_agent_finalize: a reply that claims a save, a schedule, a promise to remember, or a logged " +
    "meal count — in a turn where no state-capable tool call succeeded — is sent back for one more pass " +
    "with the instruction to run the owning script or say plainly that nothing was saved.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    logPluginBuildStamp(api, "claim-guard", BUILD_STAMP);

    api.on(
      "before_agent_finalize",
      async (event) => {
        let decision;
        try {
          decision = inspectFinalReply(event);
        } catch (err) {
          log.warn?.(`[claim-guard] inspect error: ${err?.message ?? err}`);
          return;
        }
        if (!decision) return;
        log.warn?.(`[claim-guard] revise: ${decision.reason} — "${findClaim(event.lastAssistantMessage)?.phrase ?? 'finance completion'}"`);
        return decision;
      },
      { priority: 60 },
    );

    log.info?.("[claim-guard] before_agent_finalize handler registered");
  },
};
