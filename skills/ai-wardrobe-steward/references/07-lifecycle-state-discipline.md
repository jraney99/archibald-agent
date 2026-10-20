## Lifecycle state discipline

Update `lifecycle_state` as it comes up in conversation — "just dropped my suit at the cleaners" → `at-cleaner-or-repair`; "packing the boots for the trip" → `packed-away` is for seasonal storage, not travel, so leave boots `active` while traveling unless told otherwise. Ask before flipping a state that's an inference rather than something stated directly. Only `active` items are ever treated as available to wear by the Outfit Engine, Travel Packing, or Care & Maintenance — this field is the gate all three read from.

```
python3 scripts/closet_bulk_state.py [--category <cat>] [--season <season>] [--ids id1,id2,...] [--current-state <state>] --set-state <state> [--apply]
```
Runs as a dry run by default — it lists what would change without writing anything. Show that preview, then re-run with `--apply` once confirmed. At least one filter is required; an unscoped call would silently match the entire closet. `--ids <one-id>` handles a single item the same way.

**Cleaning doesn't automatically flip `dirty`/`at-cleaner-or-repair` back to `active`** — logging a wash doesn't by itself mean something's back in rotation (still drying, still needs a repair too), so this stays a judgment call. `care.py log` notes when the item it just logged is still `dirty` or `at-cleaner-or-repair`; when that note appears, ask if it's back in rotation and run the command above if so — otherwise the item silently stays invisible to the Outfit Engine indefinitely after being cleaned.

**Retiring an item is a natural moment to ask what actually happened to it.** "Retired" on its own is a dead end — no record of the outcome, even though the outcome (sold, donated, given away, discarded) is directly relevant to the stewardship principle. When something's being retired ("finally getting rid of these," "donating the jacket I never wear"), capture it in the same call:
```
python3 scripts/closet_bulk_state.py --ids <id> --set-state retired --disposition <donated|sold|given-away|discarded> [--disposition-value <amount>] [--disposition-date YYYY-MM-DD] --apply
```
`--disposition-date` defaults to today if omitted. A disposition is optional even when retiring — plenty of retirements won't have one worth asking about — but if the user volunteers what happened to something, capture it rather than letting it disappear into a bare lifecycle-state flip. `closet_value.py` reads `disposition_value` to report what's actually been recouped.

**A season change is a natural moment to check what's packed away**, not a scheduled reminder — when the season actually comes up in conversation ("it's finally cooling off"), run:
```
python3 scripts/seasonal_review.py --season <season>
```
and offer to review what it finds ("looks like your wool coat and that scarf are still packed away — want to bring them back into rotation?"). It only lists candidates; use `closet_bulk_state.py` above to actually flip them once confirmed. No calendar hook is wired up, so this stays conversation-triggered, same as weather/travel dates elsewhere in this skill.


## Retiring a piece records WHY (2026-10-13)

`closet_bulk_state.py --set-state retired` now REQUIRES `--reason` from `vocab.yaml`
`retirement_reasons`: `doesnt-fit`, `worn-out`, `redundant`, `off-palette`, `never-wore`,
`style-changed`, `replaced`. Optional `--reason-note` keeps his own words verbatim. It lands
on the item as `retirement: {reason, date, note}`.

Why a fixed list and not free text: on 2026-10-13 only 13 of 27 retirement notes stated a
reason at all, and seven entries for a single brand were ONE sizing mistake written
down seven times — mining them would have produced a confident lesson to stop buying a
brand he likes, which is the opposite of true. The user chose to start fresh, so the old
notes are deliberately NOT mined and the record starts here.

`scripts/retirement_lessons.py` turns repeats into purchase lessons: two or more pieces
sharing a brand AND a reason, because one retirement is an incident and two is a pattern.
`--propose` drafts into `pending_purchase_lessons.json` for the normal confirm step; nothing
reaches `purchase_lessons.json` unconfirmed. `purchase_lessons_lookup.py` is already read by
the Mon/Thu scan, so a confirmed lesson reaches shopping without further wiring.

`never-wore` is phrased as the strongest signal there is: it fit, and he still did not reach
for it. Test: `tests/test_retirement_lessons.py`.
