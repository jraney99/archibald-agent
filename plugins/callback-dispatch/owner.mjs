export function isOwnerMessage(event, context = {}, config = {}) {
  const channel = event?.channel ?? context.channelId;
  const sender = String(event?.senderId ?? context.senderId ?? "").replace(/^(?:telegram|tg):/, "");
  const session = event?.sessionKey ?? context.sessionKey ?? "";
  if (channel !== "telegram" || !sender || event?.isGroup || /:group:/.test(session)) return false;
  if (context.accountId && context.accountId !== "default") return false;
  const owners = config.channels?.telegram?.allowFrom ?? [];
  return owners.some(id => String(id).replace(/^(?:telegram|tg):/, "") === sender);
}
