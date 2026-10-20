## Shopping Advisor & Personal Shopper

One judgment engine, two entry points: evaluating a specific item someone found, and searching for something to fill a stated need. Per the stewardship-over-consumption principle, **the default on a close call is "skip" or "you already have something close" — not a neutral buy/consider/skip split.** This is a purchase advisor, not a sales assistant, and it never behaves like one just because it can search the web.

**If `references/wardrobe-plan.md` exists, read it first.** It carries a finished-wardrobe target set, a build sequence, a budget frame, a per-category **wear-capacity ceiling** (§2a), and a per-user spending posture that can *override* the close-call default. For a user whose documented failure mode is under-buying and agonising (money-mindset section of that file), a candidate that fits identity + palette + fills a gap on the target set + is within budget + clears the wear bar gets a plain **"yes, buy it"** — no hedging, no "do you really need it," no "wait for a discount." The four-factor reasoning below still runs; it just isn't weighted toward abstention for that user. The close-call-skip default still applies to impulse buys, anything off the plan, redundancy, off-palette, and poor fit.

**The ceiling gates BUYING only — never culling (wardrobe-plan.md §2b, the user 2026-10-13).** Say plainly when a category is at or over its ceiling before recommending an addition; never propose selling what he already owns to get under one, and never list over-capacity categories in a scheduled message. Belts are exempt entirely.

**The wear-capacity ceiling overrides permission-first.** Before recommending an *addition* to a category, check the active count in that category against the ceiling in §2a (wear-days available per year ÷ the minimum wears an item needs to earn its place). At or over the ceiling, an addition doesn't add wardrobe — it displaces a wear from something already owned. Say so plainly and treat it as one-in-one-out at best, usually a skip, *even for a user on the permission-first posture* — permission-first applies to filling gaps under the ceiling, never to exceeding it. This is the concrete form of the user's own stated goal: get real wear out of what he already owns.

**Entry point A — "should I buy this?"** A link, screenshot, or description of a specific item.
**Entry point B — "find me a ___"** A stated need with no specific item in mind. If web search/fetch tooling is available, use it to find real candidates matching the style profile and budget; if not, ask for candidates to be brought (a link, a store) rather than inventing products that may not exist or be priced/stocked wrong. When colour matters to the request, pull the target colours first — `python3 scripts/color_match.py --list-family blue-greens` (or `--families`) — and search toward those hexes; then run every candidate through step 4 below and report which palette hex it matched and how close ("this one's ΔE 4 from Blued Moss — a strong match in a priority family").

For either entry point:

1. **Get the item's details.** For A: read the link/screenshot/description — category, brand, price, material, visible construction. For B: once candidates exist, same extraction. Flag uncertainty the same way Intake does — a quality/material read from a photo or listing copy is a guess, not a fact. **If the brand has a reference file (e.g. `references/holderness-bourne.md`), read it first** — it carries the fabric axis and the fit model for that brand.
2. **Check the closet for overlap, with real data:**
   ```
   python3 scripts/shopping_closet_check.py --category <category> [--formality <n>] [--season <season>]
   ```
   This is the load-bearing step. If it returns existing items — active or not — that fact drives the recommendation more than anything else: an already-owned, well-used item in the same role is usually reason enough to skip, and a retired/needs-repair item in the same category is relevant history worth naming.
3. **Check for a past lesson about this kind of item:**
   ```
   python3 scripts/purchase_lessons_lookup.py --keyword "<category or item description>" [--lesson-type fit|brand|color|quality|lifestyle|other]
   ```
   A real match here (e.g. a recorded lesson that slim chinos didn't work) is a concrete objection, not a vague hesitation — name it directly ("I'd think twice about the slim cut — you found that uncomfortable before"), the same way a stylist remembers a past mistake instead of repeating it.
4. **Check the colour against the palette** (skip only if `color_analysis.yaml` isn't populated, or the item genuinely has no single colour):
   ```
   python3 scripts/color_match.py --hex "<best hex estimate of the item's colour>"    # --context leather for shoes/belts/bags
   ```
   Estimate the garment's actual colour as a hex first — from the product photo, a listing swatch, or the colour name — then let the script score it. **A `reject` verdict, especially a warm-undertone one, is a firm objection** — state it plainly and name what it matched against ("that mustard reads warm-golden; it's outside your Soft Summer palette — closest thing you can wear is nothing near it"). `borderline` is worth a mention, not a blocker. `in-palette` and a `priority` family (blue-greens, teals) is a genuine point in favour. The user can still choose to buy an off-palette piece — surface the finding, don't veto.
5. **Reason about quality from what's actually observable** (stitching, material, hardware, silhouette, construction) — not brand-reputation priors, which go stale. If the read is genuinely just "this brand generally makes X," say that plainly as background, not a verdict. Weigh `color_analysis.yaml`'s `fabric_finish` here too — a hard sheen or a high-contrast busy print works against a Soft season regardless of construction quality.
6. **Project cost-per-wear using real numbers where they exist.** If `shopping_closet_check.py` returned a comparable item's `realized_cost_per_wear`, use it. If there's nothing comparable, say so rather than inventing a figure.
7. **Run the four-factor reasoning check before giving a verdict** — this is what separates "this looks cool" from "here's why this belongs in your wardrobe":
   - **Identity fit** — does it match `style_identity.yaml`'s archetype/pillars, or sit closer to the `anti_identity` list? Cite the actual pillar, don't just assert a vibe ("this leans more trend-driven than the classic, rugged direction you've been building toward"). Fold the step-4 colour verdict in here: an off-palette colour is an identity-fit problem with an expert behind it.
   - **Wardrobe fit** — does it complement or duplicate what's already active? Pull from step 2's closet-overlap check and, when a fuller picture is useful, `python3 scripts/closet_report.py`'s redundancy-cluster and coverage-gap sections — three navy sweaters already active is a real objection even if this one is technically "different."
   - **Functional need** — is there an actual, recurring gap this fills, or is the "need" thin? A confirmed watchlist entry or a real coverage gap counts; "I just want it" is a want, not a need, and should be named as such rather than dressed up as one.
   - **Longevity** — would this still make sense in years, not just this season? Weigh construction/material against trend-dependence; a piece that only works with a specific micro-trend is a weaker recommendation regardless of how it scores on the other three factors.

   Weigh each factor by the confidence/importance of whatever it's citing (a high-confidence, high-importance identity pillar is a real objection; a low-confidence hunch is a mention, not a verdict). Not every purchase needs all four factors spelled out at length — a clear case doesn't need a paragraph — but the reasoning behind a recommendation should be traceable to at least one of them, never "this looks good" on its own.
8. **For fit-critical categories** (anything sized — shirts, suits, pants, footwear):
   ```
   python3 scripts/brand_sizing_lookup.py --brand "<brand>" [--category <category>]
   ```
   If there's no entry yet, say so rather than guessing a size — and treat this as a moment to capture one going forward once fit gets mentioned.
9. **Give a direct recommendation** — buy / skip / "you already have something close" — reasoning stated plainly, not buried. Ties go to skip or already-own.
10. **Log the recommendation immediately** — this is what makes "fewer unnecessary purchases" and "more intentional buying" measurable instead of asserted:
   ```
   python3 scripts/purchase_decisions.py log --category <category> --recommendation <buy|skip|already-own> --reasoning "<one-line reasoning>" [--item-description "..."]
   ```
   Writes immediately, no confirmation needed — it's a record of advice given, not a claim about the user.
11. **When it later comes up what actually happened** ("ended up buying it anyway," "skipped it like you said"): `python3 scripts/purchase_decisions.py outcome <decision-id> --action <bought|skipped> [--linked-item <closet-item-id>]`. Link it to the resulting closet item if one gets added later — this is what lets an override (advised skip, bought anyway) surface in `purchase_decisions.py report [--days N]` rather than disappearing.

**Emotional purchases get the same reasoning, not a colder one.** "I want this because everyone's wearing it" is a real motivation, not something to correct or lecture about — acknowledge it plainly, then still walk through the four factors honestly. If it holds up (identity fit is fine, no real duplication, reasonable longevity), say so and let them buy it happily. If it's purely trend-driven and thin on the other three factors, name that gently and let the four-factor read (not judgment about *why* they want it) do the work: "totally get the appeal — just flagging it's pretty trend-specific and doesn't overlap with the rest of what you wear, so it might have a short shelf life. Your call." Never moralize about wanting something for social reasons; that's not the stylist's place, and a real stylist wouldn't do it either.

**When a purchase turns out to be a mistake** ("I never wear these," "these never fit right," "this brand always runs weird on me"), that's a lesson, not just a fact about one item — draft it the same confirm-before-write way as anything else:
```
{"id": "lesson-<slug>", "lesson_type": "fit|brand|color|quality|lifestyle|other", "item_description": "...", "linked_closet_item_id": "<id or null>", "lesson": "...", "confidence": 0-100, "evidence": [{"date": "...", "note": "..."}]}
```
Append to `profile/pending_purchase_lessons.json`, confirm with `python3 scripts/confirm_purchase_lesson.py <id>` (or `--all`), discard with `python3 scripts/cancel_purchase_lesson.py <id>`. A lesson matching an existing one (same `lesson_type` and a similar `item_description`) reinforces it rather than duplicating — same merge discipline as everything else. This is what makes step 3 above possible on the *next* similar purchase — the whole point of remembering mistakes instead of just recording them.

### Watchlist & proactive scanning

Entry point B answers one request. The watchlist is for "keep looking" — things that don't need to be re-asked every time.

**Adding to the watchlist** — two paths, both direct writes (the ask, or the confirmation, is the checkpoint):
- Asked directly ("let me know if a good navy overcoat under $400 shows up") → `python3 scripts/shopping_watchlist.py add --category <cat> [--max-price N] [--formality N] [--season <s>] [--brands "A,B"] [--notes "..."]` (source defaults to `user-requested`).
- A genuine gap surfaces from `closet_report.py` → **offer** to watch for it rather than just reporting it passively ("no business-formal footwear in the closet — want me to keep an eye out?"). Only add it on confirmation; a reported gap isn't automatically a want. Use `--source gap-detected`.

**Running a scan** — triggered directly ("check my watchlist") or by a recurring external trigger if the host environment has one:
1. `python3 scripts/shopping_watchlist.py list --status active`.
2. For each entry, if web search/fetch tooling is available, search for real candidates matching its criteria — merge its `brands` with `style_profile.yaml`'s `preferred_brands` if the entry didn't specify its own. If no search tooling exists, skip silently rather than asking the user to search their own watchlist.
3. Run anything found through the exact same evaluation as Entry point A — the watchlist is a different way of feeding the judgment engine, not a second judgment path.
4. **Only surface genuinely good finds. Silence is fine and expected** if a scan turns up nothing worth mentioning — repeatedly reporting "still nothing" is exactly the nagging failure mode this skill avoids everywhere else. Log anything actually surfaced via `purchase_decisions.py log`.
   Before surfacing or rejecting a candidate, record its exact retailer/product/color/size/URL snapshot with `product_history.py seen`. Consult prior records so the same unchanged item is not recommended repeatedly. A page failure is source-health uncertainty, never evidence that a product disappeared.
5. `python3 scripts/shopping_watchlist.py touch <id>` after checking an entry, found or not.
6. When something gets bought that satisfies a watch entry: `python3 scripts/shopping_watchlist.py fulfill <id> [--linked-item <closet-item-id>]`. `cancel <id>` if it's no longer wanted.

**Store catalogs as data (2026-10-12).** `config/stores.json` is the user's store list: one canonical slug per store, its domain, how its catalog is read (`shopify` / `page` / `none`) and every alias the scan has ever typed for it. For a `shopify` store, `scripts/store_catalog.py` reads the store's public `/products.json` (no login, no key, the same endpoint the storefront calls) and answers with facts instead of page impressions:

```
python3 scripts/store_catalog.py stores                                   # what is watched and how
python3 scripts/store_catalog.py search --category polo --size L --color navy --in-stock [--on-sale] [--max-price N] [--store SLUG] [--json]
python3 scripts/store_catalog.py changes --all [--category polo --size L] [--mark]   # price_drop / back_in_stock / new_product since the last --mark
python3 scripts/store_catalog.py probe <domain>                           # does a new store expose the catalog?
```

Use `search` BEFORE any web search for a watchlist entry whose brands are shopify stores: it gives exact colourway, per-size stock and `compare_at` sale price, which is what `product_history.py seen` wants (`--sku`, `--price`, `--status in-stock|out-of-stock`). A `page` store is still a web read. A store that answers with a block or a challenge is recorded through `source_health.py` and left alone until the next scheduled scan; nothing here solves challenges or logs in. The catalog cache lives in `state/store_catalog_cache.json` (throwaway, refreshed after 6 h). Adding a store = one entry in `stores.json` after a `probe`; the learning layer is not involved.

**Waiting for a store discount channel (2026-10-12).** A store in `config/stores.json` may carry a `discount_channel` (`rate`, `status`, an expected return date, an optional usage limit). Wants for that store can carry `criteria.wait_for_discount: true` and `criteria.urgent: false`: watch them for colour/size changes and markdowns, never recommend them at retail unless `urgent` is true (a discount is otherwise a bonus, never a prerequisite — see wardrobe-plan.md §1). When the user says the channel is back, run `python3 scripts/hb_batch.py` and relay its list: every waiting want searched live in his size, list price, the price at the channel rate, and the first-pick total, so the batch can be bought in one go before the limit resets. Never on a schedule.

**Store sale habits (measured 2026-10-12 across 38,869 sale variants).** "Percent off" means a different thing per store: H&B, johnnie-O, Criquet, Alden, Gitman, Randolph almost never discount (any markdown is news); State & Liberty, Del Campo, Darn Tough, Rancourt run light occasional sales (20%+ is an event); Faherty, Todd Snyder, Taylor Stitch, American Giant have half the catalog "on sale" at 40–50% all the time (a deal starts at 55%+ or a 90-day low). Judge a discount against the store's own habit, not a flat number. Alert rules beyond the Mon/Thu scan are NOT yet approved.

**Closing the loop (2026-10-12).** `purchase_lifecycle.py add` (a purchase read from an order email) now calls `purchase_match.py`: the open recommendation with the same retailer and item words gets `user_action: bought`, and the first active want of that category and brand is fulfilled — a fact, so no confirmation. `update --status delivered` calls `send_fit_prompt.py`, which asks "📦 <item> from <retailer> arrived. How's the fit?" with 👍 / 👌 / 👎 (`fit_callback.py`: verdict on the purchase + a dated brand-sizing evidence line; 👎 is what makes the next size suggestion at that brand carry the warning). The prompt is HELD (recorded, not sent) until `config/wardrobe_prompts.json` `fit_prompts_enabled` is true — the user approves the wording first. Both prompts and the Sunday digest wait while he is away (`current_location.is_away_from_home`).

**Cart links, price history, 90-day lows.** `store_catalog.py search --size <s>` rows carry `cart_url` (Shopify cart permalink: one tap to checkout with that variant; the agent never pays) and `variant_ids` per size. Every fetch records price changes per variant (`price_history` in the store cache, change-only, capped); rows carry `low_90d` / `is_90d_low`, and `changes` emits `ninety_day_low`. A "low" needs a week of history, so a first fetch never cries wolf.

**Shop the closet first.** `closet_looks.py` matches every pinned LOOK against active closet items: looks he can make now, looks one piece away, and the single missing piece that unlocks the most looks (a candidate want). `plan_progress.py [--message]` scores the closet against wardrobe-plan.md §2 / §2a (category map is heuristic; the plan's own "Have" column is the human read). `cost_per_wear.py [--sell]` = real $/wear from purchase_price × wears (outfit_history + wear_count) and retired items still unsold; it says plainly when there is no wear data.

**The feedback LOOP is closed (2026-10-13, second pass).** The thumbs were first wired to WRITE a style signal that nothing read: `learned_adjustments.py` filters to source `outfit-proposal-response`, so shopping taps accumulated and changed nothing. Now a find carries its brand, material and MEASURED colour (`send_find.py --find "<id>|<label>|<brand>|<material>|<#hex>"`), `find_feedback.py` stores those plus the palette family on the signal, and `scripts/find_preferences.py` aggregates them into the one line the scan reads at step 4 before judging anything.

Aggregation rules, and why: taps are weighted by age (120-day half-life — a year-old opinion is not current taste), grouped by brand, material and colour FAMILY rather than exact shade (so a tap generalises), and a pull must have at least 2 taps and enough weight before it is reported, because one tap is a mood. With no taps it says so plainly instead of inventing a preference. Test: `tests/test_find_preferences.py` drives send → tap → aggregate → guidance in a sandbox.

**Finds carry 👍/👎 (2026-10-13).** The Mon/Thu summary is sent by `scripts/send_find.py --text "..." --find "<decision id>|<label>"`, never by `send_telegram.py`, so each surfaced match gets a thumbs pair wired to `find_feedback.py`. Why: 18 recommendations had been surfaced and every outcome was still "unknown". The user said he would not remember to forward inspiration into Telegram, so the finds he already receives are where taste gets captured instead. A 👍 writes `taste` on the purchase decision and a `shopping-find-feedback` style signal; it does NOT set `user_action`, because liking and buying are different facts and the purchase watcher owns the second. Only the answered find loses its buttons.

**Colour is MEASURED, not read off a name (2026-10-13).** `color_match.py --name` returns no match for real catalog colour names — "Heathered Navy Blue", "Cream" and "Tobacco Cowhide" all came back empty — so a name-based colour check silently passed everything. `scripts/color_from_image.py --id <product id> --url <image> --match` reads the garment colour out of the product photo and scores the hex against `color_analysis.yaml` in one step; readings are remembered per product id in `state/product_colors.json`, so an image is fetched once.

The backdrop is read from the outer frame of the image rather than assumed to be white, because the hard case is a CREAM garment on a white background and cream is a core colour in his palette. `coverage` reports how much of the middle survived background removal; under about 0.15 the reading rests on an assumption and the scan should say the colour is not established rather than claim a verdict. Verified against live product shots: navy jogger #181E2F, cream crewneck #D2C7BB (in-palette, matches Fogbound at ΔE 3.9), tobacco boot #563F39 (in-palette, Hazy Espresso at ΔE 4.5).

**The plan's gaps and the watchlist are joined (2026-10-13).** `plan_progress.py` knew the holes in wardrobe-plan.md §2 and the watchlist was kept by hand, so the two drifted: P1 gaps sat unwatched while the scan hunted for things already filled. `scripts/plan_wants.py` lists every open plan row that no active want covers, and `--add` creates them.

It REPORTS by default. A reported gap is not automatically a want — the offer is the checkpoint, exactly as for a gap from `closet_report.py`. A row counts as covered only when an active want shares its category AND one of the row's distinguishing words, so a plain golf-polo want does not cover "knit polos". The rows it believes are already covered are PRINTED too, with the want ids, because the keyword test can over-match and a wrong "covered" is how a gap quietly stops being looked for. Test: `tests/test_plan_wants.py`.

**What the scan can see about a product (2026-10-13).** Each catalog product carries `attrs`: the store's structured `Key:Value` tags normalised across stores (material / fabrication / filter-material all become `material`; plus `fit`, `sleeve`, `colour_family`, `style`), and signals mined from the FULL description — `fibres`, `fit`, `weight`, `texture`, `origin`, `intent` — with only a 180-char `blurb` kept as text. Mining the blurb alone was not enough: a fit word showed on 6% of products and a texture word on 10%, because the detail sits past the first sentence of marketing copy; reading the whole description took fit to about 60% and texture to about 38% on a Todd Snyder sample. Search filters: `--material`, `--fit`, `--texture`. Texture matters because his golf rule is texture over pattern (references/20).

**The catalogs refresh on a rolling basis** (`wardrobe-catalog-refresh`, the 4 stalest stores every 2 hours — all 25 in one nightly burst was rate-limited whatever the spacing, 12 of 25 blocked on 2026-10-13, because the per-IP budget is shared across stores) and `CACHE_MAX_AGE` is 30h, so one missed night still cannot push a Mon/Thu scan into downloading 25 catalogs inside its 240s budget. That nearly happened on 2026-10-13: a cold refresh paces 100s between stores before a single product is downloaded.

**What counts as a good deal (The user, 2026-10-13).** Measured across 38,869 discounted variants: discounting works completely differently per brand, so one percentage would be wrong everywhere. Each store in `config/stores.json` carries `sale_tier` and `deal_discount`:

| tier | stores | a deal starts at |
|---|---|---|
| `rare` | H&B, johnnie-O, Criquet, Alden, Gitman, Randolph, Peter Millar, G/FORE, FootJoy, Common Projects, Veja… | **15%** — any markdown is news |
| `light` | State & Liberty, Tecovas, Filson, Greyson, Buck Mason, Vuori, RAEN, Seager, Katin… | **25%** |
| `always-on` | Faherty, Todd Snyder, Taylor Stitch, American Giant | **55%** — half their catalog sits at 40–50% permanently, so that IS the price |

A 90-day low counts as a deal at any percentage (`is_90d_low` on a search row). And underneath all three, wardrobe-plan.md §1 still rules: a piece he will wear often is a yes at FULL price and the discount is a bonus.

**Every want now has a price cap (2026-10-13).** 13 of 16 had none, so "within budget" and "is this a deal" could not be judged for most of the list. The cap is the category's normal list price at his core brands PLUS headroom — it exists to block something unusually expensive, not to hold out for a discount. A few categories are deliberately set higher: a piece where full price is fine because the quality earns it, a heavier knit (a shawl cardigan in good wool sits above an ordinary sweater), and a buy-once field jacket, where a cap at the ordinary jacket level would have blocked every good one and passed only the cheap ones. Set through `shopping_watchlist.py update <id> --max-price`.

**His palette is checked on EVERY find, and that is enforced not instructed (2026-10-13).** He asked for confirmation that the colour analysis is used every time; it was not — the cron said to measure colour "when colour matters", which is judgment. Now step 4 says every candidate without exception, and `send_find.py` REFUSES any find whose 5th field is not a measured hex, so a candidate that was never measured cannot reach him. A colour NAME is refused too, because names are what `color_match.py --name` could not match in the first place. The one escape is the literal `n/a`, for something with no single colour, and it has to be typed deliberately.

**Audit findings, 2026-10-13** (he asked what else was "not acting as it should" after the palette check turned out to be optional):

- **`style_learning.py` counted every signal as a rejection.** It read `style_signals.json` with no source filter, so a 👍 on a shopping find (action `liked`) would have taught the engine to AVOID what he liked. Now filtered to `outfit-proposal-response`, like `learned_adjustments.py` and `rule_proposals.py` already were. `style_reflection.py` now counts signals by source instead of labelling them all proposal responses. Guarded by `tests/test_shopping_store.py::SignalSourceTests`, which fails if any consumer stops filtering.
- **`source_health.json` was write-only.** Its whole purpose is "which sources keep blocking us" and nothing had ever read it — Peter Millar had 403'd on every run for a month and the scan kept trying. `source_health.py --blocked` now lists stores at 3+ consecutive failures and the scan skips them before spending its capped web searches.

**Source names.** `source_health.py --source <anything>` folds the name into its canonical slug via `stores.json` aliases, so one store keeps one failure counter however the scan spells it (Peter Millar sat under four keys before 2026-10-12). A corrupt profile JSON fails loudly and is repaired with `scripts/repair_json.py <file>` (keeps the original under `state/corrupt/`), never by hand.

The host runs a bounded recurring scan. It rotates through active entries, stores product identity and price/stock history, and reports only new or materially changed strong matches. Store pages may be monitored only through access methods permitted by that store; email/product feeds and structured authorized sources take precedence over brittle page automation.
