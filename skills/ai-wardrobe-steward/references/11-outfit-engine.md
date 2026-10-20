## Outfit Engine

Generates outfits from what's actually `active` and in-season right now — never from memory of what's probably in the closet. The filtering (formality range, season, lifecycle state) is deterministic and handled by a script; composing the actual outfit (which top with which bottom, color coordination, styling notes) is a judgment call made from that real, filtered list.

1. **Gather occasion and weather.** For conversation, ask when either is missing. For calendar automation, `calendar_outfit_candidates.py` provides the event and inferred occasion; the scheduled agent must fetch the event-location forecast and state any dress-code inference in the proposal. Tentative/cancelled events, duplicates, and events without attire/context signals are not proposed automatically.
2. **Get real candidates:**
   ```
   python3 scripts/outfit_candidates.py --occasion <tier> [--season <season>]
   ```
   If it returns warnings (e.g. no active bottoms match), say so plainly — don't paper over a thin closet by inventing an item that isn't there or isn't `active`.
   The returned `requirements` are mandatory. Business-formal and black-tie outfits require a full-body piece, base shirt, and footwear; a suit never silently substitutes for its shirt. If a required slot is missing, report the gap rather than sending a complete-looking board.
3. **Check recent history for the repeat-vs-favorite tie-break:**
   ```
   python3 scripts/outfit_history.py recent --days 14
   ```
   If the same combination was worn very recently and is NOT marked `favorite`, avoid recommending that exact combo again — suggest a different one from the candidates instead. If it IS a favorite, repeating it is fine; favorites are exempt from the cooldown (DESIGN.md's tie-break rule).
4. **Compose the outfit from the candidate list only**, using `style_profile.yaml`'s preferred colors/materials/persona/avoidances and `style_identity.yaml`'s archetype/pillars — weighted by both confidence and importance (a high-importance, high-confidence avoidance or pillar should dominate; a low-importance hunch is a mild tiebreaker at most) — to break ties between multiple valid options, and `body_fit_profile.yaml`'s fit preferences (checking for an occasion-specific `contexts` override before the default). Present the primary suggestion plus 1-2 alternatives, with reasoning that names the actual preference or pillar it's drawing on ("this leans into the rugged-heritage direction you've been building, and works with your preference for natural materials") — not generic praise like "this looks good."

   `base_top` and `layering_top` are not alternatives to each other — a sweater is normally worn *over* a shirt, not instead of one. When weather or formality calls for a layering piece, combine one `base_top` with one `layering_top`. Two `base_top` items (e.g. two shirts) genuinely are alternatives — only pick one.

   **Color harmony is a soft tiebreaker among candidates, not a filter.** When `profile/color_analysis.yaml` is populated, it's the stronger guide: prefer candidates whose colour sits in a `priority_families` family (blue-greens, teals), lean on the `combination_styles` (tonal — one colour at three depths; analogous; gentle balanced-contrast) and keep overall contrast **low** for a Soft season — no stark light/dark clash. Keep leather pieces (shoes, belt, bag, watch strap) within the `leather_palette` and within a shade or two of each other. When there's no colour analysis on file, fall back to `vocab.yaml`'s generic `color_families` (`neutral`/`warm`/`cool`): a neutral base plus at most one non-neutral accent over two competing saturated colours. Either way, mention colour reasoning only when it was actually the deciding factor ("navy and off-white keeps this tonal, with the teal knit as the one accent") — don't narrate colour theory when it wasn't the reason for the pick.
5. **Log it once the user confirms what they're actually wearing** (or logs a past outfit after the fact):
   ```
   python3 scripts/outfit_history.py log --occasion <tier> --items <id1,id2,...> [--date YYYY-MM-DD]
   ```
   The confirmation of which outfit is actually happening *is* the checkpoint here — no separate pending/confirm step, since this is recording a stated fact, not committing an AI guess.
   After logging, run `mark_after_wear.py --items <ids>` so one-wear washable categories become `dirty`; multi-wear garments remain active until their care rule is due or the user marks them dirty.

For calendar proposals, "Plan this" only reserves the outfit in `profile/outfit_proposals.json`; it does not count as a wear. After the event, ask once whether it was actually worn. Only the confirmed post-event response calls `outfit_history.py log`. Rejections become low-confidence entries in `style_signals.json`; three or more consistent behavioral signals may be proposed as a profile update, never silently promoted into a durable preference.
6. **Let a favorite get marked any time** — `python3 scripts/outfit_history.py favorite <outfit-id>` — so it can be intentionally repeated without tripping the cooldown.

## The belt (2026-10-13)

The scorer fills `base_top`, `bottoms` and `footwear` and nothing else, so for a year no board named a belt — the user owns over a dozen, including woven golf belts, and asked why no belt was listed on a golf board. `scripts/belt_pick.py` chooses one AFTER the outfit is chosen (a belt answers to the shoes and the bottoms; adding 13 more combinations to the ranking would multiply the search for nothing). `propose_from_brief.py` appends it to the proposal items and records `meta.belt`; `outfit_swap.py` re-picks it after a bottoms or shoes swap. It returns NO belt, with a reason, for athletic/loungewear, for bottoms whose notes say elastic, drawstring, jogger, sweat, training or swim, and when nothing clearly suits. Rules: woven for golf and casual, dress leather otherwise, colour-matched to a leather shoe, and never black under a light shoe. Tests: `tests/test_belt_pick.py`.


## Pinned looks reach the scorer (2026-10-13)

59 described Pinterest LOOKS and `closet_looks.py` existed for weeks, and `outfit_score.py`
had never heard of either — the thing that picks what he wears ignored the only record of
what he likes the look of. `scripts/pinned_looks.py` is the join: each pinned look is mapped
to the closet ids it matches (same field-aware matcher `closet_looks.py` uses), and a
candidate outfit that shares pieces with one gets a bonus, cited in `learned` as
`source: pinned-look` with the pin id, so a board can say WHY.

Shaped deliberately:
- **Resemblance, not reproduction.** Two shared pieces is a match, because what he pinned is
  a combination (navy knit over a light collar, grey trousers) and the point is to favour the
  pairing, not to copy a photograph.
- **Best single look, never the sum.** An item that appears in many pins would otherwise
  stack a large bonus on its own.
- **Capped at 14**, roughly one strong item-level term, so inspiration tilts a close call and
  never overrides palette, occasion or a learned correction.
- A look that maps to fewer than two closet pieces is dropped, and one piece in common is not
  a resemblance.
- The lookup is wrapped: if anything fails, `pinned=[]` and proposals carry on. Inspiration is
  a bonus, never a blocker.

Test: `tests/test_pinned_looks.py`.
