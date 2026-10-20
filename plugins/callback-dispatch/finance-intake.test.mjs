import assert from 'node:assert/strict';
import plugin from './index.mjs';
import {intakeArgv,dispatchFinance,recordFinanceDelivery} from './finance-intake.mjs';
import {inspectFinanceWrite} from './finance-write-guard.mjs';
const config={channels:{telegram:{allowFrom:['test-owner']}}};
// Installed reply_dispatch envelope. MediaPaths is absent from before_dispatch.
const event={sendPolicy:'allow',ctx:{Provider:'telegram',SenderId:'test-owner',AccountId:'default',
  SessionKey:'agent:main:telegram:direct:test-owner',MessageSid:'123',
  MediaPaths:['~/.openclaw/media/inbound/one.pdf','~/.openclaw/media/inbound/two.pdf']}};
const argv=intakeArgv(event,{},config);
assert.deepEqual(argv.slice(3,5),event.ctx.MediaPaths);
assert.equal(intakeArgv({...event,ctx:{...event.ctx,SenderId:'outsider'}},{},config),null);
assert.equal(intakeArgv({...event,ctx:{...event.ctx,MediaPaths:['/private/tmp/untrusted.pdf']}},{},config),null);
assert.equal(intakeArgv({...event,ctx:{...event.ctx,MediaPaths:[...event.ctx.MediaPaths,'~/.openclaw/media/inbound/receipt.png']}},{},config),null);
let ran=0,sent=[],calls=[];
const runner=async(args,env)=>{ran++;calls.push(args);
  if(args[0].endsWith('finance_delivery.py')) return {ok:true,out:'{"send":true}'};
  if(args.includes('ack')) return {ok:true,out:'{"matched":true}'};
  assert.equal(args.at(-1),'123');
  return{ok:true,out:JSON.stringify({handled:true,text:'Saved and checked; month coverage separate.'})}};
const context={dispatcher:{sendFinalReply:p=>{sent.push(p);return true;},getQueuedCounts:()=>({final:sent.length,tool:0,block:0})}};
const response=await dispatchFinance(event,context,config,runner,{});
assert.equal(response.handled,true);assert.equal(ran,3);assert.equal(sent.length,1);
assert.deepEqual(calls[2].slice(1,5),['ack','--status','queued','--message-id']);
await dispatchFinance({...event,suppressUserDelivery:true},context,config,runner,{});
assert.equal(ran,3,'dry-run delivery suppression must not mutate state');
const replay=await dispatchFinance(event,context,config,async()=>({ok:true,out:JSON.stringify({handled:true,text:''})}),{});
assert.equal(replay.handled,true);assert.equal(replay.queuedFinal,false);assert.equal(sent.length,1);
let deliveryCalls=[];
await recordFinanceDelivery({content:'Saved and checked; month coverage separate.',success:true,messageId:'777'},
  async args=>{deliveryCalls.push(args);return{ok:true,out:'{"matched":true}'}},{});
assert.equal(deliveryCalls.length,2);
assert.equal(deliveryCalls[0][1],'mark-digest');
assert.equal(deliveryCalls[1][2],'--status');assert.equal(deliveryCalls[1][3],'delivered');
assert.equal(inspectFinanceWrite('exec',{command:'python3 - <<\'PY\'\np="workspace/Finance/spending/card-1111-2026.json"\nopen(p,"w").write("{}")\nPY'}).block,true);
assert.equal(inspectFinanceWrite('write',{path:'~/.openclaw/workspace/Finance/spending/card-1111-2026.json',content:'{}'}).block,true);
assert.equal(inspectFinanceWrite('exec',{command:'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_intake.py status'}),undefined);
assert.equal(inspectFinanceWrite('write',{path:'/private/tmp/ordinary-note.txt',content:'hello'}),undefined);
const finance='~/.openclaw/workspace/Finance';
const blockedCommands=[
  `cd ${finance} && echo x > relative.json`,
  `cd ${finance}; python3 fix.py`,
  `cd ${finance} && echo x>relative.json`,
  `perl -i -pe s/x/y/ ${finance}/spending/card-0000-2026.json`,
  `ruby -i -pe 'gsub(/x/,"y")' ${finance}/spending/card-0000-2026.json`,
  `dd if=/tmp/x of=${finance}/spending/card-0000-2026.json`,
  `ln -sf /tmp/x ${finance}/spending/card-0000-2026.json`,
  `rsync /tmp/x ${finance}/spending/card-0000-2026.json`,
  `chmod 777 ${finance}/spending/card-0000-2026.json`,
  `sqlite3 ${finance}/state/finance.db 'delete from jobs'`,
  `git checkout -- ${finance}/spending/card-0000-2026.json`,
  `git stash -- ${finance}/spending/card-0000-2026.json`,
  `open -e ${finance}/spending/card-0000-2026.json`,
  `osascript -e 'set p to "${finance}/spending/card-0000-2026.json"'`,
  `echo x > ${finance}//spending/./card-0000-2026.json`,
  `echo x > ${finance}/spending/card-*-2026.json`,
  `find ${finance} -name '*.json' -delete`,
  `find ${finance} -type f -exec chmod 777 {} +`,
  `rg card ${finance} | xargs rm`,
  `git restore ${finance}/spending/card-0000-2026.json`,
  `git clean -fd ${finance}`,
  `git checkout -- ${finance}`,
  `git stash -- ${finance}`,
  `tar xf /tmp/archive.tar -C ${finance}`,
  `unzip /tmp/archive.zip -d ${finance}`,
  `vim ${finance}/spending/card-0000-2026.json`,
  `ex ${finance}/spending/card-0000-2026.json`,
  `nano ${finance}/spending/card-0000-2026.json`,
  `python3 -m json.tool ${finance}/spending/card-0000-2026.json ${finance}/spending/out.json`,
  `cat /tmp/x | sponge ${finance}/spending/card-0000-2026.json`,
  `echo L2hvbWUvb3duZXIvLm9wZW5jbGF3L3dvcmtzcGFjZS9GaW5hbmNlL3NwZW5kaW5nL2NhcmQtMDAwMC0yMDI2Lmpzb24= | base64 -d | xargs rm`,
  `python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_credits.py --apply`,
  `python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_intake.py retry`,
  `cd ${finance} && python3 ../Skills/afrexai-personal-finance/scripts/finance_close.py close --month 2026-09`,
  `pushd ${finance} && echo x > spending/relative.json`,
  `pushd ${finance} && find . -delete`,
];
for(const command of blockedCommands)
  assert.equal(inspectFinanceWrite('exec',{command})?.block,true,`must block ${command}`);
const allowedCommands=[
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_intake.py status',
  `ls -la ${finance}`,
  `cat ${finance}/spending/card-0000-2026.json`,
  `head -20 ${finance}/spending/card-0000-2026.json`,
  `tail -20 ${finance}/spending/card-0000-2026.json`,
  `less ${finance}/spending/card-0000-2026.json`,
  `grep -n CardA ${finance}/spending/card-0000-2026.json`,
  `rg -n CardA ${finance}`,
  `wc -l ${finance}/spending/card-0000-2026.json`,
  `stat ${finance}/spending/card-0000-2026.json`,
  `shasum -a 256 ${finance}/spending/card-0000-2026.json`,
  `diff ${finance}/a.json ${finance}/b.json`,
  `jq '.transactions | length' ${finance}/spending/checking-2026.json`,
  `cd ${finance} && cat spending/card-0000-2026.json`,
  `pushd ${finance} && cat spending/card-0000-2026.json`,
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_truth.py --month 2026-09',
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_workflow.py --month 2026-09 --question need --json',
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_evidence.py --amount 42.10 --date 2026-09-20 --card 0000 --no-gmail --json',
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_status_card.py --month 2026-09 --dry-run',
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_self_audit.py --date 2026-10-03 --dry-run --json',
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_close.py checklist --month 2026-09 --json',
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_balances.py status --month 2026-09',
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_liabilities.py status --json',
  'python3 ~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts/finance_net_worth.py --as-of 2026-10-03 --json',
  'cat /private/tmp/ordinary-note.txt',
  'cd /private/tmp && python3 inspect.py',
  'python3 /tmp/x.py', // runtime-built targets are outside a command-text tripwire
];
for(const command of allowedCommands)
  assert.equal(inspectFinanceWrite('exec',{command}),undefined,`must allow ${command}`);
assert.equal(inspectFinanceWrite('exec',{command:'echo x',workdir:finance})?.block,true);
assert.equal(inspectFinanceWrite('exec',{command:'cat spending/card-0000-2026.json',workdir:finance}),undefined);
const registered=new Map();
plugin.register({config,logger:{warn(){},info(){}},on:(name,fn)=>registered.set(name,fn)});
assert.ok(registered.has('reply_dispatch'));assert.ok(registered.has('before_tool_call'));
assert.ok(registered.has('message_sent'));
assert.equal(registered.get('before_tool_call')({toolName:'edit',params:{path:'~/.openclaw/workspace/Finance/spending/card-0000-2026.json'}}).block,true);
console.log('Finance dispatcher: runtime media envelope, owner, two PDFs, dry run, mutation boundary passed');
