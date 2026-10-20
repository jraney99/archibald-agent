import {readFileSync,statSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const OPENCLAW_HOME=process.env.OPENCLAW_HOME || fileURLToPath(new URL('../..',import.meta.url));
export const SETTLED_MAPPINGS_PATH=path.join(OPENCLAW_HOME,'workspace','Skills',
  'afrexai-personal-finance','data','finance_settled_mappings.generated.json');
const projectionCache=new Map();

export function readSettledMappings(projectionPath=SETTLED_MAPPINGS_PATH){
  try{
    const stat=statSync(projectionPath,{bigint:true});
    const stamp=`${stat.mtimeNs}:${stat.size}`;
    const cached=projectionCache.get(projectionPath);
    if(cached?.stamp===stamp) return cached.mappings;
    const mappings=JSON.parse(readFileSync(projectionPath,'utf8')).mappings??[];
    projectionCache.set(projectionPath,{stamp,mappings});
    return mappings;
  }catch{
    return projectionCache.get(projectionPath)?.mappings??[];
  }
}

const CONFUSABLES=new Map(Object.entries({
  'а':'a','е':'e','о':'o','р':'p','с':'c','х':'x','у':'y','і':'i','ј':'j',
  'А':'a','В':'b','Е':'e','К':'k','М':'m','Н':'h','О':'o','Р':'p','С':'c','Т':'t','Х':'x',
  'α':'a','ε':'e','ο':'o','ρ':'p','χ':'x','Α':'a','Β':'b','Ε':'e','Ζ':'z','Η':'h','Ι':'i','Κ':'k','Μ':'m','Ν':'n','Ο':'o','Ρ':'p','Τ':'t','Χ':'x',
}));

function collapseSpacedLetters(value){
  let text=value;
  for(let i=0;i<4;i++) {
    text=text.replace(/(?:\b[\p{L}\p{N}]\s+){2,}[\p{L}\p{N}]\b/gu,
      match=>match.replace(/\s+/g,''));
    text=text.replace(/\b[\p{L}\p{N}]{1,3}(?:\s+[\p{L}\p{N}]){2,}\b/gu,
      match=>match.replace(/\s+/g,''));
  }
  const guardedWords=['everything','matches','finance','financial','ledger','statement','september',
    'october','november','december','january','february','march','april','august','acme','globex',
    'initech','umbrella','hooli','vandelay','stark','payment','balance','charge','record','refund'];
  for(const word of guardedWords){
    const pattern=new RegExp(`\\b${[...word].join('\\s*')}\\b`,'giu');
    text=text.replace(pattern,word);
  }
  return text;
}

export const normalizedFinanceText=value=>{
  let text=String(value??'').normalize('NFKC')
    .replace(/[\u00ad\u034f\u061c\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/gu,'')
    .replace(/[\u00a0\u2007\u202f]/gu,' ');
  text=Array.from(text,ch=>CONFUSABLES.get(ch)??ch).join('');
  text=collapseSpacedLetters(text);
  return text.normalize('NFKD').replace(/\p{M}/gu,'').toLocaleLowerCase('en-US');
};
const clean=s=>normalizedFinanceText(s).replace(/[^a-z0-9$]+/g,' ').trim().replace(/\s+/g,' ');

const DISMISS=/\b(?:is not|isn t|was not|wasn t|does not match|doesn t match|could not match|couldn t match|cannot match|can t match|unknown|unrecognized|cannot confirm|can t confirm|something else)\b/;
export function settledMappingDismissal(line,mappings=readSettledMappings()){
  const text=clean(line);
  if(!DISMISS.test(text)) return false;
  return mappings.some(row=>text.includes(clean(row.merchant)) && text.includes(clean(row.settled)));
}

const MONTH='january|february|march|april|may|june|july|august|september|october|november|december';
const FINANCE_CUE=new RegExp(
  `\\$\\s*[\\d,]+(?:\\.\\d+)?|\\b(?:${MONTH}|`+
  // Issuer names are placeholders in this public copy; the live list names the owner's own issuers.
  'acme|globex|globex card|initech|umbrella|hooli|vandelay(?: bank)?|stark|visa|mastercard|card|'+
  'issuer|finance|financial|ledger|statements?|paid|payments?|owed|balances?|bills?|charges?|'+
  'purchases?|transactions?|records?|refunds?|reimbursements?|accounts?|autopay)\\b','i');
const VERDICT_WITHOUT_CUE=/\b(?:everything matches|nothing left|no open items|in the bag|all cleared|all balanced|all accounted for|books balance|closed|reconciled|final)\b/i;
const EXPLICIT_UNCHECKED=/\b(?:i (?:have not|haven t|did not|didn t) (?:check(?:ed)?|verif(?:y|ied)|sav(?:e|ed)|log(?:ged)?|record(?:ed)?)|i (?:cannot|can t) confirm|not (?:yet )?(?:checked|verified|confirmed|reconciled|closed|paid|complete|final|saved|logged|recorded)|still (?:need|needs|needed|waiting|awaiting)|here is what i still need|what i still need|need from you|awaiting|pending|unavailable|unknown|not established)\b/i;
const SCHEDULED=new RegExp(`\\b(?:scheduled|expected|arriv(?:es|ing)|due|will (?:arrive|post|close|run)|next (?:run|statement)|on|around|by)\\b[^.!?\\n]{0,60}\\b(?:20\\d\\d[-/]\\d\\d[-/]\\d\\d|(?:${MONTH})\\s+\\d{1,2}|\\d{1,2}[:.]\\d{2}\\s*(?:am|pm))\\b`,'i');
const META_QUOTE=/^(?:the incident|example|quoted text)\b.*\b(?:replied|said|quote|violation|under the rule)\b/i;
const FINANCE_SCRIPTS=new Set(['finance_truth.py','finance_workflow.py','finance_evidence.py','finance_intake.py']);

function splitClauses(text){
  return normalizedFinanceText(text).split(/(?<=[.!?])\s+|\n+|\s*(?:;|\bbut\b|\bhowever\b)\s*/u)
    .map(x=>x.trim()).filter(Boolean);
}

function latestUserText(messages){
  const message=[...(messages??[])].reverse().find(m=>m?.role==='user');
  if(typeof message?.content==='string') return message.content;
  return (Array.isArray(message?.content)?message.content:[]).map(b=>b?.text??'').join(' ');
}

function commandOwner(command){
  const cmd=String(command??'').trim();
  if(!cmd || /(?:[;&|`]|\$\(|\n)/.test(cmd)) return null;
  const hit=cmd.match(/\b(finance_(?:truth|workflow|evidence|intake)\.py)\b/);
  if(!hit || !FINANCE_SCRIPTS.has(hit[1])) return null;
  if(hit[1]==='finance_intake.py' && !/\bstatus\b/.test(cmd)) return null;
  if(hit[1]==='finance_workflow.py' && /\b--(?:defer|until|balance-needs-source|reason|account)\b/.test(cmd)) return null;
  return hit[1];
}

function successfulEvidence(turn){
  const calls=new Map();
  for(const m of turn??[]) if(m?.role==='assistant') for(const b of Array.isArray(m.content)?m.content:[])
    if(b?.type==='toolCall' && ['exec','exec_command'].includes(b.name)) {
      const owner=commandOwner(b.arguments?.command??b.arguments?.cmd);
      if(owner) calls.set(b.id,owner);
    }
  const evidence=[];
  for(const m of turn??[]) if(m?.role==='toolResult' && !m.isError && calls.has(m.toolCallId))
    for(const b of typeof m.content==='string'?[{type:'text',text:m.content}]:(Array.isArray(m.content)?m.content:[])) if(b?.type==='text' && String(b.text??'').trim()) {
      const raw=String(b.text), owner=calls.get(m.toolCallId);
      const item={owner,raw,normalized:clean(raw),allowedClaims:[]};
      if(owner==='finance_truth.py') {
        const start=raw.indexOf('{');
        try{
          const parsed=JSON.parse(raw.slice(start));
          if(parsed.finance_operation==='truth') item.allowedClaims=(parsed.allowed_claims??[]).map(clean);
        }catch{}
      }
      evidence.push(item);
    }
  return evidence;
}

const CONCEPTS=[
  [/\b(?:paid|payment|bill|taken care)\b/i,/\b(?:paid|payment|covered by confirmed payments|received)\b/i],
  [/\b(?:owed|due)\b/i,/\b(?:owed|due|statement balance)\b/i],
  [/\bbalance(?:d|s)?\b/i,/\b(?:balance|balanced|closing)\b/i],
  [/\b(?:charge|purchase|transaction)\b/i,/\b(?:charge|purchase|transaction|on file)\b/i],
  [/\b(?:record|ledger)\b/i,/\b(?:record|ledger|on file|transaction)\b/i],
  [/\bstatements?\b/i,/\bstatement\b/i],
];

function assertionSupported(assertion,evidence){
  const wanted=clean(assertion);
  for(const item of evidence){
    if(item.allowedClaims.some(claim=>claim===wanted || claim.includes(wanted) || wanted.includes(claim))) return true;
    if(item.normalized.includes(wanted)) return true;
    const anchors=[];
    for(const match of wanted.matchAll(/\$\s*[\d,]+(?:\.\d+)?|\b20\d\d[-/]\d\d(?:[-/]\d\d)?\b|\b(?:acme|globex|initech|umbrella|hooli|vandelay|stark|visa|mastercard)\b/gi))
      anchors.push(match[0].replace(/[,$\s]/g,''));
    const evidenceCompact=item.normalized.replace(/[,$\s]/g,'');
    if(!anchors.length || !anchors.every(anchor=>evidenceCompact.includes(anchor))) continue;
    const concepts=CONCEPTS.filter(([claim])=>claim.test(wanted));
    if(concepts.length && concepts.every(([,support])=>support.test(item.normalized))) return true;
  }
  return false;
}

/** Finance assertions use an allowlist: questions, explicit uncertainty/needs,
 * and dated schedule statements are allowed. Everything else with finance
 * context must be supported by one of four sanctioned readers in this turn. */
export function financeClaimDecision(event,turn){
  const text=String(event?.lastAssistantMessage??'');
  if(!text.trim()) return;
  const user=latestUserText(event?.messages);
  const contextCue=FINANCE_CUE.test(normalizedFinanceText(user));
  let sawFinance=false;
  const assertions=[];
  for(const clause of splitClauses(text)){
    const financeCued=FINANCE_CUE.test(clause) || (contextCue && VERDICT_WITHOUT_CUE.test(clause)) || settledMappingDismissal(clause);
    if(!financeCued) continue;
    sawFinance=true;
    if(/\?$/.test(clause)) continue;
    if(EXPLICIT_UNCHECKED.test(clause) || SCHEDULED.test(clause) || META_QUOTE.test(clause)) continue;
    assertions.push(clause);
  }
  // Abbreviations such as “Dr. Example” split at the period; evaluate maintained
  // merchant/category mappings once against the intact reply as well.
  if(settledMappingDismissal(text) && !/\?\s*$/.test(text)) {
    sawFinance=true;
    const intact=normalizedFinanceText(text).trim();
    if(!assertions.includes(intact)) assertions.push(intact);
  }
  const canonicalNeed=/\bwhat do you need from me\b/i.test(normalizedFinanceText(user));
  if(!assertions.length && !canonicalNeed) return sawFinance ? {allow:true} : undefined;
  const evidence=successfulEvidence(turn);
  if(canonicalNeed){
    const workflow=evidence.filter(x=>x.owner==='finance_workflow.py');
    if(!workflow.some(x=>x.normalized.includes(clean(text)))) return {action:'revise',reason:'finance-claim: owner needs answer is not from the canonical workflow status',retry:{instruction:'Run finance_workflow.py --month YYYY-MM --question need and answer from its exact output.',idempotencyKey:'finance-claim',maxAttempts:2}};
  }
  if(assertions.every(claim=>assertionSupported(claim,evidence))) return;
  return {action:'revise',reason:'finance-claim: finance assertion is not supported by a sanctioned result in this turn',retry:{
    instruction:'Ground finance assertions in a successful result from finance_truth.py, finance_workflow.py, finance_evidence.py, or finance_intake.py status in this turn. Relay the supported result closely. Questions, explicit “not checked yet” statements, and dated schedule statements may be answered without asserting a result.',
    idempotencyKey:'finance-claim',maxAttempts:2}};
}
