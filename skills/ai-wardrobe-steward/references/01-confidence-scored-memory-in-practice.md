## Confidence-scored memory, in practice

DESIGN.md defines the principle; this is how to actually apply it whenever you write to `style_profile.yaml` or `body_fit_profile.yaml`. Every stored preference is `{"value": ..., "confidence": 0-100, "importance": 0-100, "evidence": [...]}` — never a bare value. Three independent dimensions, each answering a different question:

**Confidence — "how certain is this true?"**
- **Stated directly** ("I hate skinny jeans," "I'm a size 40R in blazers") → 85-95.
- **A consistent pattern across multiple independent signals** (three navy purchases, never a comment against it) → 55-75.
- **A single indirect signal** (one photo, one offhand remark, one guess from context) → 30-50.
- **Reconfirmed** — the same value comes up again → raise the existing confidence rather than adding a duplicate entry. `confirm_style_update.py`'s `add` and `confirm_body_fit_update.py`'s `set`/`add` do this merge automatically — passing only the fields that changed preserves everything else (including evidence and, for fit preferences, other contexts).

**Importance — "how much should this weigh in a recommendation, independent of how sure I am it's true?"** A trivial-but-certain fact (always wears black socks) shouldn't carry the same weight as a core identity trait (prefers classic over trendy), even at equal confidence. A flatly-stated dealbreaker ("I refuse to wear logos") should start at high importance immediately, even on message one, regardless of how confidence builds — importance tracks emphasis and stakes, confidence tracks certainty, and they can diverge from the very first mention.
- Casually mentioned, low-stakes → 30-50.
- A real, standing preference → 55-75.
- Explicitly emphasized, or something that would be a genuine problem if ignored → 80-95.

**Evidence — the trail that actually built the confidence**, capped at the 5 most recent observations per preference (older ones drop off automatically). This is what makes "why do you think that?" answerable with something real instead of an agent fabricating a plausible-sounding reason after the fact — never assert an explanation you can't back with an actual evidence entry or closet/outfit data. A short, dated note each time: `{"date": "2026-07-13", "note": "referenced JFK and Steve McQueen as style inspirations"}`.

**Fit preferences additionally support per-context overrides** — someone can prefer relaxed fits by default but need something structured for business-formal. Context keys are `vocab.yaml`'s `occasion_tiers` (the same vocabulary the Outfit Engine already filters on, not a new parallel concept):
```json
"fit_preferences.tops": {
  "value": "relaxed", "confidence": 85, "importance": 60,
  "contexts": {"business-formal": {"value": "classic", "confidence": 70, "importance": 80}}
}
```
When composing an outfit or evaluating a purchase for a specific occasion, check whether a context-specific override exists before falling back to the default value.

**Low confidence is what triggers a natural clarifying question — folded into a conversation already happening for another reason, never a standalone "let me ask you some questions."** If a low-confidence entry never becomes relevant to anything, it's fine for it to just sit there unresolved indefinitely; nothing forces it to be cleared. Don't manufacture a reason to ask about it.

This does not apply to closet item fields (brand, material, etc.) — those go through synchronous confirm-before-write during Intake instead (a guess gets flagged and confirmed *before* it's saved at all, so there's no need for a lingering confidence number on a closet record the way there is on a passively-accumulated taste preference). Two different mechanisms for two different situations, not an inconsistency: closet extraction gets resolved immediately; style/fit understanding is allowed to stay a hypothesis for a while.
