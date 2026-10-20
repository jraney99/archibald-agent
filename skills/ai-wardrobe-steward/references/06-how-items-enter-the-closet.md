## How items enter the closet

There is no wardrobe upload step, batch or otherwise, that has to happen before this skill is useful. The closet grows exactly as fast as it comes up in conversation — "I bought these shoes," a photo shared because it was convenient, an afternoon spent deliberately cataloging everything at once. All of these are equally normal; none is the "right" way to build the inventory.

**Offering a photo catalog pass while the closet is still thin.** This is an *offer*, never a gate — no different in spirit from the deep-calibration offer below, just aimed at the closet instead of the style profile, and it never blocks anything else from working in the meantime. While the closet has only a handful of items (roughly under 10) and a wardrobe-relevant conversation is already happening, it's fine to suggest it once, with the actual reason attached: "since we're just getting your closet started, want to snap photos of everything you own? It'll go faster and be more accurate than describing each piece one at a time — totally fine to skip and just tell me about things as they come up instead." Before offering, check `closet_catalog_last_offered` in `style_profile.yaml` — if it's already been offered, don't repeat it (declined or ignored, once is enough; the closet either grows from here through ordinary conversation, or through a batch the user brings on their own initiative). Record the offer regardless of the answer:
```
python3 scripts/confirm_style_update.py   # after writing {"review_summary": "offered a closet photo catalog", "set": {"closet_catalog_last_offered": "<today's date>"}} to pending_style_update.json
```
Never suggest this once the closet has real depth to it already — at that point the closet is growing the normal way, and repeating the offer would be exactly the kind of nagging check-in this skill's design rules out elsewhere.

**If accepted, give practical shooting tips up front** — this is what actually makes the batch fast to process instead of full of guesses and back-and-forth:
- One item per photo, not a pile or a full closet-rack shot — extraction (category/color/material/brand) is per item, so one garment per frame is far more reliable than parsing several out of one image.
- Any visible tag/label facing the camera helps (brand, size, care label) — saves a field from being a flagged guess, but isn't required.
- Decent light is enough; no need to stage it — flat, on a hanger, or worn all work fine, this isn't a catalog photo.
- Send them all in one go, no need to space them out — every photo in the batch gets drafted before anything is shown for review, so there's no back-and-forth per item.
- A one-word usage note per item if it's easy ("wear the boots constantly, the vest maybe never") — feeds `estimated_frequency` (see "Capturing a self-reported usage baseline" below) so pre-owned items don't read as "never worn" until wear-tracking catches up.

Drafting is fully automatic, whether it's one item or a batch of several — no back-and-forth mid-process. Only the final commit needs a checkpoint, and it's one lightweight step regardless of how many items are involved:

1. **Extract or collect the fields for every item mentioned, in one pass**, without pausing to ask anything. For manual mentions, use what was said directly. For a photo, identify category/color/material/visible brand yourself. For a receipt (email, screenshot, PDF), extract brand/price/date. If several items came in at once (a batch of photos, a multi-item receipt), process all of them before moving on. If an actual image file was shared (not just described), copy it to `profile/photos/closet/<id>.jpg` (preserving the original extension, using the slugified id the record will get) and set the draft's `photo` field to that path now, at draft time. If the draft later gets rejected, the copied file becomes a harmless orphan `check_photos.py` will catch — a fine trade for not needing to defer the copy. If `color_analysis.yaml` is populated and the item has a clear single colour, estimate its hex, run `python3 scripts/color_match.py --hex "<hex>"` (`--context leather` for shoes/belts/bags), and set `palette_match` on the draft from the result (`{hex, name, family, delta_e, verdict}`); leave it `null` otherwise. Don't stop the draft over an off-palette result — note it in the review summary so it comes up at confirmation ("FYI this mustard scarf is outside your palette — still want it logged?").
2. **Mark uncertainty on the record itself; don't stop to ask about it mid-draft.** Anything read from a photo or receipt is a guess, not a fact — the agent may infer, never assume (DESIGN.md universal rule 1). Set `flagged_fields` to the guessed field names (e.g. `["brand", "model"]`) and put a plain-language note in `notes` (e.g. "brand guessed from the pocket layout and stitching, couldn't see an interior label — is that right?"). This surfaces the guess honestly at review time instead of blocking the draft now.
3. **Run the de-dup check for each item before drafting it:**
   ```
   python3 scripts/closet_dedup.py --category <category> --brand "<brand>" --color <color> [--purchase-date YYYY-MM-DD]
   ```
   A match at or above ~0.7 means this is probably an existing item, not a new one — note that in the review summary (e.g. "looks like it might already be item X — update instead of add?") rather than silently drafting a duplicate.
4. **Append the drafted record(s) to `profile/pending_closet_additions.json`**, each with `review_summary` (one plain-language line) and `flagged_fields` added on top of the normal item shape. Assign `id` by slugifying brand+category (e.g. `jcrew-navy-blazer`), disambiguating with `-2`, `-3` if taken.
5. **Present one consolidated summary, in conversation, not a form or a JSON dump** — "3 pieces ready to add: J.Crew navy blazer, Red Wing boots (brand's my best guess from the toe shape), Patagonia vest. Look right?"
6. **Wait for confirmation before anything is committed** — this is not optional, even when the user's phrasing already sounds like an instruction to just add it. The confirmation *is* what turns a hypothesis into memory (DESIGN.md universal rule 1).
   - Confirm one: `python3 scripts/confirm_closet_item.py <id>`
   - Confirm everything queued: `python3 scripts/confirm_closet_item.py --all`
   - Reject one: `python3 scripts/cancel_closet_item.py <id>`
   - Reject everything queued: `python3 scripts/cancel_closet_item.py --all`
   - View what's waiting any time: `python3 scripts/confirm_closet_item.py --list`

   Confirming moves the item(s) into `profile/closet.json` and runs `validate_closet.py` automatically — report its result plainly. If validation fails, the write already happened; fix the issue in `closet.json` directly and re-validate rather than leaving it broken.

### Basics (socks, underwear, undershirts, sleepwear)

The `basics` category group exists so the closet can hold foundation garments for **inventory, replacement, laundry, and spend** tracking — not so the stylist starts composing around them. They intake exactly like anything else (draft → `pending_closet_additions.json` → confirm), with a few defaults:

- **`formality`: 1**, **`occasion_tags`: `[]`** — basics aren't occasion-specific. Setting formality avoids them cluttering `closet_report.py`'s "missing formality" note.
- **Batch them by type, not one at a time** — "I own maybe 20 pairs of black no-show socks, 5 white crew" is a reasonable single record each with a `notes` count, or a handful of records if they genuinely differ (dress socks vs. athletic vs. no-show). Use judgment; don't make the user photograph 20 identical socks.
- **`estimated_frequency`** — set it (`regularly` etc.) since these are pre-existing, so they don't read as "never worn."
- The Outfit Engine, Travel Packing, and Shopping Advisor's identity/colour reasoning **ignore this group by design**. `closet_report.py` skips it in duplicate/redundancy detection (many identical pairs is correct). `care.py` and `closet_value.py` **do** count them — laundry aggregates and total spend are the point.
- Don't run `color_match.py` / set `palette_match` on basics unless one has a genuinely notable colour worth checking.

### Capturing a self-reported usage baseline

`wear_count` only starts counting from the moment an item enters the closet — it has no way to know an item was actually worn constantly for the three years before it got catalogued. For anything genuinely new (bought recently, being added the day it arrived), `wear_count: 0` is simply correct and `estimated_frequency` should stay `null`. But for a **pre-existing** item entering the closet as part of a catalog pass (a photo of something already owned, a deliberate "let me tell you everything I own" session), ask a light version of the question in the same batch review moment — "roughly how often do you wear this — regularly, occasionally, rarely, or basically never?" — and set `estimated_frequency` on the draft from the answer, using `vocab.yaml`'s `usage_frequency_estimates`. This is a one-time, self-reported fact captured at intake, not something the agent infers or revises later; `closet_report.py`'s usage section reads it to avoid calling something "never worn" when it's actually just untracked so far.

## The photo on an order-email intake (2026-10-13)

`propose_closet_addition.py` used to attach a photo only when the model found an image URL
in the order email. In practice that never happened: all 145 items in the closet came from
The user's own photos (127) or from receipts (18), none from an order email, because those
links are usually tracking pixels, thumbnails or behind a login.

The intake now looks the item up in the store's own catalog first — `catalog_lookup.py`,
over the 25 stores in `config/stores.json` that publish one — and takes the store's studio
image, product URL and list price from there. The email's image URL is the fallback. The
record keeps `photo_source` ("store catalog" or "order email") and `product_url`, and a
price found this way clears `purchase_price` from `flagged_fields`.

The lookup is conservative: the retailer must resolve to a known store (short forms like
"H&B" and "S&L" are aliases) and the title must share at least two distinctive words, so
a near miss returns nothing — a wrong photo in the closet is worse than no photo. Shade
words ("heathered", "light", "vintage") are ignored when matching a colourway, since
"Heathered Navy" and "Heathered Spruce" are different joggers. `WARDROBE_NO_NETWORK=1`
skips the lookup entirely, and any failure leaves the intake unaffected.

**Consequence worth knowing:** catalog photos are studio shots on white, while the older
closet photos are on a duvet or carpet. Boards will look mixed until the old ones are
replaced, and the user has said he is not reshooting them.

**Measured on the existing closet (2026-10-13), before anyone proposes a backfill:** of 118
active items, 73 are from brands with no public catalog, and of the 45 from catalog brands only **8** still match a product the
store sells today — a catalog carries the current season, not what he bought two years ago.
So studio photos arrive with NEW purchases and will not take the wardrobe over quickly. A
bulk backfill is not worth running, and the first attempt at one is exactly how the matching
bugs below were found:

- a licensed edition outscored the plain garment (a university-licensed "Jackson Hoodie" for
  his plain one) — licensed titles are now skipped, not merely penalised;
- a sibling colourway was accepted ("Navy & Slate Microstripe" for his "Navy & Whiskey
  Dogleg" Fischer belt) — a named colourway must now appear on the matched product;
- shade words ("heathered") decided a colourway — they are ignored;
- colour words alone satisfied the two-word threshold — only non-colour words count.
