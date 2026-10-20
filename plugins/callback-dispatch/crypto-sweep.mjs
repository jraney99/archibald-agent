export function cryptoDestinationArgv(event = {}) {
  const replyId = event?.replyToId ?? event?.replyToIdFull;
  const text = String(event?.content ?? event?.body ?? '').trim();
  if (!replyId || !text) return null;
  return ['afrexai-personal-finance/scripts/finance_holdings.py',
    'reply-destination', '--reply-id', String(replyId).split(':').at(-1), '--text', text];
}
