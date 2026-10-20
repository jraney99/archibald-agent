/**
 * Operational-state claims: "your cron failed", "the daemon is running fine".
 *
 * WHY (2026-10-07): claim-guard caught "I did X" and nothing else. Asked why a cron had
 * failed, Archibald replied "it was trying to auto-check this appointment and hit an
 * error" — a diagnosis of a job that does not exist, for a failure that never happened.
 * The user checked, it did not hold up, and the reply had sailed through the guard because
 * it claimed nothing SAVED. Finance had already hit this wall ("October is reconciled"
 * is a state claim too) and solved it by demanding a sanctioned reader in the same turn.
 * This is that rule, for how the machine itself is doing.
 *
 * BOTH DIRECTIONS. Invented reassurance is as costly as an invented failure: on the same
 * day, three crons were broken and the weekly job that would have named them was one of
 * them, so "everything looks fine" would have been both comforting and wrong.
 *
 * THE TEST IS "DID YOU LOOK", NOT "DOES THE TEXT MATCH". A claim about a component needs
 * a successful read of THAT component this turn — cron claims need a cron read, calendar
 * claims a calendar read. Matching the words of a claim against a reader's output was
 * rejected deliberately: status vocabularies differ between tools, so textual support
 * would misfire on honest answers constantly, and a guard that cries wolf gets ignored.
 * This catches fabrication, which is the failure that actually happened; it does not
 * catch misreading real output. accuracy-check-weekly is the backstop for that.
 */
import { normalizedFinanceText as normalize } from './finance-claims.mjs';

// Each component: what naming it in a sentence looks like, and what reading it looks like.
const COMPONENTS = [
  {
    id: 'cron',
    cue: /\b(?:crons?|cron ?jobs?|scheduled jobs?|schedulers?|nightly jobs?|weekly jobs?)\b/,
    reader: /\bopenclaw\s+cron\b|\bcron_(?:list|status)\b|\bopenclaw\.sqlite\b|\bcron_jobs\b/i,
  },
  {
    id: 'calendar',
    cue: /\b(?:calendars?|your schedule)\b/,
    reader: /\bfetch_calendar\.py\b|\bgog\s+cal\b|\bfetch_tomorrow_cal\.py\b/i,
  },
  {
    id: 'email',
    cue: /\b(?:email daemon|mail daemon|email agent|inbox|gmail|email monitor)\b/,
    reader: /\bemail-agent\.log\b|\bemail_monitor_state\.json\b|\bgog\s+gmail\b|\blaunchctl\b|\bdaemon\.py\b/i,
  },
  {
    id: 'service',
    // "the daemon" is ambiguous here — in this deployment it usually means the email
    // daemon, sometimes the gateway — so any genuine service read satisfies it,
    // including the email daemon's own log. Over-accepting slightly beats blocking a
    // true answer: a guard that fires on honest replies is one the user learns to ignore,
    // and accuracy-check-weekly still counts anything that slips past.
    cue: /\b(?:gateway|daemons?|services?|launchd|openclaw itself)\b/,
    reader: /\blaunchctl\b|\bopenclaw\.log\b|\bgateway\.log\b|\bemail-agent\.log\b|\bopenclaw\s+(?:status|doctor|health)\b/i,
  },
  {
    id: 'tests',
    cue: /\b(?:tests?|test suite|the suite)\b/,
    reader: /\btest-all\.mjs\b|\bunittest\b|\btest_[a-z0-9_]+\.py\b|\bnode\s+\S*test\b|\bpytest\b/i,
  },
];

// Working, or not working. A claim either way needs a look either way.
const BROKEN = /\b(?:fail(?:s|ed|ing|ure|ures)?|error(?:ed|s)?|broke|broken|down|dead|crash(?:ed|ing|es)?|stuck|hung|wedged|stopped|silent|timed out|not running|never ran|misfired)\b/;
const HEALTHY = /\b(?:running|ran fine|fine|healthy|working|works|ok|okay|clean|green|passing|passed|succeeded|successful|operational|up and|no errors|all good|nothing (?:is )?wrong|look(?:s|ing) (?:fine|good|healthy))\b/;

// A clause that asserts nothing about the present — exempt, and each for its own reason.
const EXEMPT = [
  [/\?\s*$/,                                              'a question'],
  [/^(?:if|unless|should|were|had|in case|assuming|suppose)\b/, 'a condition, not a claim'],
  [/\b(?:would|could|might|may|whenever|any time|used to)\b/, 'hypothetical or habitual'],
  // Contractions arrive here already flattened (see splitClauses), so "can't" is "cant".
  [/\bi (?:havent|hadnt|didnt|dont|cant|cannot|couldnt|wont)\s+(?:yet\s+)?(?:check|checked|verify|verified|look|looked|confirm|confirmed|tell|say|know)\b/, 'explicitly unchecked'],
  [/\b(?:not sure|no idea|unclear|unknown|i dont know|worth checking|need to check|needs checking|let me (?:check|look)|im checking|checking (?:now|whether|if)|without looking|havent looked)\b/, 'stated uncertainty or work in progress'],
  [/\b(?:will|wo ?n[o']?t|is going to|runs?|scheduled to|next run)\b[^.!?]{0,40}\b(?:sunday|monday|tuesday|wednesday|thursday|friday|saturday|tomorrow|tonight|next|at \d|\d{1,2}(?::\d\d)?\s*(?:am|pm))\b/, 'a future schedule'],
  [/^(?:run|check|try|look at|open|see)\b/,               'an instruction to the user'],
  [/\b(?:you said|you mentioned|you asked|according to you|per your)\b/, 'attributed to the user'],
  [/\b(?:a|an|any) (?:failing|broken|healthy) \w+ would\b/, 'a generic example'],
];

/** Flatten contractions so one spelling of a negation is enough to match.
 *  "can't" is c-a-n-'-t, so a pattern written as `can ?n[o']?t` — which reads correctly
 *  for "didn't" and "haven't" — silently fails on it, and "I can't tell if the cron
 *  failed" gets flagged as a fabricated diagnosis. Dropping the apostrophe between two
 *  letters makes every contraction one plain word. */
const deContract = s => s.replace(/(\p{L})['\u2018\u2019\u02bc](\p{L})/gu, '$1$2');

function splitClauses(text) {
  return deContract(normalize(text))
    .split(/(?<=[.!?])\s+|\n+|\s*(?:;|\bbut\b|\bhowever\b|—|–)\s*/u)
    .map(x => x.trim()).filter(Boolean);
}

/** Successful reads in this turn, as the set of component ids they cover. */
export function componentsRead(turn) {
  const calls = new Map();
  for (const m of turn ?? []) {
    if (m?.role !== 'assistant') continue;
    for (const b of Array.isArray(m.content) ? m.content : []) {
      if (b?.type !== 'toolCall') continue;
      const text = [b.arguments?.command, b.arguments?.cmd, b.arguments?.path,
                    b.arguments?.file_path, b.arguments?.filePath]
        .filter(Boolean).map(String).join(' ');
      if (!text) continue;
      const ids = COMPONENTS.filter(c => c.reader.test(text)).map(c => c.id);
      if (ids.length) calls.set(b.id, ids);
    }
  }
  const covered = new Set();
  for (const m of turn ?? []) {
    // A read that FAILED proves nothing — that is how nine dead gog calls looked like
    // clean ones for four days (feedback-daemon-launchd-bare-env).
    if (m?.role !== 'toolResult' || m.isError || !calls.has(m.toolCallId)) continue;
    for (const id of calls.get(m.toolCallId)) covered.add(id);
  }
  return covered;
}

/** Clauses asserting a component's operational state, with the component named. */
export function stateAssertions(text) {
  const out = [];
  for (const clause of splitClauses(text)) {
    const exempt = EXEMPT.find(([pattern]) => pattern.test(clause));
    if (exempt) continue;
    if (!BROKEN.test(clause) && !HEALTHY.test(clause)) continue;
    // EVERY component the clause names, not just the first. "Your crons are fine and
    // the daemon is healthy" is one clause asserting two things; stopping at the first
    // let a cron read vouch for a daemon nobody had looked at.
    for (const component of COMPONENTS) {
      if (component.cue.test(clause)) {
        out.push({ component: component.id, clause: clause.slice(0, 100) });
      }
    }
  }
  return out;
}

export function stateClaimDecision(event, turn) {
  const text = String(event?.lastAssistantMessage ?? '');
  if (!text.trim()) return;
  const assertions = stateAssertions(text);
  if (!assertions.length) return;
  const covered = componentsRead(turn);
  const unbacked = assertions.filter(a => !covered.has(a.component));
  if (!unbacked.length) return;
  const names = [...new Set(unbacked.map(a => a.component))].join(', ');
  return {
    action: 'revise',
    reason: `state-claim: asserted how ${names} is doing with no successful read of it in this turn`,
    retry: {
      instruction:
        `This reply states whether ${names} is working or broken, and nothing in this ` +
        `turn actually looked. Run the reader and answer from its output — ` +
        `"openclaw cron list --all" for jobs, "fetch_calendar.py" for the calendar, ` +
        `the daemon log for email, "launchctl list" for services, the test suite for ` +
        `tests — or say plainly that you have not checked. Never explain a failure you ` +
        `have not seen: inventing a cause is worse than saying you do not know.`,
      idempotencyKey: 'state-claim',
      maxAttempts: 2,
    },
  };
}
