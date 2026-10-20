/*
 * reply-nudge — a typed REPLY to a meal nudge, logged without the agent.
 *
 * WHY THIS EXISTS (2026-09-18)
 * Button taps are deterministic (index.mjs). A typed meal was not: the agent
 * (Haiku, thinking off) guessed at commands — invented flags, logged a "shake"
 * the user never mentioned, saved "Burrito" as `taco`, and reported supplements as
 * confirmed without running their script. Dr. Example reads these logs.
 *
 * send_meal_reminder.py records each nudge's Telegram message id → (date, slot)
 * in meal_nudge_index.json. When an inbound message is a reply to one of those
 * ids, index.mjs runs confirm_meal.py --what "<his words>" itself and claims
 * the message, so the words are stored as typed and nothing is interpreted.
 *
 * Anything that is not a reply to an indexed nudge, or that reads as a question,
 * is left for the agent.
 */

import { readFileSync } from "node:fs";

export const NUDGE_INDEX_PATH = "~/.openclaw/meal_nudge_index.json";
const MAX_WORDS_LEN = 500;

export function loadNudgeIndex(indexPath = NUDGE_INDEX_PATH) {
  try {
    return JSON.parse(readFileSync(indexPath, "utf8"))?.messages ?? {};
  } catch {
    return {};
  }
}

// replyToId is the bare Telegram message id; replyToIdFull may carry a
// "<chat>:<id>"-style prefix. Accept either.
function candidateIds(event) {
  const ids = [];
  for (const raw of [event?.replyToId, event?.replyToIdFull]) {
    if (raw === undefined || raw === null || raw === "") continue;
    const s = String(raw);
    ids.push(s);
    const tail = /(\d+)$/.exec(s);
    if (tail && tail[1] !== s) ids.push(tail[1]);
  }
  return ids;
}

export function mealReplyArgv(event, messages = undefined) {
  const ids = candidateIds(event);
  if (ids.length === 0) return null; // not a reply — skip the file read entirely
  const words = String(event?.content ?? event?.body ?? "").trim();
  if (!words || words.length > MAX_WORDS_LEN || /\?\s*$/.test(words)) return null;
  const index = messages ?? loadNudgeIndex();
  const hit = ids.map((id) => index[id]).find(Boolean);
  if (!hit || !/^\d{4}-\d{2}-\d{2}$/.test(String(hit.date)) || !Number.isInteger(hit.slot)) return null;
  return [
    "nutrition/scripts/confirm_meal.py",
    "--date", hit.date,
    "--slot", String(hit.slot),
    "--action", "eaten",
    "--what", words,
  ];
}
