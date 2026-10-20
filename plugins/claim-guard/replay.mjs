/**
 * Offline replay of claim-guard over recorded turns. Read-only; used by
 * workspace/Skills/shared/accuracy_check.py.
 *
 *   node replay.mjs turns.json   ->  {"turns": N, "unbacked": [{ts, user, reply, kind, phrase}]}
 *
 * turns.json: [{ts, user, final, messages}] where `messages` is the turn in native
 * session shape (the user message first). Uses the plugin's own decision function, so
 * "unbacked" here means exactly what the live guard would have sent back for another pass.
 *
 * WHY THE DECISION IS THE SOURCE, NOT findClaim (2026-10-07): inspectFinalReply flags a
 * turn down two separate paths. The finance path (financeClaimDecision) returns before
 * findClaim is ever consulted, so a finance assertion is flagged while findClaim(text)
 * returns null for the same reply. This file used to call findClaim again and read
 * .kind off it, which threw "Cannot read properties of null" and took the whole weekly
 * accuracy check down with it — the one job whose report names the OTHER failing jobs.
 * It had been silently dead since 2026-10-04, hiding two broken crons.
 *
 * So the decision object is what gets reported, and findClaim only adds the offending
 * phrase when it happens to have one.
 */
import { readFileSync } from "node:fs";
import { inspectFinalReply, findClaim } from "./index.mjs";

/** The claim type, named the way the live guard names it when it retries. */
function kindOf(decision, claim) {
  if (claim?.kind) return claim.kind;
  const key = decision?.retry?.idempotencyKey ?? "";
  // "claim-guard:save" -> "save"; "finance-claim" -> "finance-claim".
  return key.includes(":") ? key.slice(key.indexOf(":") + 1) : (key || "unknown");
}

/** What was objected to: the offending sentence if known, else the guard's reason. */
function phraseOf(decision, claim) {
  return claim?.phrase ?? decision?.reason ?? "(no phrase recorded)";
}

const turns = JSON.parse(readFileSync(process.argv[2], "utf8"));
const unbacked = [];
for (const t of turns) {
  const decision = inspectFinalReply({ lastAssistantMessage: t.final, messages: t.messages });
  if (!decision) continue;
  const claim = findClaim(t.final);
  unbacked.push({
    ts: t.ts,
    user: String(t.user).slice(0, 120),
    reply: String(t.final).slice(0, 200),
    kind: kindOf(decision, claim),
    phrase: String(phraseOf(decision, claim)).slice(0, 80),
  });
}
console.log(JSON.stringify({ turns: turns.length, unbacked }));
