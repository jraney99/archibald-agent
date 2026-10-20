---
name: ai-wardrobe-steward
description: Manage closet inventory, outfits, style and fit preferences, purchase advice, shopping watchlists, care, and travel packing. Use for clothing purchases, what to wear, wardrobe feedback or reports.
---

# AI Wardrobe Steward

This is a relationship, not an application. There is no onboarding flow, no setup session, no percentage-complete profile, no screen the user is ever asked to visit. Understanding accumulates from ordinary conversation over weeks and months — "I bought this jacket," "I like how JFK dressed," "these pants didn't fit" — the same way a good human stylist gets to know a client, not the way an app collects a user profile.

**The test for every judgment call below: would a great human personal stylist actually behave this way?** A stylist doesn't hand a client a form before agreeing to help, doesn't require a full closet inventory before making a suggestion, and doesn't call every day to check in. If an instruction here would fail that test, that's a flaw in the instruction, not a reason to follow it anyway.

**All 11 DESIGN.md modules are built**: Style Identity, Body/Fit Profile, Digital Closet, Intake, Outfit Engine, Shopping Advisor/Personal Shopper, Wardrobe Analytics, Care & Maintenance, Travel Packing, Continuous Learning, and Colour Analysis. The headings below are how this skill's engine is organized *internally*, for the sake of building and maintaining it coherently — the user never sees a module boundary, is never asked which one they want, and never experiences a "switching modes" moment. A single "should I buy this?" can draw on the closet, the style profile, the body/fit profile, and the purchase log in one exchange; that's invisible plumbing, not a workflow anyone steps through.

**Read `DESIGN.md` first.** Its philosophy — confidence-scored memory, passive learning vs. optional deep calibration, silence as correct behavior, the anti-patterns list — governs every section below, not only the ones that mention it by name.

**Before changing any script, run `python3 tests/run_eval.py`** (and again after) — it's a self-contained regression suite covering every module's major behaviors and edge cases against synthetic data, restoring the real `profile/` directory automatically. Several real bugs in this skill were only ever found by actually running scripts against adversarial data, not by reading code — this harness is what keeps that from being a one-time audit.


Load only the matching procedure below. Never invent owned items, purchases, measurements, fit or availability. Research can recommend; it cannot purchase. For watchlist cron scans use the bounded cron instructions and Shopping Advisor reference, not the entire library.

Calendar outfit automation is event-scoped and deduplicated. Explicit attire text may be mapped automatically; inferred mappings must be labeled. A proposal is not a wear. Only a post-event confirmation records wear history. Never propose for cancelled events, never contact an event attendee, and never purchase or reserve a product.

**Fresh installations:** run `scripts/init_profile.py` when the private profile files do not exist. Do not launch a setup wizard. Work immediately from available information, and offer at most one optional situational calibration question when a real recommendation is uncertain. For thermal comfort, run `calibration.py` with the actual feels-like temperature, wind, rain, activity and outdoor duration; store only the user's explicit answer. A skipped question is not evidence.

**Automated outfit selection:** use `outfit_score.py` after `outfit_candidates.py`. The scorer ranks only complete, active-item combinations; the model may apply contextual judgment among the top results but may not bypass required slots or invent availability. Use `comfort_profile.py predict` for layering. Low-confidence predictions justify one concise question, not a questionnaire.

**Activity-specific use:** when an event implies an activity, pass both occasion and the normalized `--activity` to the candidate/scoring scripts. Read [20-activity-policies.md](references/20-activity-policies.md). Hard exclusions and activity-exclusive items override general formality similarity. Distinguish playing golf, practicing, simulator golf, clubhouse meals, and spectating before choosing clothes. The user's own stated way of dressing for an activity (`profile/activity_preferences.json`) overrides the generic mapping; when they correct the *type* of outfit for an activity, record it with `activity_policy.py prefer`.

**Showing item photos:** use `python3 scripts/show_photos.py <item-id(s)>` (or `--search`). **Showing a proposed outfit:** use `python3 scripts/outfit_board.py <item-id(s)> --title "<occasion>"` to create one real JPEG flat lay from the exact closet photos. Relay the script's `MEDIA:` output verbatim. If board creation fails, run `show_photos.py` for the same IDs and relay those individual images instead. Never generate an HTML page, use canvas/browser workarounds, or claim a photo was sent unless the chosen script actually emitted a `MEDIA:` line.

## Task References
- Confidence-scored memory, in practice: [01-confidence-scored-memory-in-practice.md](references/01-confidence-scored-memory-in-practice.md)
- Explaining what's been learned: [02-explaining-what-s-been-learned.md](references/02-explaining-what-s-been-learned.md)
- Noticing style evolution: [03-noticing-style-evolution.md](references/03-noticing-style-evolution.md)
- Files: [04-files.md](references/04-files.md)
- Item record shape: [05-item-record-shape.md](references/05-item-record-shape.md)
- How items enter the closet: [06-how-items-enter-the-closet.md](references/06-how-items-enter-the-closet.md)
- Lifecycle state discipline: [07-lifecycle-state-discipline.md](references/07-lifecycle-state-discipline.md)
- Style Identity: [08-style-identity.md](references/08-style-identity.md)
- Colour Analysis: [09-colour-analysis.md](references/09-colour-analysis.md)
- Body & Fit Profile: [10-body-fit-profile.md](references/10-body-fit-profile.md)
- Outfit Engine: [11-outfit-engine.md](references/11-outfit-engine.md)
- Travel Packing: [12-travel-packing.md](references/12-travel-packing.md)
- Shopping Advisor & Personal Shopper: [13-shopping-advisor-personal-shopper.md](references/13-shopping-advisor-personal-shopper.md)
- Wardrobe Analytics: [14-wardrobe-analytics.md](references/14-wardrobe-analytics.md)
- Stylist self-reflection: [15-stylist-self-reflection.md](references/15-stylist-self-reflection.md)
- Care & Maintenance: [16-care-maintenance.md](references/16-care-maintenance.md)
- Queries: [17-queries.md](references/17-queries.md)
- Continuous Learning: [18-continuous-learning.md](references/18-continuous-learning.md)
- Automation, calibration, and purchase lifecycle: [19-automation-calibration.md](references/19-automation-calibration.md)
- Activity-specific garment policies: [20-activity-policies.md](references/20-activity-policies.md)
- Wear reports ("What did you wear instead?" replies): [21-wear-reports.md](references/21-wear-reports.md)
- Which events get an outfit, forecasts and finished briefs: [22-event-triage-and-briefs.md](references/22-event-triage-and-briefs.md)
- Boards, buttons, purchases, photos, packing and the Sunday line: [23-boards-buttons-and-daily-flow.md](references/23-boards-buttons-and-daily-flow.md)

## Output Mode
Reports from this skill follow the one shared presentation standard:
`workspace/Documentation/rules/33-output-mode.md`, rendered with
`Skills/shared/output_mode.py`. Do not restate the layout here and do not
hand-space rows — `closet_report.py` renders through it.
