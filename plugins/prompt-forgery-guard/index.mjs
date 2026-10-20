import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
export const BUILD_STAMP = pluginBuildStamp(import.meta.url);

/*
 * prompt-forgery-guard — the agent may not TYPE a message that only a script may send.
 *
 * WHY THIS EXISTS (2026-09-26)
 * The user reported getting this three times:
 *
 *   💳 Card A (••0000)
 *   $45.00 · RIDESHARE* PENDING EXAMPLE CITY
 *   2026-09-26 01:28 PM ET
 *   → Dining — what was the occasion?
 *
 * No script sent any of them. The charge was never categorized as Dining — the
 * ledger said Groceries, `.merchant_answers.json` held exactly one answer
 * (Groceries, 15:23:32), finance_txn_watch.py reported context_prompts: 0 on
 * every run that afternoon, and resolve_context() cannot emit a new message at
 * all (it edits the existing one, and only for a social category). What actually
 * happened: the agent had just read the rule file that documents this template,
 * then reproduced it from memory with a plausible-looking category filled in.
 *
 * That is worse than a wrong answer. A forged prompt is indistinguishable from a
 * real one, so it is trusted — and it invites a tap or a reply that answers a
 * question nothing asked, about data that is wrong.
 *
 * This is the OUTBOUND twin of the callback-dispatch plugin. That one stopped the
 * model improvising on inbound button taps by making them deterministic; this one
 * keeps script-owned message templates deterministic on the way out. Neither is a
 * watchdog over a flaky process: both draw a boundary the model cannot cross.
 *
 * It returns {action: "revise"} rather than blocking, so a reply that mixes real
 * content with a forged prompt is rewritten instead of lost — and a false positive
 * costs one extra model pass, not a dropped message.
 */

// The allowlist is the small set of shapes normal prose may not imitate. The
// vocabulary is seeded from the actual finance template sources at module load;
// the test also renders those templates, so a source rewording cannot quietly
// orphan the guard again.
const REQUIRED_GLYPHS=['✅','✔️','☑️','🧾','💳','💰','📎'];
const REQUIRED_VERBS=['Logged','Recorded','Saved','Filed','Categorized'];
const HERE=path.dirname(fileURLToPath(import.meta.url));
const TEMPLATE_DIR=path.resolve(HERE,'../../workspace/Skills/afrexai-personal-finance/scripts');
const TEMPLATE_FILES=['purchase_notice.py','finance_workflow.py','finance_close.py',
  'finance_reply.py','finance_transfer_categorize.py','finance_p2p_categorize.py'];

export function deriveTemplateTokens(values){
  const text=(values??[]).join('\n');
  const glyphs=REQUIRED_GLYPHS.filter(glyph=>text.includes(glyph));
  const verbs=REQUIRED_VERBS.filter(verb=>new RegExp(`\\b${verb}\\b`,'i').test(text));
  return {glyphs,verbs};
}

let sourceTokens={glyphs:[],verbs:[]};
try{ sourceTokens=deriveTemplateTokens(TEMPLATE_FILES.map(file=>readFileSync(path.join(TEMPLATE_DIR,file),'utf8'))); }catch{}
export const OWNED_GLYPHS=[...new Set([...REQUIRED_GLYPHS,...sourceTokens.glyphs])];
export const OWNED_VERBS=[...new Set([...REQUIRED_VERBS,...sourceTokens.verbs])];

const escaped=value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const glyphAlternation=OWNED_GLYPHS.map(escaped).join('|');
const verbAlternation=OWNED_VERBS.map(escaped).join('|');
const MONEY='\\$[\\d,]+(?:\\.\\d{1,2})?';
const LEADING_GLYPH_AMOUNT=new RegExp(`^(?:\\s|[*_~])*(?:${glyphAlternation})(?:[*_~]|\\s)*(?:(?:${verbAlternation})\\s+)?${MONEY}\\b`,'imu');
const VERB_WITH_AMOUNT=new RegExp(`\\b(?:${verbAlternation})\\b[\\s\\S]{0,120}${MONEY}|${MONEY}[\\s\\S]{0,120}\\b(?:${verbAlternation})\\b`,'iu');
const CARD_HEADER=new RegExp(`^(?:\\s|[*_~])*(?:💳|🧾)(?:[*_~]|\\s)*[^\\n]*`,'imu');
const VERDICT_OR_QUESTION=/(?:^|\n)\s*(?:→|[^\n]{0,100}\?|(?:reply|tap|press|choose|select)\b[^\n]{0,100}(?:confirm|skip|✅))(?:[^\n]*)/imu;
const AMOUNT_QUESTION=new RegExp(`${MONEY}[^\\n]{0,140}\\?[^\\n]{0,80}\\b(?:tap|reply|choose|select)\\b|\\b(?:which|what)\\b[^\\n]{0,100}\\?[^\\n]{0,80}${MONEY}`,'iu');

/**
 * A forgery needs the script's LAYOUT, not merely its words: the 💳 header plus
 * either an owned question or a "→ verdict" line. Requiring both is what lets the
 * agent still talk ABOUT these prompts — "you got an occasion prompt for that
 * charge" carries no 💳 header and passes untouched.
 */
export function findForgery(reply) {
  const text = String(reply ?? "");
  if (!text) return null;
  if(LEADING_GLYPH_AMOUNT.test(text)) return {phrase:'status-glyph purchase line',owner:'the finance transaction scripts'};
  if(VERB_WITH_AMOUNT.test(text)) return {phrase:'owned status verb with amount',owner:'the finance transaction scripts'};
  if(AMOUNT_QUESTION.test(text)) return {phrase:'amount plus owned question shape',owner:'the finance transaction scripts'};
  const header=CARD_HEADER.exec(text);
  if(header){
    const after=text.slice((header.index??0)+header[0].length);
    if(VERDICT_OR_QUESTION.test(after) || /(?:→|\?)/u.test(header[0]))
      return {phrase:'card header followed by verdict or question',owner:'the finance transaction scripts'};
  }
  return null;
}

function exactToolEvidence(event,text){
  return (event?.messages??[]).some(message=>message?.role==='toolResult' && !message?.isError &&
    (message.content??[]).some(block=>block?.type==='text' && String(block.text??'').includes(text)));
}

function retryInstruction(forgery) {
  return (
    `Your reply reproduces a message template that only a script may send ("${forgery.phrase}", ` +
    `owned by ${forgery.owner}). Those prompts are sent by the script that owns the data, with the ` +
    `real ledger values and working buttons; a typed copy is a forgery — it looks identical to Master ` +
    `Wayne, carries whatever category you guessed, and invites him to answer a question nothing asked. ` +
    `Rewrite it now: say what you actually know in your own words, or run the script that owns the ` +
    `prompt and let it send its own message. Never hand-write a 💳 card prompt, a "→ Category" verdict ` +
    `line, or an occasion/receipt question.`
  );
}

/** The whole decision, pure: plain event in, hook result (or undefined) out. */
export function inspectFinalReply(event) {
  const text=String(event?.lastAssistantMessage??'');
  const forgery = findForgery(text);
  if (!forgery) return undefined;
  if(exactToolEvidence(event,text)) return undefined;
  return {
    action: "revise",
    reason: `prompt-forgery-guard: reply imitates a script-owned prompt (${forgery.phrase})`,
    retry: {
      instruction: retryInstruction(forgery),
      idempotencyKey: "prompt-forgery-guard:card-prompt",
      maxAttempts: 2,
    },
  };
}

export default {
  id: "prompt-forgery-guard",
  name: "Prompt Forgery Guard",
  description:
    "before_agent_finalize: a reply that hand-writes a script-owned Telegram prompt — a 💳 card " +
    "header with a → verdict line, or an occasion/receipt question — is sent back for one more " +
    "pass. Built after 2026-09-26, when the agent typed three fake '→ Dining — what was the " +
    "occasion?' prompts for a charge the ledger had recorded as Groceries.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    logPluginBuildStamp(api, "prompt-forgery-guard", BUILD_STAMP);

    api.on(
      "before_agent_finalize",
      async (event) => {
        let decision;
        try {
          decision = inspectFinalReply(event);
        } catch (err) {
          log.warn?.(`[prompt-forgery-guard] inspect error: ${err?.message ?? err}`);
          return;
        }
        if (!decision) return;
        log.warn?.(`[prompt-forgery-guard] revise: ${decision.reason}`);
        return decision;
      },
      // After claim-guard (60): an unsupported claim is the broader fault, and if it
      // already sent the turn back this never needs to fire.
      { priority: 65 },
    );

    log.info?.("[prompt-forgery-guard] before_agent_finalize handler registered");
  },
};
