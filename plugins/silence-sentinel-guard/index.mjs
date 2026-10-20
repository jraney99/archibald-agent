/**
 * A silence sentinel that isn't exactly the sentinel gets delivered.
 *
 * WHY (2026-10-09): The user received a wall of the email daemon's internal `[Cal]` log
 * lines, ending `NO_REPLY 🎩`. `NO_REPLY` is the gateway's silence signal and is matched
 * EXACTLY; the trailing hat broke the match, so what should have been silence was sent
 * to his phone — logs and all.
 *
 * His own rules already say this, twice:
 *   AGENTS.md         "output exactly NO_REPLY with no 🎩 and no other text"
 *   rules/15          "never add 🎩 to silence sentinels ... Adding 🎩 breaks the
 *                      gateway's silence detection and delivers the message"
 *
 * A rule violated despite being written down twice is the signal for a guard
 * (plugins/GUARD-PATTERN.md). The model has to end nearly every Telegram message with a
 * hat, so appending one is the trained reflex and the sentinel is the rare exception —
 * exactly the shape that gets got wrong again.
 *
 * Deliberately narrow. It fires only when the reply CONTAINS a sentinel token and is
 * not exactly that sentinel. A reply that merely mentions NO_REPLY while explaining
 * something — as this very session did — is left alone, because the token has to be
 * the operative content, not a word in a sentence.
 */
const SENTINELS = ["NO_REPLY", "HEARTBEAT_OK"];

/** The sentinel this reply was *trying* to be, or null. */
export function intendedSentinel(text) {
  const raw = String(text ?? "");
  const trimmed = raw.trim();
  if (!trimmed) return null;
  for (const s of SENTINELS) {
    if (trimmed === s) return null;              // already correct, nothing to do
    // The sentinel plus decoration and nothing else: a hat, quotes, backticks,
    // stray punctuation, trailing whitespace. Anything with real words around it is
    // prose about the sentinel, not an attempt at one.
    const decorated = new RegExp(`^[\\s"'\`*_]*${s}[\\s"'\`*_.,:;!-]*$`, "u");
    const stripped = trimmed.replace(/\p{Extended_Pictographic}|️/gu, "").trim();
    if (decorated.test(stripped)) return s;
  }
  return null;
}

/** A reply that buried a sentinel at the end of other output — the Cal-log case.
 *
 * The sentinel must be its OWN LAST LINE. An earlier version asked whether the whole
 * reply ended with the token, which fired on the sentence "never add the hat to silence
 * sentinels like NO_REPLY or HEARTBEAT_OK" — prose that merely finishes on the word.
 * A guard that cannot survive a discussion of its own subject is not shippable.
 */
export function trailingSentinel(text) {
  const lines = String(text ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return null;              // one line is intendedSentinel's job
  const last = lines[lines.length - 1]
    .replace(/\p{Extended_Pictographic}|\uFE0F/gu, "")
    .replace(/^["'`*_\s]+/u, "")
    .replace(/["'`*_.,:;!\-\s]+$/u, "");
  return SENTINELS.includes(last) ? last : null;
}


export function inspectFinalReply(event) {
  const text = event?.lastAssistantMessage;
  const intended = intendedSentinel(text) ?? trailingSentinel(text);
  if (!intended) return undefined;
  return {
    action: "revise",
    reason: `silence-sentinel: reply is ${intended} with extra text, which the gateway ` +
            `will not recognise as silence and will deliver instead`,
    retry: {
      instruction:
        `Output exactly ${intended} — that string alone, nothing before or after it, ` +
        `no 🎩, no explanation, no log output. It is a signal to the gateway, not a ` +
        `message to the user: anything attached to it breaks the match and sends the whole ` +
        `reply to his phone. If you did mean to say something to him, write the message ` +
        `properly and do not use ${intended} at all.`,
      idempotencyKey: "silence-sentinel",
      maxAttempts: 2,
    },
  };
}

export default {
  id: "silence-sentinel-guard",
  name: "Silence Sentinel Guard",
  description:
    "before_agent_finalize: a reply that is a silence sentinel carrying anything else — " +
    "a 🎩, trailing log output — is sent back, because the gateway matches the sentinel " +
    "exactly and delivers everything it does not recognise.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    api.on(
      "before_agent_finalize",
      async (event) => {
        let decision;
        try {
          decision = inspectFinalReply(event);
        } catch (err) {
          log.warn?.(`[silence-sentinel-guard] inspect error: ${err?.message ?? err}`);
          return;
        }
        if (!decision) return;
        log.warn?.(`[silence-sentinel-guard] ${decision.reason}`);
        return decision;
      },
      { priority: 55 },
    );
    log.info?.("[silence-sentinel-guard] before_agent_finalize handler registered");
  },
};
