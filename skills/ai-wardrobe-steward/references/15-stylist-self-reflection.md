## Stylist self-reflection

For "what do you currently believe about my style?", "what are you unsure about?", or a genuine check-in on the relationship's own progress:
```
python3 scripts/style_reflection.py
```
Read-only, computed entirely from `style_profile.yaml`, `style_identity.yaml`, and `body_fit_profile.yaml` — nothing new is stored. Reports high-confidence beliefs, still-uncertain ones, possible contradictions (literal text overlap between something liked and something avoided — a real signal worth naming, but not the only kind of tension that can exist; a subtler one noticed in conversation is just as valid even if this script wouldn't catch it), and entries that haven't been reconfirmed in a while.

Translate this into conversation the same way as "Explaining what's been learned" above — plain language, never raw scores, admit thin spots honestly. A contradiction is framed as an observation to reconcile, not an error: "I've got conflicting signals — you've mentioned liking classic footwear, but you've also saved a few minimalist sneakers recently. Which one's closer to where you're at?"

**On-demand only, same rule as everything else here** — never run this proactively as a scheduled check-in. The value is in having a grounded answer ready when asked, not in volunteering a self-assessment unprompted.
