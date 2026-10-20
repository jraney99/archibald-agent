/** A receipt save needs a successful sanctioned writer and its read-back receipt. */
export function receiptClaimDecision(event, turn) {
  const text = String(event?.lastAssistantMessage || '').replace(/[’]/g, "'");
  const messages = event?.messages || [];
  const user = [...messages].reverse().find(m => m.role === 'user');
  const context = JSON.stringify(user?.content || '') + ' ' + text;
  if (!/receipt|purchase details|card (?:number|suffix)|shirts?/i.test(context)) return;
  const clauses = text.split(/[.!?\n]+/);
  const claims = clauses.filter(s => !/\b(?:not saved|not recorded|nothing (?:is |was )?saved|haven't|hasn't|couldn't|failed|next step|will |would |could |should |need to|before )/i.test(s) &&
    /\b(?:saved|recorded|logged|filed|I'm (?:now )?(?:saving|recording|logging)|I am (?:now )?(?:saving|recording|logging))\b/i.test(s));
  if (!claims.length) return;
  const calls = new Map();
  for (const m of turn || []) for (const c of Array.isArray(m.content) ? m.content : []) {
    if (m.role !== 'assistant' || c.type !== 'toolCall') continue;
    const cmd = String(c.arguments?.command || c.arguments?.cmd || '');
    if (/^(?:python3?|\/[^\s]+\/python3?)\s+(?:-W\s+ignore\s+)?[^\s]*\b(?:finance_txn_split|receipt_splits|finance_purchase_review)\.py\s/.test(cmd) && !/[;|&`\n]/.test(cmd)) calls.set(c.id, cmd);
  }
  for (const m of turn || []) {
    if (m.role !== 'toolResult' || m.isError || !calls.has(m.toolCallId)) continue;
    if (m.details?.exitCode != null && m.details.exitCode !== 0) continue;
    const result = (m.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
    if (/NOT saved|Save unverified|Traceback|read-back failed/i.test(result)) continue;
    for (const line of result.split('\n')) {
      if (!line.startsWith('FINANCE_RECEIPT_SAVED ')) continue;
      try {
        const proof = JSON.parse(line.slice('FINANCE_RECEIPT_SAVED '.length));
        const cmd = calls.get(m.toolCallId);
        const id = cmd.match(/--(?:id|confirm|transaction-id)\s+['"]?([^\s'"]+)/)?.[1];
        if (proof.read_back === true && id && String(proof.transaction_id).replace(/^live:/, '') === id.replace(/^live:/, '')) {
          const claimedIds = text.match(/live:[a-zA-Z0-9_-]+/g) || [];
          if (claimedIds.every(x => x.slice(5) === id.replace(/^live:/, ''))) return;
        }
      } catch { /* malformed evidence never authorizes a save claim */ }
    }
  }
  return { action: 'revise', reason: 'receipt save claim lacks a successful writer read-back receipt',
    retry: { instruction: 'Do not claim saved, recorded, or recording from chat or an unrelated tool result. Use the maintained receipt/purchase writer and its FINANCE_RECEIPT_SAVED read-back proof for this transaction. If missing or failed, state not saved/unverified and the next step. Do not repeat an already verified write.', idempotencyKey: 'receipt-read-back', maxAttempts: 2 } };
}
