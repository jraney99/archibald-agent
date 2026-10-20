import path from 'node:path';
import {createHash} from 'node:crypto';
import { isOwnerMessage } from './owner.mjs';

const digest = text => createHash('sha256').update(String(text ?? ''), 'utf8').digest('hex');
const pendingDigests = new Set();

export function intakeArgv(event, context, config, root = '~/.openclaw') {
  const ctx = event?.ctx ?? {};
  const inbound = { channel: ctx.OriginatingChannel ?? ctx.Surface ?? ctx.Provider,
    senderId: ctx.SenderId, sessionKey: ctx.SessionKey,
    isGroup: Boolean(ctx.GroupSubject || ctx.GroupChannel || ctx.ChatType === 'group') };
  if (!isOwnerMessage(inbound, {accountId: ctx.AccountId}, config)) return null;
  // Paths come from the runtime envelope, never from model text or PDF content.
  const paths = [...new Set([...(ctx.MediaPaths ?? []), ctx.MediaPath].filter(Boolean))];
  if (!paths.length) return null;
  const base = path.resolve(root, 'media/inbound') + path.sep;
  if (paths.some(p => typeof p !== 'string' || !path.resolve(p).startsWith(base))) return null;
  const pdfs = paths.filter(p => path.extname(p).toLowerCase() === '.pdf');
  // Mixed uploads remain conversational; never swallow a receipt image when
  // handling the PDF in the same Telegram envelope.
  if (!pdfs.length || pdfs.length !== paths.length) return null;
  return ['afrexai-personal-finance/scripts/finance_intake.py', 'route', '--paths', ...pdfs,
    '--message-id', String(ctx.MessageSidFull ?? ctx.MessageSid ?? '')];
}

export async function dispatchFinance(event, context, config, runScript, env) {
  const argv = intakeArgv(event, context, config);
  if (!argv || event.suppressUserDelivery || event.sendPolicy === 'deny') return;
  const result = await runScript(argv, env, 60000);
  let output;
  try { output = JSON.parse(result.out); } catch { return; }
  if (!result.ok || !output.handled) return;
  const inboundId = String(event?.ctx?.MessageSidFull ?? event?.ctx?.MessageSid ?? '');
  let maySend = Boolean(output.text);
  if (maySend) {
    const reserved = await runScript(['afrexai-personal-finance/scripts/finance_delivery.py', 'reserve',
      '--key', `intake-ack:${inboundId}`, '--kind', 'intake_ack', '--text-sha256', digest(output.text)], env, 10000);
    if (!reserved.ok) throw new Error('Finance acknowledgment intent was not saved');
    try { maySend = Boolean(JSON.parse(reserved.out).send); } catch { maySend = false; }
  }
  const queuedFinal = maySend ? context.dispatcher.sendFinalReply({text: output.text}) : false;
  if (queuedFinal && output.text) {
    const ack = await runScript(['afrexai-personal-finance/scripts/finance_intake.py', 'ack',
      '--status', 'queued', '--message-id', inboundId, '--text-sha256', digest(output.text)], env, 10000);
    if (!ack.ok) throw new Error('Finance acknowledgment queue state was not saved');
    pendingDigests.add(digest(output.text));
  }
  context.recordProcessed?.('finance-intake');
  context.markIdle?.('finance-intake');
  return {handled: true, queuedFinal, counts: context.dispatcher.getQueuedCounts()};
}

export async function recordFinanceDelivery(event, runScript, env) {
  if (!event?.content) return;
  const textDigest = digest(event.content);
  const financeShaped = /^(?:💸|💳|💰|🏦|📊|🧾|🤝)|\b(?:finance status|statement|reimbursement|repayment)\b/i.test(String(event.content));
  // message_sent is global. Avoid starting a finance subprocess for ordinary
  // outbound traffic, while still allowing a finance receipt to be recovered
  // after a gateway restart when the in-memory intake digest is gone.
  if (!pendingDigests.has(textDigest) && !financeShaped) return;
  const generic = ['afrexai-personal-finance/scripts/finance_delivery.py', 'mark-digest',
    '--status', event.success ? 'delivered' : 'failed', '--text-sha256', textDigest];
  if (event.messageId) generic.push('--delivery-id', String(event.messageId));
  if (!event.success && event.error) generic.push('--error', String(event.error).slice(0, 500));
  const marked = await runScript(generic, env, 10000);
  if (!marked.ok) throw new Error('Finance generic delivery state was not saved');
  // The in-memory set identifies intake acknowledgments, while the durable
  // generic receipt above also works after a gateway restart.
  if (!pendingDigests.has(textDigest)) return;
  const args = ['afrexai-personal-finance/scripts/finance_intake.py', 'ack',
    '--status', event.success ? 'delivered' : 'failed', '--text-sha256', textDigest];
  if (event.messageId) args.push('--delivery-id', String(event.messageId));
  if (!event.success && event.error) args.push('--error', String(event.error).slice(0, 500));
  const result = await runScript(args, env, 10000);
  if (!result.ok) throw new Error('Finance acknowledgment delivery state was not saved');
  pendingDigests.delete(textDigest);
}
