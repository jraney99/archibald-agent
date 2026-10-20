## Item record shape

```json
{
  "id": "short-slug-or-uuid",
  "category": "blazer",
  "brand": "J.Crew",
  "model": "Ludlow",
  "color": "navy",
  "material": "wool",
  "size": "40R",
  "purchase_date": "2026-06-20",
  "purchase_price": 250,
  "condition": "excellent",
  "season": "all-season",
  "formality": 4,
  "occasion_tags": ["business-formal"],
  "lifecycle_state": "active",
  "photo": "profile/photos/closet/short-slug-or-uuid.jpg",
  "wear_count": 0,
  "estimated_frequency": null,
  "palette_match": null,
  "notes": "",
  "added_via": "manual",
  "added_date": "2026-07-13"
}
```

Tops (`shirt`, `polo`, `t-shirt`) also carry `"sleeve": "short" | "long"` (from `vocab.yaml` `sleeve_lengths`) — record it at intake from the photo; the heat comfort rule depends on it and flags any top that lacks it.

`category`, `season`, `condition`, `lifecycle_state`, `added_via`, and `occasion_tags` must be values from `vocab.yaml`. `formality` must be an integer key from `vocab.yaml`'s `formality_scale`. Do NOT store a `compatible_items` field — that's computed at request time by the Outfit Engine, never stored (DESIGN.md universal rule 4). Closet fields are factual inventory, not taste preferences, so they don't carry the `{value, confidence}` wrapper — uncertainty in what a field actually *is* gets resolved through confirmation before the record is ever saved (see below), not carried forward as a lingering score.

`estimated_frequency` (optional, one of `vocab.yaml`'s `usage_frequency_estimates`) is a self-reported usage baseline, not a tracked count — it's what closes the gap when a pre-existing item enters the closet with `wear_count: 0` (see "Capturing a self-reported usage baseline" below). Leave it `null` for anything actually new; never guess it.

`palette_match` (optional) is set at intake by running `color_match.py --hex <your best hex estimate of the item's colour>` and storing `{"hex": ..., "name": ..., "family": ..., "delta_e": ..., "verdict": ...}` from the result (`null` if `color_analysis.yaml` isn't populated, or the item has no meaningful single colour). It's a snapshot for Wardrobe Analytics' palette-coverage read — recompute it if the item's `color` is later corrected. See "Colour Analysis" below.

For a retired item, three more optional fields record what actually happened to it: `disposition` (one of `vocab.yaml`'s `disposition_types` — `validate_closet.py` rejects it unless `lifecycle_state` is `retired`), `disposition_value` (numeric, what it sold for, if anything), and `disposition_date`. See "Lifecycle state discipline" below for when to capture these.

For `category: watch`, an optional `movement_type` field (`quartz` / `mechanical` / `automatic`, from `vocab.yaml`) sharpens `care.py`'s service-interval guess. Not required; without it, `care.py` falls back to a generic watch interval.
