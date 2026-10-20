## Care & Maintenance

Wear-based, not calendar-based — something unworn for a year doesn't need cleaning; something worn hard for a month might. `care.py check` handles this automatically; only step in with judgment when there's no default rule for a category (jewelry/sunglasses/ties intentionally have none — situational, not cadence-driven) or when told a default is wrong for a specific item.

Cleaning method and drying method are tracked separately and both matter — a garment can survive the wrong wash and still get ruined by a hot dryer. Always pass along the `drying` instruction alongside the cleaning action, not the action alone.

The category/material defaults are the automatic, zero-effort baseline — never require a care-label photo or extra input. But a real care label always beats a generic default, so if one's shared, capture it: `python3 scripts/care.py override --item <id> --drying "<what the tag says>"` — opt-in enrichment, never a requirement.

1. **Mention what's due naturally, not on a timer** — a good moment is after logging an outfit or during a general wardrobe conversation. `python3 scripts/care.py check`.
2. **When care actually happened** ("just got the boots conditioned"): `python3 scripts/care.py log --item <id> --action "<what was done>"`. Writes immediately — the statement of a completed fact is the checkpoint.
3. **When a default gets corrected, or a real care label gets shared**: `python3 scripts/care.py override --item <id> [--interval-wears N | --interval-days N] [--action "..."] [--drying "..."]`. Only the given fields change — defaults are a starting point, not a fixed rule.
4. Items `at-cleaner-or-repair`, `packed-away`, `lent-out`, or `retired` are excluded — already handled, or not in rotation.

### Laundry days (2026-09-19)

A confirmed wear marks one-wear washables (`shirt`, `t-shirt`, `activewear`) `dirty` and stamps `dirty_since`. `scripts/laundry_day.py` (silent cron `wardrobe-laundry-day`, 8pm daily) returns them to `active` on the user's laundry days in `profile/laundry.json` — only when he is home, only pieces dirty since before that day, never dry-clean or spot-clean items. Anything dirty more than `max_dirty_days` comes back regardless, so a skipped week cannot empty the closet. Do not ask him whether laundry is done. If he says his laundry days changed, edit `profile/laundry.json` (weekday names) and record his words in `evidence`. If he says a specific piece is still in the hamper, set it `dirty` with `closet_bulk_state.py --ids <id> --set-state dirty --apply`.
