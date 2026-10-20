## Activity-specific garment policies

Occasion answers how formal an outfit should be. Activity answers what the wearer will actually do. Always pass both when known:

```
python3 scripts/outfit_score.py --occasion smart-casual --activity golf-round
```

`vocab.yaml` defines stable activities, activity phrases, slot/category constraints, and advisories. `scripts/activity_policy.py` adds item-level policy. Effective policies distinguish:

- `preferred_activities`: a ranking advantage, not an exclusion elsewhere.
- `allowed_activities`: the valid set when an item is exclusive.
- `excluded_activities`: hard prohibition even when formality matches.
- `activity_exclusive`: the item is filtered from every activity outside its allowed set.

Use `activity_policy.py set` only from explicit user direction or unambiguous functional identity, and preserve the evidence. Do not declare every polo golf-only merely because it can be worn golfing.

### Personal activity preferences

`vocab.yaml` is the generic convention. How *this* wearer dresses for an activity lives in `profile/activity_preferences.json` and wins over it:

```
python3 scripts/activity_policy.py prefer --activity golf-simulator --occasion athletic \
  --slot base_top=t-shirt --slot bottoms=shorts --slot footwear=athletic_shoes --evidence "<his words, dated>"
python3 scripts/activity_policy.py prefer --activity golf-round --slot-temp bottoms:80:pants:shorts \
  --guidance "<soft judgment the filters cannot express>" --evidence "<his words, dated>"
python3 scripts/activity_policy.py heat-rule --limit-f 85 --dressy-indoor-min-formality 3 --evidence "<his words, dated>"
python3 scripts/activity_policy.py venue --phrase "example sim lounge" --activity golf-simulator --evidence "<why>"
python3 scripts/activity_policy.py preferences
```

- `prefer` sets the occasion tier and/or the allowed categories per slot for one activity. `outfit_candidates.py` / `outfit_score.py` apply it automatically whatever `--occasion` was passed, and say so in `warnings`. Do not argue with it or style around it.
- `--slot-temp slot:threshold_f:below:at_or_above` picks a slot's categories from the forecast. It only works when the caller passes `--feels-like-f`; without it the scripts warn and fall back. For that slot the forecast outranks an item's calendar-season tag.
- `--guidance` is surfaced verbatim in `warnings` as "Personal guidance for <activity>". Treat it as the wearer's instruction for the judgment call among the ranked results (layers, blazer, "look up the restaurant first").
- `heat-rule` removes long-sleeve tops (closet field `sleeve`) at or above the limit, except for dressy indoor events: formality at/above the floor, not `--outdoor`, and not a `physical_activities` activity.
- **Always pass `--feels-like-f` from the real forecast, and `--outdoor` when the event is outside.** Both rules above are inert without them.
- `venue` teaches the calendar scan that a named place means a specific activity subtype, so it is not guessed per event.
- Pass `--explicit-occasion` only when the invitation itself states a dress code; a stated dress code beats the personal default.
- Record a preference only from the user's explicit direction ("that's the wrong kind of outfit for X", "for X I wear Y"), with dated evidence. One disliked proposal is a style signal, not a preference.
- Activities listed under `climate_controlled_activities` in `vocab.yaml` ignore `--season`: a summer-tagged short is fine in an indoor bay in November.

Golf subtypes are materially different (generic defaults — a personal preference above overrides them):

- `golf-round`: collared polo, golf-compatible bottoms, actual golf footwear; verify course rules.
- `driving-range`: flexible golf clothing; golf footwear optional.
- `golf-simulator`: clean sneakers/athletic shoes; golf shoes normally excluded.
- `golf-clubhouse`: club-appropriate smart casual; golf shoes excluded unless venue context explicitly permits them.
- `golf-spectator`: walking comfort and weather protection; playing equipment excluded.

Calendar parsing uses the longest matching activity phrase so “clubhouse dinner” does not collapse into generic `golf-round`. If the subtype remains ambiguous and changes the outfit, ask one concise question.


## The golf-round look (The user, 2026-10-13)

Workshopped over four reference images he supplied from the Walker Cup and Presidents Cup.
Recorded in `profile/activity_preferences.json` → `activities["golf-round"].guidance`, which
`outfit_candidates.py` surfaces to every golf brief.

**What he means, and what he does not.** He asked for "the classic style of the Walker Cup
uniforms and the colors" — the tailoring and the palette, NOT team merchandise. The skill had
read his team-merchandise pieces as evidence of the taste; that was wrong, and he
corrected it. No lettering, no flag prints, no large logos.

**The formula, which appeared in all four images:** a KNIT LAYER WORN OVER A COLLAR — crewneck,
V-neck, sweater vest or quarter-zip, with the shirt collar showing — plus tailored trousers in
mid-grey or navy, clean white shoes and a cap. The neckline varies; the move does not.

**Texture, not pattern.** Rib, cable, tipping at a neckline. Soft Summer is low contrast, so bold
pattern fights him while texture does the same work quietly. It also matches his standing
avoidances (no loud prints, no visible logos).

**Palette.** Cream and bone first — the first two entries in his colour analysis — then navy,
heather grey and muted blue-greens. **A dusty, almost salmon red is allowed; a bright true red is
not.** Asked directly, he chose "only a muted, dusty red". This is the one golf exception to the
colour analysis and it is his explicit call, not an inference.

**The two gaps this exposed**, both already P1 in wardrobe-plan.md §2 and now on the watchlist
(`watch-2026-10-13-001`, `watch-2026-10-13-002`):
- a fine-gauge crewneck or V-neck to layer over a collar — he has 1 against a target of 5;
- a KNIT sweater vest with contrast tipping — he owns four vests and every one is quilted or
  technical, none is knitwear.

Everything else in those images he already owns: fine-stripe polos (6), tailored navy trousers
(3), quilted vests (4), white golf shoes.
