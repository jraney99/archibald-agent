/*
 * pending-context — tell the agent when the user has been ASKED something.
 *
 * WHY THIS EXISTS (2026-09-19)
 * Some prompts ask for a typed answer instead of a tap ("Reply with what it was
 * for", "What did you wear instead?"). The only thing linking his next message to
 * that question was a rule telling the agent to go look for a pending file. With
 * nothing in front of it, the agent (Haiku) read "This is for a yoga class" — the
 * answer to a card-charge question — as a wardrobe request and built an outfit;
 * the charge was never categorised.
 *
 * This does NOT interpret or claim his message (a category needs judgment: splits,
 * receipts, "this is for a…"). It only removes the guess about WHETHER a question
 * is open, by prepending one line per fresh pending file to the agent's turn. The
 * agent still decides if the message answers it, and still follows the rule file.
 *
 * Read-only by construction: no writes, no processes, no network.
 */

import { readFileSync } from "node:fs";

const OPENCLAW_DIR = "~/.openclaw";
const RULES_DIR = "Documentation/rules";
const HOUR = 60 * 60 * 1000;

const money = (v) => (typeof v === "number" ? `$${v.toFixed(2)}` : "a");
const clip = (v, n = 80) => String(v ?? "").replace(/\s+/g, " ").trim().slice(0, n);

// maxAgeMs: how long after asking the question can still plausibly be "open".
// A stale pending file is harmless on disk but must not colour every later turn.
export const SOURCES = [
  {
    file: `${OPENCLAW_DIR}/pending_txn_category.json`,
    maxAgeMs: (p) => (p.mode === "receipt" ? 24 * HOUR : 2 * HOUR),
    describe: (p) =>
      p.mode === "receipt"
        ? `he was asked to send a RECEIPT PHOTO for the ${money(p.amount)} card charge at "${clip(p.merchant)}" (id ${clip(p.id, 40)})`
        : `he was asked what the ${money(p.amount)} card charge at "${clip(p.merchant)}" (id ${clip(p.id, 40)}) was for`,
    rule: `${RULES_DIR}/09-live-purchase-what-was-this-for-reply-free-text-photo-not-a-call.md`,
  },
  {
    file: `${OPENCLAW_DIR}/pending_p2p_category.json`,
    maxAgeMs: () => 2 * HOUR,
    describe: (p) => `he was asked what a peer-to-peer payment (id ${clip(p.id, 40)}) was for`,
    rule: `${RULES_DIR}/10-live-p2p-what-was-this-for-reply-free-text-not-a-callback.md`,
  },
  {
    file: `${OPENCLAW_DIR}/pending_bank_transfer_category.json`,
    maxAgeMs: () => 2 * HOUR,
    describe: (p) => `he was asked what a bank transfer (id ${clip(p.id, 40)}) was for`,
    rule: `${RULES_DIR}/11-live-bank-transfer-what-was-this-for-reply-free-text-not-a-callback.md`,
  },
  {
    file: `${OPENCLAW_DIR}/workspace/Skills/ai-wardrobe-steward/profile/pending_wear_report.json`,
    maxAgeMs: () => 14 * HOUR, // asked at 9:30pm; a morning answer still counts
    describe: (p) => `he was asked what he actually wore to "${clip(p.event_title)}" instead of the proposed outfit (proposal ${clip(p.proposal_id, 20)})`,
    rule: "Skills/ai-wardrobe-steward/references/21-wear-reports.md",
  },
  // Appended, not inserted: pending-context.test.mjs addresses these by index
  // (SOURCES[3] is the wear report), so a new entry goes on the end. Order has no
  // effect on behaviour — openQuestions sorts what it finds by asked_at.
  {
    file: `${OPENCLAW_DIR}/workspace/Skills/car-maintenance/state/pending_odometer.json`,
    maxAgeMs: () => 6 * HOUR, // asked mid-morning; a number typed tomorrow answers something else
    describe: (p) =>
      `he was asked for the car's odometer reading (last on file ${clip(p.last_miles)} mi on ${clip(p.last_date, 12)}) — a bare number in his reply is that reading`,
    rule: `${RULES_DIR}/34-odometer-reply.md`,
  },
  {
    file: `${OPENCLAW_DIR}/workspace/Skills/ai-wardrobe-steward/state/pending_wear_photo.json`,
    maxAgeMs: () => 2 * HOUR,
    describe: (p) =>
      p.status === "correcting"
        ? `he was shown a guess at what he is wearing in a photo (${clip(p.labels?.join?.(", ") ?? p.labels, 120)}) and tapped "Not quite" — his reply says what to fix (question id ${clip(p.id, 20)})`
        : `he was asked whether a photo shows him wearing ${clip(p.labels?.join?.(", ") ?? p.labels, 120)} (question id ${clip(p.id, 20)})`,
    rule: `${RULES_DIR}/35-wardrobe-photo-and-wear-replies.md`,
  },
  {
    // The monthly tape measurements. Several numbers typed in his own words, so there
    // are no buttons; a long window because taking a tape to nine sites is not done in
    // the minute he reads the message.
    file: `${OPENCLAW_DIR}/workspace/Skills/hevy-workout/state/pending_measurement.json`,
    maxAgeMs: () => 12 * HOUR,
    describe: (p) =>
      `he was asked for his monthly body measurements (${clip(p.fields?.join?.(", ") ?? p.fields, 120)}) — ` +
      `numbers in his reply are those readings` +
      (p.last_weight ? `; last weight on file ${clip(p.last_weight)} lb (${clip(p.last_weight_date, 12)})` : ""),
    rule: `${RULES_DIR}/36-measurement-reply.md`,
  },
  {
    // 2026-10-12: after a 👌/👎 fit tap he describes what is off in his own words.
    file: `${OPENCLAW_DIR}/workspace/Skills/ai-wardrobe-steward/state/pending_fit_detail.json`,
    maxAgeMs: () => 12 * HOUR,
    describe: (p) =>
      `he tapped "${clip(p.verdict)}" on how "${clip(p.item, 60)}" from ${clip(p.retailer, 40)} fits and was asked what is off — his reply describes the fit (question id ${clip(p.id, 20)})`,
    rule: `${RULES_DIR}/37-fit-detail-reply.md`,
  },
];

function readPending(file) {
  try {
    const parsed = JSON.parse(readFileSync(file, "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

// A pending file may hold several open questions at once — one per transaction.
// 2026-09-28: it used to hold exactly one, so a second charge silently replaced
// the first and his typed answer could land on the wrong transaction. See
// workspace/Skills/shared/pending_questions.py, which owns the shape.
// A flat object is still read as a single question: files written before the
// change, and the wear-report and odometer files, which only ever ask once.
export function entriesOf(pending) {
  if (!pending) return [];
  if (Array.isArray(pending.open)) {
    return pending.open.filter((q) => q && typeof q === "object" && !Array.isArray(q));
  }
  return [pending];
}

export function openQuestions(now = Date.now(), read = readPending, sources = SOURCES) {
  const open = [];
  for (const source of sources) {
    for (const pending of entriesOf(read(source.file))) {
      const askedAt = Date.parse(pending?.asked_at ?? "");
      if (Number.isNaN(askedAt)) continue;
      const age = now - askedAt;
      if (age < 0 || age > source.maxAgeMs(pending)) continue;
      open.push({ askedAt, text: source.describe(pending), rule: source.rule });
    }
  }
  return open.sort((a, b) => b.askedAt - a.askedAt);
}

// Only a live Telegram conversation can be an answer; cron and isolated runs never are.
export function isConversationTurn(ctx = {}) {
  if (ctx.jobId) return false;
  const channel = String(ctx.channel ?? ctx.messageProvider ?? ctx.channelId ?? "").toLowerCase();
  return channel === "telegram";
}

export function pendingContext(ctx, now = Date.now(), read = readPending) {
  if (!isConversationTurn(ctx)) return null;
  const open = openQuestions(now, read);
  if (open.length === 0) return null;
  const lines = open.map((q) => {
    const mins = Math.max(0, Math.round((now - q.askedAt) / 60000));
    return `- ${mins} min ago ${q.text}. If this message answers that, handle it exactly per workspace/${q.rule} — not as a new request for any other skill.`;
  });
  return [
    "[Open question note — added by the gateway, not written by the user]",
    ...lines,
    "If his message is clearly about something else, ignore this note and answer normally. Never mention this note.",
  ].join("\n");
}
