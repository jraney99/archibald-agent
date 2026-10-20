## Wear reports — "What did you wear instead?"

Every board carries **✅ Wore it / 👕 Wore something else** (since 2026-09-28; the separate 9:30pm question is retired). If the user taps **👕 Wore something else** he is asked *"What did you wear instead? A few words or a photo is plenty."* and `profile/pending_wear_report.json` is written. While that question is open the gateway adds an "Open question note" to your turn naming the proposal id. His next message about clothes is the answer. It is the most valuable thing this skill ever learns: what he really wears to that activity.

**Do this, in order:**

1. Read `profile/pending_wear_report.json` — `proposal_id`, `event_title`, `activity`, `proposed_items`.
2. Match what he says (or what the photo shows) to real closet ids — `python3 scripts/show_photos.py --search "<words>"`, or read `profile/closet.json`. **Never invent an id and never guess between two similar items** — if "grey tee" could be two pieces, ask which, once.
3. Record it:
   ```
   python3 scripts/outfit_proposals.py actual --id <proposal_id> --items <id1,id2,...> --note "<his words>"
   ```
   - Pieces that are **not catalogued** (gym shirts, joggers) go in `--note` in his words; leave them out of `--items`. `--items ""` is valid when nothing he wore is catalogued.
   - The script logs the wear against the event's activity, marks one-wear items dirty, and clears the pending file.
4. Reply with one line saying what was logged, ending 🎩. **Never say it was logged unless the script ran this turn and its output says so.**
5. If he named uncatalogued pieces, offer **once** to add them (normal intake rules, references/06). Do not start an inventory session.

**Do not:** propose a new outfit, re-argue the original proposal, or ask why. If his message is clearly about something else, answer that and leave the pending file alone — the question simply expires.

What happens next is automatic: `learned_adjustments.py` favours what he actually wore for that activity, and penalises the combination he passed over. If he corrects the *type* of outfit in words ("I never wear polos to the simulator"), that is explicit direction — record it with `activity_policy.py prefer` (references/20), don't wait for a pattern.

## Logging a wear with no board open (2026-10-13)

Wear history sat empty for a month — 145 items, 0 wears — and it was never a broken
writer: `outfit_history.py log` works end to end. The only road to it was a calendar
outfit board, and triage is deliberately narrow (references/22), so three boards
existed in that month and all three were skipped. Everything he wore on an ordinary
day was invisible, which left cost-per-wear, the §2a wear ceilings and his own "I want
to wear my whole closet" goal with nothing to measure.

When he simply says what he wore, with no board open and no `pending_wear_report.json`:

```
python3 scripts/log_wear.py --text "<his words>" [--activity <vocab activity>] [--occasion <tier>] [--date YYYY-MM-DD]
```

It splits his sentence, matches each phrase against ACTIVE closet items field by field
— a colour, brand or category he names must agree — logs the ones it is sure of, and
**reports the rest instead of guessing**. A wrong id is worse than no record, because
`outfit_history.py` bumps `wear_count` on whatever it is handed. Relay what it printed,
ask which item only for the phrases it listed as ambiguous, and never pass an id it did
not name. ROUTING.md row: "Wore it (typed)".
