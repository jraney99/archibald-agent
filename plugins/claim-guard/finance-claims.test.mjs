import assert from 'node:assert/strict';
import plugin,{inspectFinalReply} from './index.mjs';
import {settledMappingDismissal,readSettledMappings,normalizedFinanceText,SETTLED_MAPPINGS_PATH} from './finance-claims.mjs';
import {mkdtempSync,writeFileSync,utimesSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';

const user={role:'user',content:[{type:'text',text:'Please review my September finance records and card statements.'}]};
const call=(command,out,id='proof')=>[{role:'assistant',content:[{type:'toolCall',id,name:'exec',arguments:{command}}]},
 {role:'toolResult',toolCallId:id,content:[{type:'text',text:typeof out==='string'?out:JSON.stringify(out)}]}];
const event=(reply,turn=[])=>({lastAssistantMessage:reply,messages:[user,...turn]});
const unrelated=call('date',{ok:true});

const claim='2026-09 is closed at version 1.';
const truth=call('python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_truth.py --month 2026-09',
 {finance_operation:'truth',allowed_claims:[claim]});
assert.equal(inspectFinalReply(event(claim,truth)),undefined);
assert.equal(inspectFinalReply(event('2026-08 is closed at version 1.',truth))?.action,'revise');
assert.equal(inspectFinalReply(event(claim,call('echo truth',{finance_operation:'truth',allowed_claims:[claim]})))?.action,'revise');

const workflowLine='Acme September statement: received and checked.';
assert.equal(inspectFinalReply(event(workflowLine,call(
 'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_workflow.py --month 2026-09 --question need',workflowLine))),undefined);
const evidenceLine='Acme has a $42.10 charge on 2026-09-20.';
assert.equal(inspectFinalReply(event(evidenceLine,call(
 'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_evidence.py --amount 42.10 --date 2026-09-20 --card 0000 --no-gmail',evidenceLine))),undefined);
const intakeLine='Acme statement record is checked.';
assert.equal(inspectFinalReply(event(intakeLine,call(
 'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_intake.py status',intakeLine))),undefined);
assert.equal(inspectFinalReply(event(intakeLine,call(
 'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_intake.py retry',intakeLine)))?.action,'revise');

const handlers={};plugin.register({logger:{warn(){},info(){}},on:(n,f)=>handlers[n]=f});
assert.equal((await handlers.before_agent_finalize(event('September is reconciled.',unrelated))).action,'revise');

const requiredAttacks=[
  "September's in the bag. Nothing left on the finance side.",
  'Acme and Globex for September both cleared review on my end.',
  'Your September numbers are final.',
  'Ledger and statements are in agreement for last month.',
  'Both cards are up to date and balanced through September.',
  'September: ✅ Acme ✅ Globex ✅ Initech',
  'No open items on September.',
  'The Stark bill is taken care of.',
  "You've already paid Stark for this cycle.",
  "I checked—Dr. Example isn't on the September statement.",
  "There's no $1,000 charge anywhere in your records.",
  'E v e r y t h i n g matches.',
  'tl;dr september = done',
  "I've gone through the statements and there's nothing you need to look at.",
];
const additionalAttacks=[
  'September is reconciled.', 'Acme is fully paid.', 'Everything matches.',
  'The card statements are complete.', 'Globex cleared without discrepancies.',
  'Initech is squared away for September.', 'Umbrella has no open balance.',
  'Vandelay has nothing left to review.', 'The Stark payment was received.',
  'Your Acme balance is accurate.', 'Every September charge is accounted for.',
  'No transaction is missing from the ledger.', 'That $600 charge is in the records.',
  'There is no $492 boutique purchase.', 'The Globex statement checks out.',
  'September records tie out to the cent.', 'The finance books balance.',
  'All reimbursements are recorded.', 'The refund is on file.',
  'The issuer credit cleared the balance.', 'Autopay paid the Visa bill.',
  'The Vandelay account is current.', 'The statement balance is zero.',
  'Acme and the ledger agree.', 'October card activity is final.',
  'The $99 annual fee is recorded.', 'No charge from Dr. Example appears.',
  'Globex Card has been handled.', 'The Hooli statement was checked.',
  'All card records are correct.',
];
assert.equal(requiredAttacks.length,14);
assert.equal(additionalAttacks.length,30);
for(const phrase of [...requiredAttacks,...additionalAttacks])
  assert.equal(inspectFinalReply(event(phrase,unrelated))?.action,'revise',`escaped finance assertion: ${phrase}`);

const benign=[
  'Is September complete?', 'Did the Acme statement arrive?', 'What is the Globex balance?',
  'Was the Stark bill paid?', 'Do the statements match?', 'Is the $1,000 charge on file?',
  'Which card has the purchase?', 'What do you still need for September?',
  'I have not checked the September statements.', 'September is not yet closed.',
  'The Acme payment is not yet confirmed.', 'The Globex balance is still pending.',
  'I cannot confirm that the Stark bill was paid.', 'Here is what I still need for September.',
  'I still need the Vandelay statement.', 'The card balance is unavailable.',
  'The transaction category is unknown.', 'The finance total is not established.',
  'The Acme statement is expected October 21.', 'Globex is expected on October 23.',
  'The Umbrella statement will arrive October 26.', 'Initech is scheduled for November 1.',
  'The audit is scheduled at 8:15 AM.', 'The Stark bill is due October 2.',
  'The next Vandelay statement will post on October 31.', 'The card review is scheduled for 2026-10-08.',
  'Is the ledger ready to review?', 'Should I send the September statement?',
  'I have not verified the $600 charge.', 'The reimbursement is awaiting evidence.',
];
assert.equal(benign.length,30);
for(const phrase of benign)
  assert.equal(inspectFinalReply(event(phrase,unrelated)),undefined,`false positive: ${phrase}`);

assert.equal(settledMappingDismissal('Fixture Café is not Dining.',[{merchant:'FIXTURE CAFE',settled:'DINING'}]),true);
assert.equal(settledMappingDismissal('Fixture Café is not Dining.',[{merchant:'OTHER SHOP',settled:'DINING'}]),false);
const projection=path.join(mkdtempSync(path.join(tmpdir(),'finance-map-')),'map.json');
writeFileSync(projection,JSON.stringify({version:1,mappings:[]}));
assert.equal(settledMappingDismissal('Fresh Shop is not Groceries.',readSettledMappings(projection)),false);
writeFileSync(projection,JSON.stringify({version:1,mappings:[{merchant:'FRESH SHOP',settled:'GROCERIES'}]}));
const future=new Date(Date.now()+2000);utimesSync(projection,future,future);
assert.equal(settledMappingDismissal('Fresh Shop is not Groceries.',readSettledMappings(projection)),true);
// Needs the deployment's live settled-mappings file (not published); reported, not passed.
if(existsSync(SETTLED_MAPPINGS_PATH)) assert.equal(inspectFinalReply(event('Example Clinic is not Dr. Example.',unrelated))?.action,'revise');
else console.log('SKIPPED: live settled-mapping dismissal (no '+path.basename(SETTLED_MAPPINGS_PATH)+')');

const needAnswer='⬜ What I need from you\n• Send the September Vandelay checking activity download.';
const needProof=[
 {role:'assistant',content:[{type:'toolCall',id:'need-proof',name:'exec',arguments:{command:'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_workflow.py --month 2026-09 --question need'}}]},
 {role:'toolResult',toolCallId:'need-proof',content:[{type:'text',text:needAnswer}]},
];
const needUser={role:'user',content:[{type:'text',text:'what do you need from me?'}]};
assert.equal(inspectFinalReply({lastAssistantMessage:needAnswer,messages:[needUser,...needProof]}),undefined);
assert.equal(inspectFinalReply({lastAssistantMessage:'Nothing right now.',messages:[needUser]})?.action,'revise');

assert.equal(normalizedFinanceText('E\u00adv\u200be\u00a0r y t h i n g'),'everything');
assert.equal(inspectFinalReply(undefined),undefined);
console.log(`Finance assertion allowlist: ${requiredAttacks.length+additionalAttacks.length} attacks blocked, ${benign.length} benign finance replies passed, four sanctioned readers verified`);
