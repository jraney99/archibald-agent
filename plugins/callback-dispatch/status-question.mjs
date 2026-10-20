/**
 * A typed question about what is logged TODAY — "how many meals have I had today?",
 * "what have I logged?", "did I take my vitamins?" — is answered by
 * dr-physician/scripts/today_status.py, before the agent turn, the same way a tap is.
 *
 * Why (2026-09-20 accuracy audit): asked "How many meals have I hit today?" on
 * 09-19, the agent replied "2 of 5 (Earlier meal logged)" in two seconds without
 * opening a file; the log held 1. Counts that came from a script were right 10/10,
 * counts from the model's head 0/4. The reply text was approved by the user 2026-09-20
 * ("Full list").
 *
 * Narrow on purpose (../GUARD-PATTERN.md): short messages that open with one of a
 * few question shapes and name no other day. Anything else — a past day, a longer
 * message, a question mixed with a report — goes to the agent, where ROUTING.md
 * points at the same script and claim-guard backstops an unbacked count.
 */

export const STATUS_ARGV = ["dr-physician/scripts/today_status.py"];
const MAX_LEN = 90;

// today_status.py only knows today. Any other day belongs to report_physician.py via the agent.
const OTHER_DAY =
  /\b(yesterday|last (?:night|week|month)|this (?:week|month)|past|since|(?:mon|tues|wednes|thurs|fri|satur|sun)day|\d{1,2}\/\d{1,2}|\d{4}-\d{2}-\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)\b/i;

const THING = "meals?|windows?|supplements?|vitamins?|breakfast|lunch|dinner|snacks?|water";
const SHAPES = [
  /^how many (?:meals?|(?:eating )?windows?)\b/,
  /^(?:what|which)(?: meals?| windows?)? (?:have i|did i|i've|ive) (?:logged|log|eaten|eat|had|hit|missed|miss)\b/,
  new RegExp(`^(?:did|have) i (?:take|taken|log|logged|have|had|get|gotten|drink|drunk|hit)\\b.*\\b(?:${THING})\\b`),
  /^how (?:much|many (?:oz|ounces)(?: of)?) water\b/,
  /^(?:what'?s|whats|show(?: me)?|give me) (?:my |today'?s )?(?:status|logs?|progress)(?: (?:for )?today| so far)?$/,
  /^(?:today'?s |my )?(?:status|progress)(?: today| so far)?$/,
];

/** STATUS_ARGV when the message is a today-status question, else null. */
export function statusQuestionArgv(event) {
  const raw = String(event?.content ?? event?.body ?? "").trim();
  if (!raw || raw.length > MAX_LEN || raw.includes("\n") || raw.includes("<media:")) return null;
  if (OTHER_DAY.test(raw)) return null;
  const text = raw.toLowerCase().replace(/[\s?.!\u{1F3A9}]+$/u, "").replace(/\s+/g, " ");
  return SHAPES.some((shape) => shape.test(text)) ? [...STATUS_ARGV] : null;
}
