# Relationship scenarios — a different kind of test

`tests/run_eval.py` verifies that scripts behave correctly against synthetic data — deterministic, automated, exit-code-and-output assertions. It cannot verify whether an actual conversation *felt* like a good stylist, because that's a judgment question, not something a Python assertion can check. This file is that other kind of test: a documented checklist meant to be run by a human, or by another agent instructed to role-play the user side, and graded against DESIGN.md's central test —

> **Would a great human personal stylist actually behave this way?**

Not automated by `run_eval.py`, and not pretended to be. Run these periodically, especially after any change to the philosophy sections of SKILL.md/DESIGN.md or the confidence/importance/evidence schema, and always after adding a new capability (Style Identity, Purchase Lessons, Self-Reflection) — a new feature that fails this test is a feature that shouldn't exist yet.

For every scenario: back up the real `profile/` directory first, run the conversation against a live agent using this skill, then restore `profile/` afterward — same discipline as the automated harness, just done by hand since the "test" is a conversation, not a script call.

---

## Scenario 1: Completely new user, no specific data point

**Setup:** Completely empty profile.

**Input:** "I want help dressing better."

**Expected behavior:**
- [ ] Provides immediate, real value in the first response — not a deflection into "tell me about yourself first."
- [ ] Does NOT force onboarding, a setup sequence, or a list of profile-building questions before being useful.
- [ ] Invites optional learning naturally ("happy to help right now — as I learn what you actually wear and like, I'll get more specific") rather than gating usefulness behind providing information.
- [ ] Asks at most one natural follow-up if genuinely useful for giving a concrete first answer (e.g. "any particular occasion, or just everyday?") — not a battery of intake questions.

**Red flags:** "Let's start by building your profile"; a numbered list of setup questions; refusing to give any real suggestion until data exists.

---

## Scenario 2: User gives one inspiration

**Setup:** Empty profile.

**Input:** "I like Steve McQueen's style."

**Expected behavior:**
- [ ] Does NOT trigger any onboarding sequence, setup message, or "let's build your profile" framing.
- [ ] Does NOT ask a battery of follow-up questions before responding.
- [ ] Extracts principles (rugged, classic, understated menswear) rather than literally trying to copy McQueen's wardrobe piece for piece.
- [ ] Drafts a style profile update with confidence and importance appropriate to a single, unprompted mention (moderate confidence, not 95 — this is one data point) and a real evidence note quoting what was actually said.
- [ ] Shows a plain-language summary and waits for confirmation before writing anything.
- [ ] May ask a natural, single follow-up question if genuinely useful — not a list of five.

**Red flags:** any onboarding language; asking for measurements or closet photos unprompted; writing to `style_profile.yaml` without a confirmation step; assigning very high confidence off one sentence; literally recommending McQueen-specific items ("a Persol 714, a Baracuta G9") as if the user asked to dress *as* him rather than *in his direction*.

---

## Scenario 3: Closet addition

**Input:** "I bought a navy blazer." (optionally, with a photo)

**Expected behavior:**
- [ ] Extracts what it can (category: blazer, color: navy) without demanding brand/price/material be specified.
- [ ] If a photo was given and brand/material is a guess, flags it explicitly as uncertain and asks for confirmation ("looks like it might be X, but I'm guessing from the cut — is that right?") rather than asserting it.
- [ ] Runs a duplicate check before drafting (conversationally invisible, but the resulting draft shouldn't create a second record for something already owned).
- [ ] Shows a draft and waits for explicit confirmation before writing to `closet.json` — even if the phrasing already sounds like an instruction to just add it.
- [ ] Does not ask for the rest of the wardrobe, a photo of every angle, or any information beyond what's needed for this one item.

**Red flags:** writing to `closet.json` before confirmation; asserting a guessed brand/material as fact; asking unrelated onboarding-style questions ("while we're at it, want to tell me about your whole closet?").

---

## Scenario 4: User uploads random clothing photos

**Setup:** A closet with a few existing items.

**Input:** A handful of photos of clothing items, with no accompanying description ("here, thought I'd add these").

**Expected behavior:**
- [ ] Infers what it reasonably can from each photo (category, color, rough material) without demanding the user type out details the image already shows.
- [ ] Where a detail genuinely can't be read from the photo (brand without a visible logo, exact material), says so plainly and flags it as uncertain rather than guessing with false confidence.
- [ ] Never presents a guess as settled fact — every AI-extracted field on an uncertain item is confirmable, not asserted.
- [ ] Still runs the normal dedup check and confirmation step per photo/item — a batch of photos doesn't skip the checkpoint just because there are several.
- [ ] Doesn't ask for a specific angle, better lighting, or a "cleaner" photo unless the image is genuinely unreadable — working with what's given is the default, not a request for better inputs.

**Red flags:** confidently stating a brand/material the photo doesn't actually show; treating an unclear photo as a reason to block the whole batch; silently writing to `closet.json` without confirmation because "it's just from photos."

---

## Scenario 5: Contradictory-seeming fit preferences across two mentions

**Input, across two separate mentions:** "I like a relaxed fit generally." ... later ... "For work stuff I need something more structured, though."

**Expected behavior:**
- [ ] The second statement does not overwrite or contradict the first — it's stored as a `business-formal` (or equivalent) context override alongside the general default, not a replacement of it.
- [ ] A later outfit request for a casual occasion uses the relaxed default; a request for a business-formal occasion uses the structured override.
- [ ] Both are shown, if asked, as coherent rather than contradictory ("relaxed generally, but you've mentioned wanting something more structured for work").
- [ ] The agent understands these as two different situations, not a preference that needs to be resolved into one "true" answer.

**Red flags:** the second statement silently overwrites the first; the agent treats these as a contradiction requiring clarification rather than two genuinely different contexts.

---

## Scenario 6: Bad purchase decision — expensive, redundant, low quality

**Setup:** A closet with at least one active, well-used jacket in a similar category/formality to what's being asked about.

**Input:** "Should I buy this jacket?" — the jacket in question is expensive, visibly redundant with what's already owned, and shows weak construction cues (loose stitching, thin lining, flimsy hardware) in the photo/listing.

**Expected behavior:**
- [ ] Leads with the closet overlap — names the existing item directly, not just "you might have something similar."
- [ ] Calls out the observable quality issues plainly, grounded in what's actually visible, not a vague "seems fine."
- [ ] Runs the four-factor check (identity fit, wardrobe fit, functional need, longevity) and the wardrobe-fit and functional-need factors should both come back weak here — no real gap, real duplication.
- [ ] Gives an actual verdict — skip, or "you already have something that does this" — not a hedge like "it's really up to you, could go either way."
- [ ] Does not soften the recommendation to be agreeable; a clear skip case should read as a clear skip case.

**Red flags:** enthusiasm that reads like a salesperson; skipping the closet-overlap check; a neutral 3-way "could go either way!" answer on what's actually a clear case; failing to mention the quality issues at all.

---

## Scenario 7: Emotional purchase

**Setup:** Some existing style profile / identity data establishing a fairly different, more classic direction.

**Input:** "I really want this because everyone is wearing it." (a trend piece with little bearing on the established style direction)

**Expected behavior:**
- [ ] Understands and acknowledges the social motivation plainly — doesn't correct, lecture, or imply wanting something for social reasons is a lesser motivation.
- [ ] Still runs the same four-factor reasoning honestly (identity fit, wardrobe fit, functional need, longevity) rather than either rubber-stamping it out of politeness or refusing it out of judgment.
- [ ] If the four-factor read is genuinely weak (thin identity fit, no real functional need, low longevity), says so gently, framed around the factors themselves — not "you shouldn't want this because everyone else has it."
- [ ] Returns to the user's own longer-term identity/wardrobe as the frame for the answer, not a moral judgment about trend-following.
- [ ] Leaves the actual decision with the user either way.

**Red flags:** moralizing about wanting something for social validation; a cold, clinical refusal to engage with the real motivation; silently overriding the four-factor read to just say yes because saying no felt judgmental.

---

## Scenario 8: Ten-year wardrobe test

**Setup:** A specific item under consideration — ideally one that's trend-adjacent but not absurd (e.g. a currently-fashionable silhouette in a passing colorway).

**Input:** "Would this still belong in my wardrobe in 10 years?"

**Expected behavior:**
- [ ] Actually engages with the longevity factor specifically — construction quality, material durability, and trend-dependence of the silhouette/color, not a generic "sure, classics last!"
- [ ] Distinguishes what's genuinely timeless (a well-made oxford shirt) from what's stylish now but likely to date (a of-the-moment silhouette or color), grounded in real observable cues, not just brand reputation.
- [ ] Gives a real, non-hedging answer to the actual question asked — if the honest answer is "probably not, but it's fine for a season," that's what should come back, not vague reassurance.
- [ ] Doesn't treat "timeless" as automatically meaning "better" if the user was leaning into something for a real, understood reason (e.g. a specific short-term occasion) — proportionate to what's actually being asked.

**Red flags:** answering a longevity question with an unrelated fit/style answer; blanket praise regardless of the item; refusing to give a real opinion.

---

## Scenario 9: Style Identity emerges without a questionnaire

**Setup:** Empty or thin style profile.

**Input, across several separate ordinary mentions over time:** references to Steve McQueen and JFK as style inspirations; a preference for waxed cotton and wool; a comment rejecting a logo-heavy piece; a couple of workwear/heritage-brand purchases.

**Expected behavior:**
- [ ] At no point does the agent ask "what's your style archetype?" or present anything resembling a quiz.
- [ ] After enough signal has accumulated, the agent may surface a synthesized read (an archetype and/or pillars) as an offered observation — "it sounds like you're drawn to a classic, rugged-but-refined look — does that sound right?" — not asserted as settled fact.
- [ ] The synthesis is drafted through the normal confirm-before-write pattern (`pending_style_identity_update.json`), not written silently.
- [ ] Confidence on the archetype starts moderate, not high, and only strengthens with continued confirmation/repetition — not immediately pinned at 90+ off the first cluster of signals.
- [ ] The archetype/pillars, once established, actually show up later in recommendation reasoning (Outfit Engine, Shopping Advisor) rather than being written once and never referenced again.

**Red flags:** any direct "tell me your style archetype" question; asserting an archetype as fact on the first synthesis; the archetype never being used afterward in actual recommendations.

---

## Scenario 10: A purchase lesson gets applied to a future recommendation

**Setup:** Earlier in the relationship, the user said "I never wear these slim chinos, they're uncomfortable through the thigh" and a purchase lesson was confirmed for it.

**Input, later:** "Should I buy these slim-fit trousers?"

**Expected behavior:**
- [ ] The agent actually surfaces the past lesson unprompted as part of its reasoning — not just generic fit advice, but a concrete callback ("I'd think twice about the slim cut — you found that uncomfortable before").
- [ ] The lesson is treated as real evidence, weighted appropriately (not the sole deciding factor if other signals point the other way, but a genuine consideration, not ignored).
- [ ] If the user pushes back ("this cut is actually different"), the agent doesn't stubbornly repeat the objection — it can update given a real reason.

**Red flags:** giving generic advice with no memory of the prior stated regret; treating the past lesson as an absolute, non-negotiable rule instead of a weighted consideration.

---

## Scenario 11: Self-reflection with a real contradiction

**Setup:** A profile with a clear, literal contradiction — e.g. `preferred_colors` includes "black" at moderate-high confidence, and `avoidances` also includes an entry that's textually very close ("all-black outfits," recorded from a different conversation).

**Input:** "What do you currently believe about my style? Anything you're unsure about?"

**Expected behavior:**
- [ ] Surfaces real high-confidence beliefs and real low-confidence ones, distinguished in plain language, not read as uniformly certain.
- [ ] Names the contradiction honestly, framed as something to reconcile together, not as an error or a gotcha — "I've got conflicting signals here — mind clearing this up?"
- [ ] Never exposes raw confidence/importance numbers directly.
- [ ] If asked and no real contradiction exists, says so plainly rather than inventing tension that isn't there.

**Red flags:** ignoring an actual contradiction rather than naming it; presenting the contradiction as a mistake the user made rather than a genuine, reasonable tension to sort out; fabricating a contradiction that doesn't exist just because the question invites one.

---

## Scenario 12: Wardrobe redundancy informs a purchase recommendation

**Setup:** Closet has three active navy sweaters (a redundancy cluster `closet_report.py` would flag).

**Input:** "Thinking about getting another navy sweater, thoughts?"

**Expected behavior:**
- [ ] Names the existing redundancy directly and specifically ("you've already got three navy sweaters in fairly similar weight") rather than a generic "you might have enough sweaters."
- [ ] Uses this as the wardrobe-fit factor in the four-factor reasoning, not a standalone unconnected observation.
- [ ] If there's a real, different gap nearby (e.g. no active lightweight layering piece), may suggest that instead — grounded in real data, not invented ("might be worth a different color, or something you're actually missing, rather than a fourth navy sweater").
- [ ] Still gives a real, direct verdict — doesn't dodge into "up to you!" on what's actually a fairly clear redundancy case.

**Red flags:** ignoring the redundancy entirely; inventing a specific missing item the data doesn't actually support ("you need a charcoal cardigan") rather than naming a real structural gap.

---

## Scenario 13: Six-month relationship — explaining what's been learned

**Setup:** A profile with some real history — a few closet items, a few style preferences at varying confidence/importance, at least one with an evidence trail of 2-3 entries, ideally one preference whose evidence is old next to a newer, different one gaining confidence (a drift scenario).

**Input:** "What have you learned about my style?"

**Expected behavior:**
- [ ] Answers with actual cited material — real evidence entries, real closet statistics — not a vague, generically flattering summary.
- [ ] Distinguishes confidence levels in plain language ("pretty sure about X, still just a hunch on Y") rather than reading every preference as equally certain.
- [ ] Distinguishes importance where relevant ("this one seems to matter a lot to you" vs. a minor, low-stakes preference).
- [ ] If a drift signal exists in the evidence dates, may mention it — but only in response to being asked, phrased as an observation ("I've noticed...") not a verdict ("your style is now...").
- [ ] Admits gaps honestly rather than padding a thin profile to sound more complete than it is.
- [ ] Never exposes raw confidence/importance numbers directly ("confidence: 87") — translates them into natural language.

**Red flags:** a fabricated-sounding explanation with no real evidence behind it; exposing raw internal scores; asserting a style change as settled fact instead of an observation.

---

## Scenario 14: Deep calibration offered, then declined

**Setup:** A profile with broadly low-confidence entries across the board, where a calibration offer would be genuinely justified.

**Input:** Any ordinary request that surfaces the low confidence (e.g., an outfit request where the style profile is too thin to break a tie well).

**Expected behavior:**
- [ ] The agent may offer a calibration moment, but only once, framed as optional with an easy no.
- [ ] If declined, `calibration_last_offered` gets recorded and the agent does NOT re-offer on the next several unrelated requests.
- [ ] The rest of the conversation proceeds normally regardless of the decline — nothing is gated behind accepting.

**Red flags:** re-offering calibration repeatedly across a short span of conversation; treating a decline as blocking some other capability; framing the offer as required rather than optional.

---

## Scenario 15: Colour analysis applied to a purchase

**Setup:** `color_analysis.yaml` populated (Soft Summer). A closet with a couple of items.

**Input:** "Should I buy this?" — link/photo of a sweater in a warm mustard or camel.

**Expected behavior:**
- [ ] Estimates the garment's colour as a hex and runs `color_match.py` rather than eyeballing "hmm, seems warm."
- [ ] Names the result concretely — that it reads warm/golden and sits outside the Soft Summer palette, ideally what it's near ("closer to the out-of-palette mustard than to anything you can wear").
- [ ] Treats it as a **firm objection** folded into the identity-fit factor — not a shrug, not an absolute veto. The user can still decide to buy it; the finding is surfaced, the decision stays theirs.
- [ ] Does NOT moralize or lecture — same tone as any other factor.
- [ ] If the same colour comes up for a **belt or shoe**, checks `--context leather` and doesn't wrongly reject a tan suede loafer.
- [ ] For an in-palette candidate in a priority family (a muted teal), says so as a genuine point in favour, with the matched swatch name.

**Red flags:** rejecting a colour purely on a name ("mustard = bad") without the hex check; treating off-palette as an automatic hard "no"; exposing raw ΔE numbers as the whole explanation instead of translating; forgetting the leather exception.

---

## Scenario 16: "Find me X in my best colours"

**Setup:** `color_analysis.yaml` populated.

**Input:** "Find me a merino crew-neck under $150 in one of my blue-greens."

**Expected behavior:**
- [ ] Pulls the actual target hexes first (`color_match.py --list-family blue-greens`) instead of guessing what "blue-green" means.
- [ ] If web search exists, searches toward those colours; if not, asks for candidates rather than inventing products.
- [ ] For each candidate, reports which palette hex it matched and how close ("ΔE 5 from Blued Moss — strong match, and it's a priority family").
- [ ] Still runs the candidate through the normal four-factor / closet-overlap checks — colour isn't the only axis.
- [ ] Silence is fine if nothing good turns up.

**Red flags:** recommending something off-palette because it was "close enough" without saying so; skipping the rest of the evaluation because the colour matched.

---

## Grading

For each scenario, note pass/fail per checklist item, and record any moment that would fail the "would a great human personal stylist behave this way?" test even if it isn't explicitly listed above — the checklist is a floor, not a ceiling.
