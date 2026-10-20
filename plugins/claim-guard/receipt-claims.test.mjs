import assert from 'node:assert/strict';
import {receiptClaimDecision as decide} from './receipt-claims.mjs';
const command="python3 scripts/finance_txn_split.py --id live:shirts --items-json /tmp/items.json --receipt-card 0000";
const call=cmd=>({role:'assistant',content:[{type:'toolCall',id:'a',name:'exec',arguments:{command:cmd}}]});
const result=(proof,extra={})=>({role:'toolResult',toolCallId:'a',content:[{type:'text',text:proof}],...extra});
const proof='FINANCE_RECEIPT_SAVED '+JSON.stringify({transaction_id:'shirts',receipt_id:'receipt-1',revision:1,read_back:true});
const check=(text,turn=[])=>decide({messages:[{role:'user',content:'Receipt is in my email'}],lastAssistantMessage:text},turn);
let n=0;
for(const [text,turn] of [
 ['Saved the receipt.',[]], ["I'm recording both card numbers.",[]],
 ['Saved the receipt.',[call('python3 unrelated.py'),result(proof)]],
 ['Saved the receipt.',[call(command),result(proof,{isError:true})]],
 ['Saved the receipt.',[call(command),result('Saved receipt-1')]],
 ['Saved the receipt.',[call(command),result(proof.replace('shirts','other'))]],
 ['Saved live:other receipt.',[call(command),result(proof)]],
 ['Saved the receipt.',[call(command),result(proof,{details:{exitCode:1}})]],
 ['Saved the receipt.',[call('echo '+proof),result(proof)]],
]) {assert.equal(check(text,turn)?.action,'revise');n++;}
for(const text of ['Not saved — the writer failed.','Next step: record the receipt.','I will record the receipt after checking the card.']) {assert.equal(check(text),undefined);n++;}
assert.equal(check('Saved receipt live:shirts.',[call(command),result(proof)]),undefined);n++;
assert.equal(check('Saved receipt live:shirts.',[] )?.action,'revise');n++;
console.log(`${n} receipt-claim guard checks passed`);
