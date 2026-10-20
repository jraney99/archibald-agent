## Boards, buttons and the daily flow (2026-09-28)

**When boards arrive.** A board for *tomorrow's* event is held and sent at 7:30pm tonight under one line — *"👔 Tomorrow: Breakfast w. Alex Morgan (8:00 AM) · Flight to LGA (6:25 AM)"*. A board for *today's* event (added the same day) goes out at once. The watcher does all of this; you only ever run `propose_from_brief.py` when handed briefs.

**Buttons under every board** (all deterministic, all edit the board in place — the reply is `NO_REPLY`):

| Row | Buttons | What happens |
|---|---|---|
| 1 | ✅ Wore it · 👕 Wore something else | logs the wear (history + hamper), or asks what he wore (references/21) |
| 2 | Swap top · Swap bottoms · Swap shoes | `outfit_swap.py`: the next-best piece from the same candidate pool; photo + caption re-rendered, *"Swapped: A → B"* added |
| 3 | Too formal · Too casual · Too warm · Too cold | a style signal (learned_adjustments.py); the 2nd consistent correction offers a rule (rule_proposals.py) |
| 4 | 🚫 … | switches off that person / venue / kind (references/22) |

The separate 9:30pm "Did you wear it?" message is retired; the board carries the question. Plan this / Another / Skip / Unavailable are gone.

**Other questions the skill asks by itself** (never type them):
- `outfit_questions.py` — new person / dressy venue (references/22).
- `propose_closet_addition.py` — a delivered order: *"📦 Delivered: … Add it to your closet?"* [✅ Add] [↩️ Returning it] [❌ Not clothing]. Run it from the purchase watcher with the fields read from the email. A piece added without a photo still works on boards (a "no photo yet" tile) — offer once to attach a photo when it next comes up.
- `propose_wear_from_photo.py` — a bare photo of him dressed: *"Looks like: … Log as worn today?"* (rule 35).
- `packing_list.py` — two evenings before an outbound flight, one message: destination forecast, the plane outfit, what to pack, what is in the hamper. Deterministic; nothing for you to do.

**Sunday.** `weekly_summary.py --brief` gives the morning briefing one line on what the wardrobe learned; silent in quiet weeks.

**Model.** The styling choice on a brief runs on Sonnet (`STYLIST_MODEL` in calendar_outfit_watch.py); everything factual was decided by scripts before you were woken.
