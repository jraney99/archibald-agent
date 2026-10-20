## Continuous Learning

The actual promise here isn't tracking objectively (wear counts, dates, prices) — it's getting better at recommending for *this* person because of what actually worked, the way an ongoing relationship with a stylist would.

**Ask at exactly three moments — never on a schedule, never after every outfit:**
1. **A first wear.** `outfit_history.py log`'s output flags this explicitly.
2. **A low-confidence outfit suggestion** — `outfit_candidates.py`/`travel_packing.py` returned warnings, or there was only one real option (no genuine alternative to choose between).
3. **A reaction volunteered unprompted** — always capture it, no gating, this one isn't sampled.

Outside these three, don't ask. Asking "how did that feel?" after every routine outfit is exactly the failure mode this whole design exists to avoid.

**Recording it:**
```
python3 scripts/outfit_history.py feedback <outfit-id> --note "<what was said>" [--favorite | --unfavorite]
```

**Route feedback that reveals something durable to where it actually belongs**, updating confidence rather than treating it as a one-off note:
- A style preference ("felt so put together in navy and olive") → a Style Identity diff, confirmed the normal way — reinforce the existing entries' confidence if they already exist, or add new ones.
- A fit/sizing fact ("these ran small, should've sized up") → a Body & Fit diff for `brand_sizing`, confirmed the normal way.
- A purchase regret ("I never wear these," "this brand always runs weird on me") → a Purchase Lesson (see Shopping Advisor above), confirmed the normal way — this is what lets the *next* similar recommendation actually avoid repeating the mistake.
- Nothing beyond "liked/disliked this specific outfit" — the `feedback`/`favorite` fields on that one entry are enough.

Confirmed-wear feedback lives in `outfit_history.json` and, when it is durable, gets folded into the confidence-scored profiles everything else already reads from. `style_signals.json` is only a staging ledger for low-confidence proposal reactions; it is never a second source of truth about established preferences.

Calendar proposal responses are provisional evidence rather than durable preferences. `profile/style_signals.json` holds these low-confidence accept/reject signals and `scripts/style_learning.py` aggregates them. A planned outfit is not evidence of wear. A confirmed wear is logged in `outfit_history.json`, and only a repeated pattern or explicit statement may be drafted into the main profile through the existing confirm-before-write flow. This keeps inspiration, purchase, planned wear, actual wear, and direct feedback distinct.

### Behavioural learning loop (2026-09-19)

Three layers, from automatic to explicit. Never skip a layer upward on your own.

1. **Evidence → adjustment (automatic, silent, fades).** `learned_adjustments.py` recomputes score nudges on every `outfit_score.py` run from `style_signals.json` (button taps) and `outfit_history.json` (confirmed wears, now tagged with `activity`). Nothing is stored; each outfit's `learned` list says why a piece moved ("called too formal for dining on …", "you have actually worn it for …"). Use those reasons when the user asks why something stopped appearing. A `too-warm`/`too-cold` tap only counts when the proposal recorded `--feels-like-f` and today's forecast is similar.
2. **Repeated evidence → proposed rule (needs his yes).** `rule_proposals.py` — on the second "too formal"/"too casual" tap for the same activity on a *different* outfit within 60 days, the tap's reply becomes a one-tap question ("Make it a rule…?"). ✅ saves it through `activity_policy.py prefer --merge`; ❌ means those were one-offs. It never asks twice in 60 days for the same activity and direction, never for `general`, and never when there is no sensible tier to move to. You do not send this yourself — it is deterministic.
   **Ground truth comes from the board itself** (references/23): ✅ Wore it / 👕 Wore something else sit under every board (the separate 9:30pm question was retired 2026-09-28); "Wore something else" asks what he wore (references/21). A bare photo of him dressed is the other source (rule 35).
3. **Rule (explicit).** `activity_policy.py prefer|venue|heat-rule` with dated evidence. When the user corrects the *type* of outfit for an activity in conversation, record it immediately — that is explicit direction, not a pattern to wait on.

Always pass `--activity` to `outfit_history.py log` and `--feels-like-f` to `outfit_proposals.py create`; without them layer 1 has nothing to learn from.
