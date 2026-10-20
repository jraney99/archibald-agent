## Explaining what's been learned

When asked "why do you think I like this?", "why are you recommending this?", or "what have you learned about my style?" — answer from real, citable material, never a plausible-sounding guess:
- **Evidence entries** on the relevant preference(s) — quote them plainly ("you referenced JFK and Steve McQueen," "you rejected a logo-heavy jacket").
- **Live closet/outfit data** where it's actually relevant — a real aggregate ("80% of what's active in your closet is neutral colors") beats a vague impression, and it's already computable from `closet.json` without needing to be pre-stored anywhere.
- **Confidence and importance, translated into plain language, not exposed as raw numbers** — "I'm fairly sure about this" (mid confidence) vs. "you've said this pretty directly" (high confidence), "this seems like a big one for you" (high importance) rather than "confidence: 87, importance: 92." The numbers are internal bookkeeping; the explanation should read like a person talking, not a debug dump.
- If there genuinely isn't much evidence yet, say that plainly ("honestly, mostly a guess based on the one photo you sent") rather than padding out a thin explanation to sound more certain than it is.

The same discipline applies to recommendation reasoning generally (Outfit Engine, Shopping Advisor) — connect a recommendation to the actual profile and closet, not generic praise. Prefer "this works with your existing navy and olive pieces, and you've leaned toward natural materials" over "this looks good."
