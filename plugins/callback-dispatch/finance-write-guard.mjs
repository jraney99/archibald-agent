// Runtime accident tripwire: once a command touches Finance data/code, only a
// small read allowlist is accepted. Arbitrary code running as the same macOS
// user can still assemble a path at runtime; OS/user separation is the real
// boundary for that case.
import path from 'node:path';

const FINANCE=/(?:workspace[\\/]Finance(?:[\\/]|\b)|workspace[\\/]Skills[\\/]afrexai-personal-finance(?:[\\/]|\b)|(?:card-[\w*-]+-\d{4}|transactions-live-\d{4}|reimbursements|finance_intake|statement_receipts|finance_balances)\.(?:jsonl?|ya?ml|sqlite3?|db))/i;
const REASON='Finance paths are read-only to agent shell tools. Use an enumerated read command or the maintained callback/intake writer. Direct edits bypass identity, saved answers and verification.';
const READ_COMMANDS=new Set(['ls','cat','head','tail','less','grep','rg','wc','stat','shasum','diff']);

// Every allowed Python entry point is named here. The validator describes the
// read-only surface; anything else, including --apply/--write, fails closed.
export const READ_ONLY_FINANCE_SCRIPTS=Object.freeze({
  'finance_truth.py': args=>onlyOptions(args,new Set(['--month']),new Set()),
  'finance_workflow.py': args=>onlyOptions(args,new Set(['--month','--date','--question']),new Set(['--json'])),
  'finance_evidence.py': args=>onlyOptions(args,new Set(['--amount','--date','--card','--merchant','--days']),new Set(['--no-gmail','--json'])),
  'finance_intake.py': args=>args.length===1 && args[0]==='status',
  'finance_status_card.py': args=>args.includes('--dry-run') && onlyOptions(args,new Set(['--month','--date']),new Set(['--dry-run'])),
  'finance_self_audit.py': args=>args.includes('--dry-run') && onlyOptions(args,new Set(['--date']),new Set(['--dry-run','--json'])),
  'finance_status.py': args=>onlyOptions(args,new Set(),new Set(['--brief','--changes-only'])),
  'finance_close.py': args=>args[0]==='checklist' && onlyOptions(args.slice(1),new Set(['--month']),new Set(['--json'])),
  'finance_balances.py': args=>args[0]==='status' && onlyOptions(args.slice(1),new Set(['--month']),new Set()),
  'finance_liabilities.py': args=>args[0]==='status' && onlyOptions(args.slice(1),new Set(),new Set(['--json'])),
  'finance_net_worth.py': args=>onlyOptions(args,new Set(['--as-of']),new Set(['--json'])),
  'finance_reimbursements.py': args=>['status','claims'].includes(args[0]) && onlyOptions(args.slice(1),new Set(),new Set(['--json'])),
});

function onlyOptions(args,valueFlags,boolFlags){
  for(let i=0;i<args.length;i++){
    const arg=args[i];
    if(boolFlags.has(arg)) continue;
    if(valueFlags.has(arg)){
      if(i+1>=args.length || args[i+1].startsWith('-')) return false;
      i++;continue;
    }
    return false;
  }
  return true;
}

function normalize(value){
  return String(value??'').replaceAll('\\','/').replace(/\/{2,}/g,'/').replace(/\/\.\//g,'/');
}

function decodedFinanceReference(command){
  if(!/\bbase64\b/i.test(command)) return false;
  for(const match of command.matchAll(/\b[A-Za-z0-9+/]{16,}={0,2}\b/g)){
    try{
      const decoded=Buffer.from(match[0],'base64').toString('utf8');
      if(FINANCE.test(normalize(decoded))) return true;
    }catch{}
  }
  return false;
}

function splitShell(command){
  const parts=[];let token='',quote=null,escaped=false;
  for(let i=0;i<command.length;i++){
    const ch=command[i];
    if(escaped){token+=ch;escaped=false;continue;}
    if(ch==='\\' && quote!=="'"){token+=ch;escaped=true;continue;}
    if(quote){token+=ch;if(ch===quote) quote=null;continue;}
    if(ch==='"'||ch==="'"){token+=ch;quote=ch;continue;}
    const two=command.slice(i,i+2);
    if(two==='&&'||two==='||'){if(token.trim()) parts.push(token.trim());parts.push(two);token='';i++;continue;}
    if(ch===';'||ch==='|'){if(token.trim()) parts.push(token.trim());parts.push(ch);token='';continue;}
    token+=ch;
  }
  if(quote||escaped) return null;
  if(token.trim()) parts.push(token.trim());
  return parts;
}

function shellWords(segment){
  const words=[];let token='',quote=null,escaped=false,started=false;
  for(const ch of segment){
    if(escaped){token+=ch;escaped=false;started=true;continue;}
    if(ch==='\\'&&quote!=="'"){escaped=true;continue;}
    if(quote){if(ch===quote) quote=null;else token+=ch;started=true;continue;}
    if(ch==='"'||ch==="'"){quote=ch;started=true;continue;}
    if(/\s/.test(ch)){if(started){words.push(token);token='';started=false;}continue;}
    token+=ch;started=true;
  }
  if(quote||escaped) return null;
  if(started) words.push(token);
  return words;
}

function stripEnv(words){
  const out=[...words];
  while(out.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(out[0])) out.shift();
  if(out[0]==='env'){
    out.shift();
    while(out.length && (/^[A-Za-z_][A-Za-z0-9_]*=/.test(out[0]) || out[0]==='-i')) out.shift();
  }
  return out;
}

function pythonReadAllowed(words){
  const argv=stripEnv(words);
  if(!/^python3?$/.test(path.basename(argv[0]??''))) return false;
  let i=1;
  while(['-W','-u','-B'].includes(argv[i])) i+=argv[i]==='-W'?2:1;
  const script=path.basename(argv[i]??'');
  const validate=READ_ONLY_FINANCE_SCRIPTS[script];
  if(!validate) return false;
  return validate(argv.slice(i+1));
}

function simpleReadAllowed(words){
  const argv=stripEnv(words), executable=path.basename(argv[0]??'');
  if(READ_COMMANDS.has(executable)) return true;
  if(executable==='jq') return !argv.some(arg=>/^--(?:slurpfile|argfile)$/.test(arg));
  return pythonReadAllowed(argv);
}

function inspectCommand(command,initialCwd){
  const normalized=normalize(command);
  if(/(?:^|[^>])>>?(?!>)|<<?|`|\$\(|\n/.test(normalized)) return false;
  const parts=splitShell(normalized);
  if(!parts) return false;
  let cwd=normalize(initialCwd),inFinance=FINANCE.test(cwd);
  const encodedFinance=decodedFinanceReference(command);
  const globalTouches=FINANCE.test(normalized)||inFinance||encodedFinance;
  for(const part of parts){
    if(['&&','||',';','|'].includes(part)) continue;
    const parsed=shellWords(part);if(!parsed?.length) return false;
    const words=stripEnv(parsed),exe=path.basename(words[0]??'');
    if(exe==='cd'||exe==='pushd'){
      if(words.length!==2) return false;
      cwd=normalize(path.resolve(cwd||'/',words[1]));inFinance=FINANCE.test(cwd);
      continue;
    }
    if(exe==='popd') return false;
    const touches=globalTouches||inFinance||FINANCE.test(normalize(part));
    if(touches && !simpleReadAllowed(parsed)) return false;
  }
  return true;
}

export function inspectFinanceWrite(toolName,params={}){
  const name=String(toolName).replace(/^.*[.:]/,'');
  const serialized=normalize(JSON.stringify(params));
  if(['write','edit','apply_patch'].includes(name)&&FINANCE.test(serialized))
    return {block:true,blockReason:REASON};
  if(!['exec','exec_command'].includes(name)) return;
  const command=String(params.command??params.cmd??'');
  const cwd=String(params.cwd??params.workdir??process.cwd());
  const touches=FINANCE.test(normalize(command))||FINANCE.test(normalize(cwd))||decodedFinanceReference(command);
  if(!touches) return;
  if(!inspectCommand(command,cwd)) return {block:true,blockReason:REASON};
}
