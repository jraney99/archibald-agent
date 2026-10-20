import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { findForgery, inspectFinalReply, deriveTemplateTokens, OWNED_GLYPHS, OWNED_VERBS } from "./index.mjs";
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";

const verdict = (reply) => inspectFinalReply({ lastAssistantMessage: reply });
const FORGED = [
  "💳 Card A (••0000)\n$45.00 · RIDESHARE* PENDING EXAMPLE CITY\n2026-09-26 01:28 PM ET\n→ Dining — what was the occasion?",
  "💳 Card A (••0000)\n$45.00 · RIDESHARE* PENDING\n→ Groceries",
  "💳 $45.00 · RIDESHARE* PENDING\n📷 Send the receipt photo when you have it — I'll split it.",
  "💳 Card A (••0000)\n$45.00 · Uber — service shows only at settlement\nWhich Uber was this?",
  "💳 Card A (••0000)\n$49.90 · UNIQLO\nWhat was this for? Tap one — or reply with the receipt photo.",
  "💳 Card B (••1111)\n$88.00 · SOME MERCHANT\n$75+ — what did you pick up? A quick description or receipt photo works.",
  "💳 Card B (••1111)\n$42.10 · CHILI'S\nReply ✅ to confirm Dining",
  "💳 Card B (••1111)\n$42.10 · CHILI'S\nTap ✅ to confirm",
  "✅ Logged $42.10 · CHILI'S · Dining · Card B (••1111)",
  "🧾 Card B\n$42.10 · CHILI'S\n→ Dining",
  "Logged: 💳 Card B\n$42.10 · CHILI'S\n→ Dining",
  "$42.10 · CHILI'S — what was this for? Tap one:",
  "Which Uber was this? $42.10 pending ride",
  "🧾 Logged $42.10 · CHILI'S · Dining",
  "✔️ Logged $42.10 · CHILI'S · Dining",
  "✅ Recorded $42.10 at CHILI'S as Dining",
  "💳 $42.10 · CHILI'S\nDining — reply 'no' if that's wrong.",
  "**💳** $42.10 CHILI'S → Dining",
  "💳 Card B (••1111) · $42.10 · CHILI'S — Was this for work or personal? Tap one.",
  "Saved $42.10 as Dining.",
  "The $42.10 purchase was Categorized as Dining.",
  "📎 $42.10 · receipt attached",
  "☑️ $42.10 · Card B checked",
];
for (const reply of FORGED) {
  const d = verdict(reply);
  assert.equal(d?.action, "revise", `should revise: ${JSON.stringify(reply.slice(0, 48))}`);
  assert.ok(d.retry?.instruction.length > 80);
  assert.match(d.reason, /prompt-forgery-guard/);
}

const FINE = [
  "You got an occasion prompt for that charge because Dining is a social category.",
  "I reviewed the Uber order and would use Groceries if the receipt supports it.",
  "what was the occasion? — that question comes from txn_context.py, not from me.",
  "💳 Your Card A statement is due on the 22nd, Master Wayne.",
  "💳 Card A is the right card for warehouse runs — it earns 2% there.",
  "Groceries → Household is the split I'd suggest.",
  "Done. Bedroom lights off. 🎩", "", null, undefined,
];
for (const reply of FINE) assert.equal(verdict(reply), undefined, `should pass: ${String(reply)}`);
assert.equal(inspectFinalReply(undefined), undefined);
assert.equal(inspectFinalReply({}), undefined);
assert.equal(findForgery(12345), null);

// Render the owning Python templates during the test. A wording change cannot
// silently leave a stale JavaScript phrase list behind.
const ROOT = new URL("../../", import.meta.url).pathname;
const scripts=ROOT+'workspace/Skills/afrexai-personal-finance/scripts';
const python=[
  'import json,sys',
  "sys.path.insert(0,'.'); import finance_test_env",
  'import purchase_notice,finance_workflow',
  "sys.path.insert(0,'../../shared'); import txn_context",
  `entry={'id':'fixture','amount':42.10,'merchant':"CHILI'S",'card':'Card B','card_last4':'1111','category':'Dining','txn_date':'2026-10-03','txn_time_et':'12:00 PM ET'}`,
  "print(json.dumps([purchase_notice.line(entry),finance_workflow.render_card_purchase_question(entry,card='Card B (••1111)',when='2026-10-03',kind='category'),finance_workflow.render_card_purchase_question(entry,card='Card B (••1111)',when='2026-10-03',kind='receipt'),finance_workflow.render_card_purchase_question(entry,card='Card B (••1111)',when='2026-10-03',kind='uber'),txn_context.context_question_text(entry)]))",
].join(';');
const generated=JSON.parse(execFileSync('python3',['-c',python],{cwd:scripts,encoding:'utf8',env:{...process.env,TELEGRAM_SEND_DISABLED:'1'}}));
for(const reply of generated) assert.ok(findForgery(reply),`production template escaped guard: ${reply}`);
const derived=deriveTemplateTokens(generated);
for(const glyph of derived.glyphs) assert.ok(OWNED_GLYPHS.includes(glyph),`missing generated glyph ${glyph}`);
for(const verb of derived.verbs) assert.ok(OWNED_VERBS.includes(verb),`missing generated verb ${verb}`);
for(const glyph of ['✅','✔️','☑️','🧾','💳','💰','📎']) assert.ok(OWNED_GLYPHS.includes(glyph));
for(const verb of ['Logged','Recorded','Saved','Filed','Categorized']) assert.ok(OWNED_VERBS.includes(verb));

const relayed=generated[0];
assert.equal(inspectFinalReply({lastAssistantMessage:relayed,messages:[{role:'toolResult',toolCallId:'x',content:[{type:'text',text:relayed}]}]}),undefined);
assert.equal(verdict(relayed)?.action,'revise');

console.log(`prompt-forgery-guard: ${FORGED.length} forgeries revised, ${FINE.length} legitimate replies passed, ${generated.length} live templates rendered`);
await runRuntimeFixtures("prompt-forgery-guard");
