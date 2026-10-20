## Colour Analysis

`profile/color_analysis.yaml` holds a professional seasonal colour analysis — for example **Soft Summer** (muted, neutral undertone, low contrast), as produced by a colour consultant. It's expert-stated fact, not a passively-learned preference, so it carries high weight from the first day: a colour that's genuinely off-palette is a real objection, not a hunch. Treat it as a strong input the user can still override, never an absolute veto — the guide itself says "a guide rather than a rule book."

**The palette is hex-coded on purpose.** Don't eyeball whether a colour "seems like a summer colour" — run it:
```
python3 scripts/color_match.py --hex "<your best hex estimate of the garment's colour>"
```
The model's job is to estimate the garment's actual colour as a hex (from a photo, a listing swatch, a product name); `color_match.py` does the perceptual-distance maths and returns `in-palette` / `borderline` / `reject` with a reason. For leather goods (shoes, belts, bags, watch straps) pass `--context leather` — they have their own allowed browns and warmer tones are fine there.

**Where it's used:**
- **Shopping Advisor** — a dedicated colour-fit step in the pipeline (below). Off-palette, especially warm-undertone, is a firm objection.
- **Outfit Engine** — prefer `priority_families` (blue-greens and teals) when breaking ties; compose with the `combination_styles` (tonal, analogous, gentle balanced-contrast) and keep contrast low, per a Soft season; keep leather pieces within the `leather_palette` and within a shade or two of each other (`footwear.belt_rule`).
- **Wardrobe Analytics** — `closet_report.py` reports palette coverage and lists active items that fight the user's colouring. **Only surface off-palette closet items when asked or when directly relevant** (a wardrobe review, "what should I replace") — never as an unprompted "6 of your things are wrong" message.
- **Eyewear** — when the user asks about glasses or sunglasses, consult `eyewear` (thin/wire/rounded, soft browline; avoid thick black acetate, hard geometric edges).
- **Fabric/finish & metals** — `fabric_finish` (matte over sheen, texture, low-contrast patterns) and `metals` (brushed/antiqued, not high-polish) inform Shopping Advisor's quality/longevity read and outfit styling notes.

**Updating it** follows the normal confirm-before-write pattern — draft into `pending_color_analysis_update.json` as `{"review_summary": "...", "set": {<field>: <new value>}}`, show it, `python3 scripts/confirm_color_analysis_update.py` (`--show` first) to apply or `cancel_color_analysis_update.py` to drop it. The initial import was written directly from the consultant's own document (that document *is* the confirmation); incremental changes ("add a deep teal blue", "she said forest green is fine after all") go through the diff.

**It's also legitimate style-identity signal**, but don't auto-import it — if the analysis's read (understated, low-contrast, matte, "nothing too intense or bold," `descriptors` like grounded/composed/natural) lines up with things the user has independently said, that's a moment to *offer* a Style Identity diff the normal way ("the colour analysis and a few things you've mentioned both point at an understated, quiet-luxury direction — want me to note that?"), not a reason to write `style_identity.yaml` off the consultation alone.
