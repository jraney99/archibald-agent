## Inline Button Callbacks (confirm:*/skip:*)
> **As of 2026-08-30 these are handled deterministically by the `callback-dispatch` plugin (`before_dispatch` hook) BEFORE any agent turn — you should almost never see a raw `confirm:`/`skip:` message.** If you do (plugin disabled, or a token it doesn't know), the rules below are the fallback. Dispatch map: `~/.openclaw/plugins/callback-dispatch/dispatch-table.json` — keep it and this table in sync.

**Read this section in full before replying to ANY message whose text starts with `confirm:` or `skip:`** (strip a leading `callback_data: ` prefix if present, e.g. `callback_data: confirm:hydration-32` → `confirm:hydration-32`). This section lives here, not under any per-topic skill section — do not go looking for it under Email/Hydration/Nutrition/etc. Various daemons and crons send Telegram messages with ✅/❌ or quick-add inline buttons; the gateway delivers the tapped button's callback data as plain text.

Match the callback EXACTLY against the table below — do not ask for clarification, execute immediately. **Never reply with a success confirmation ("logged", "done", "✓", etc.) unless the listed command was actually run as a tool call this turn and its output supports that reply** — a plausible-sounding confirmation without having run the script is a false report, not a shortcut, and is equivalent to the "never claim completion without verification" violation in Critical Operating Rules. If the callback matches `skip:*` or `confirm:*` but is NOT in this table, reply "Skipped. 🎩" (skip) or "No pending action found. 🎩" (confirm) — never treat unrecognized callbacks as conversation.

**A callback is a fully independent, always-real, always-actionable event — never a continuation of the previous turn, and never "just context/history with nothing to act on."** Diagnosed 2026-08-20/21 from three separate real incidents' own recorded reasoning:
- A `confirm:hydration-32` callback arrived seconds after the user asked "Turn my bedroom lights off" and got a reply. The model let that immediately-preceding topic bleed into its interpretation and re-ran the bedroom-light check instead of `log_hydration.py`.
- A `confirm:hydration-32` callback arrived seconds after an unrelated "Time to clean the washing machine" reminder reply. The model's own reasoning concluded "there's no actual user message... this appears to be part of the system providing me with recent conversation history" and it did nothing at all — no reply, no tool call. (This is why the user saw no confirmation and tapped again.)
- On that second tap 20 seconds later, the model correctly recognized it as a hydration callback this time, but still never called `log_hydration.py` — and replied "Hydration logged, Master Wayne" anyway. A false completion claim.

None of these are reasoning failures that more context or clarification would fix — they are a bare `confirm:`/`skip:` string being misread as noise, or its adjacency to an unrelated prior topic being misread as relevance. Whenever inbound text matches `confirm:`/`skip:` (after stripping the `callback_data: ` prefix), that alone is proof it is a real, current, actionable event needing exactly one thing: match it against the table below and run the mapped command as a tool call this same turn. It is never "just conversation history," never "no actual user message," never a continuation of whatever you just replied to, no matter how close in time or how similar the topic feels. If you find yourself about to (a) repeat/re-verify your previous reply, or (b) conclude there's nothing to act on, or (c) reply with a success claim you have not backed with an actual tool call this turn — any of those three is the failure mode itself. Stop and re-match the callback against the table instead.

**Reply key:** `NO_REPLY` = script edits the original Telegram message in place and prints `NO_REPLY` on success — if stdout is exactly `NO_REPLY`, output that exact string alone, nothing else attached (no 🎩, per the silence-sentinel rule in Communication Style). `report` = summarize stdout, end with 🎩.

| Callback | Command (prefix `python3 -W ignore ~/.openclaw/workspace/Skills/` unless noted) | Reply |
|---|---|---|
| `confirm:outfit-<12hex>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action planned` | report |
| `skip:outfit-<12hex>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action skip` | report |
| `confirm:outfitworn-<12hex>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action worn` | report |
| `skip:outfitworn-<12hex>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action not-worn` | report |
| `outfit:alt-<12hex>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action alternate` | report |
| `outfit:formal-<12hex>` / `outfit:casual-<12hex>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action too-formal|too-casual` | report |
| `outfit:warm-<12hex>` / `outfit:cold-<12hex>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action too-warm|too-cold` | report |
| `outfit:unavail-<12hex>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action unavailable` | report |
| `outfit:menu-<12hex>-<main\|swap\|wrong>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action menu-<which>` | NO_REPLY (only swaps which buttons the board shows; records nothing) |
| `outfit:swap-<12hex>-<top\|bottoms\|shoes>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action swap-<slot>` (re-renders the board in place) | NO_REPLY |
| `confirm:closetadd-<12hex>` / `skip:closetadd-<12hex>-<return\|notclothing>` | `ai-wardrobe-steward/scripts/closet_addition_callback.py --id <id> --answer add\|return\|notclothing` | NO_REPLY (edits the "Delivered: … add it?" message in place) |
| `confirm:find-<12hex>` / `skip:find-<12hex>` | `ai-wardrobe-steward/scripts/find_feedback.py --id <id> --answer like\|dislike` | NO_REPLY (👍/👎 on a scan find; records taste, not a purchase; only that find's buttons change) |
| `confirm:pindigest-<12hex>` / `skip:pindigest-<12hex>` | `ai-wardrobe-steward/scripts/pinterest_digest_callback.py --id <id> --answer add\|skip` | NO_REPLY (edits the Sunday Pinterest digest in place) |
| `confirm:pinwatch-<12hex>-<n>` | `ai-wardrobe-steward/scripts/pinterest_digest_callback.py --id <id> --answer watch --n <n>` | NO_REPLY (adds a watchlist want; only that 🔍 button becomes 👀 Watching) |
| `confirm:fit-<12hex>-<great\|ok\|poor>` | `ai-wardrobe-steward/scripts/fit_callback.py --id <id> --answer <answer>` | NO_REPLY (edits "arrived. How's the fit?" in place; records the verdict + brand sizing evidence) |
| `confirm:wearphoto-<12hex>` / `skip:wearphoto-<12hex>` | `ai-wardrobe-steward/scripts/wear_photo_callback.py --id <id> --answer log\|notquite` | NO_REPLY (edits the "Looks like: …" message in place) |
| `outfit:never-<12hex>` | `ai-wardrobe-steward/scripts/outfit_callback.py --id <id> --action never` (the 🚫 button: stops outfits for that person / venue / kind) | report |
| `confirm:outfitwho-<12hex>-<biz\|date\|fam\|friend>` | `ai-wardrobe-steward/scripts/outfit_questions.py answer --id <id> --value <value>` | NO_REPLY (edits the question in place) |
| `confirm:outfitvenue-<12hex>-<yes\|no>` | `ai-wardrobe-steward/scripts/outfit_questions.py answer --id <id> --value <value>` | NO_REPLY (edits the question in place) |
| `confirm:outfitrule-<12hex>` | `ai-wardrobe-steward/scripts/rule_proposals.py resolve --id <id> --answer accept` | NO_REPLY (edits the rule question in place) |
| `skip:outfitrule-<12hex>` | `ai-wardrobe-steward/scripts/rule_proposals.py resolve --id <id> --answer decline` | NO_REPLY (edits the rule question in place) |
| `confirm:vacation` | `hevy-workout/scripts/confirm_vacation.py` | NO_REPLY (edits the proposal message in place) |
| `skip:vacation` | `shared/skip_proposal.py` | NO_REPLY (edits the proposal message in place; was "Skipped. 🎩") |
| Legacy email/calendar buttons | Refuse as stale; reissue using shared/show_pending_proposal.py. Never execute the current global pending slot. |
| Legacy email/calendar buttons | Refuse as stale; reissue using shared/show_pending_proposal.py. Never execute the current global pending slot. |
| `confirm:equipment` | `hevy-workout/scripts/confirm_equipment.py` | NO_REPLY (edits the proposal message in place) |
| `skip:equipment` | `shared/skip_proposal.py` | NO_REPLY (edits the proposal message in place; was "Skipped. 🎩") |
| `confirm:crm` | `email-monitor/scripts/confirm_crm_email.py` | NO_REPLY (edits the proposal message in place) |
| `skip:crm` | `email-monitor/scripts/cancel_crm_email.py` | NO_REPLY (edits the proposal message in place) |
| Legacy email/calendar buttons | Refuse as stale; reissue using shared/show_pending_proposal.py. Never execute the current global pending slot. |
| Legacy email/calendar buttons | Refuse as stale; reissue using shared/show_pending_proposal.py. Never execute the current global pending slot. |
| `confirm:calrename` | `email-monitor/scripts/confirm_cal_rename.py` | NO_REPLY (edits the proposal message in place) |
| `skip:calrename` | `email-monitor/scripts/skip_cal_rename.py` | NO_REPLY (edits the proposal message in place) |
| `confirm:diary` | `email-monitor/scripts/confirm_diary_entry.py` | NO_REPLY (edits the proposal message in place) |
| `skip:diary` | `shared/skip_proposal.py` | NO_REPLY (edits the proposal message in place; was "Skipped. 🎩") |
| `confirm:transcript` | `call-transcriber/scripts/confirm_transcript.py` | NO_REPLY (edits the proposal message in place) |
| `skip:transcript` | `shared/skip_proposal.py` | NO_REPLY (edits the proposal message in place; was "Skipped. 🎩") |
| `confirm:meals` | `reminders/scripts/log_meals.py` | NO_REPLY (edits the reminder message in place) |
| `skip:meals` | do not log | "Skipped. 🎩" |
| `confirm:hydration-<N>` (N = number, e.g. `-32`) | `hydration/scripts/log_hydration.py --oz <N>` | NO_REPLY |
| `confirm:putting` | `golf-putting/scripts/log_putting.py --done` | NO_REPLY (edits the nightly cue in place) |
| `confirm:openloop-<id>` (id = 12 hex) | `shared/open_loop_callback.py --id <id> --action done` | NO_REPLY (closes the commitment, edits in place) |
| `skip:openloop-<id>` (id = 12 hex) | `shared/open_loop_callback.py --id <id> --action keep` | NO_REPLY (snoozes 3 days, edits in place) |
| `skip:putting` | `golf-putting/scripts/log_putting.py --skip` | NO_REPLY (edits the nightly cue in place) |
| `confirm:meal-<date>-<slot>` (date=`YYYY-MM-DD`, slot = trailing int 1–5 — split on the LAST hyphen only, the date itself contains hyphens; e.g. `confirm:meal-2026-09-10-2` → date=`2026-09-10`, slot=`2`; snack slots 2/4 and the calendar-conflict path never carry a choice) | `nutrition/scripts/confirm_meal.py --date <date> --slot <slot> --action eaten` | NO_REPLY |
| `skip:mealchoice-<date>-<slot>` (same date/slot parsing) | `nutrition/scripts/confirm_meal.py --date <date> --slot <slot> --action declined` | NO_REPLY |
| `confirm:prep-done` | `nutrition/scripts/confirm_prep.py` | NO_REPLY |
| `confirm:movement-<date>` | `hevy-workout/scripts/log_movement.py --date <date> --action done` | NO_REPLY |
| `skip:movement-<date>` | `hevy-workout/scripts/log_movement.py --date <date> --action skipped` | NO_REPLY |
| `confirm:booking` | `restaurant-booking/scripts/confirm_booking.py` | NO_REPLY (edits the proposal message in place) |
| `skip:booking` | `shared/skip_proposal.py` | NO_REPLY (edits the proposal message in place; was "Skipped. 🎩") |
| `confirm:cancelbooking` | `restaurant-booking/scripts/cancel_booking.py confirm` | NO_REPLY (edits the proposal message in place) |
| `skip:cancelbooking` | `shared/skip_proposal.py` | NO_REPLY (edits the proposal message in place; was "Kept. 🎩") |
| `confirm:golfbooking` | `golf-booking/scripts/confirm_booking.py` | NO_REPLY (edits the proposal in place; books the tee time on GolfNow as the user — stops at checkout with a screenshot until `checkout.mapped` is true) |
| `skip:golfbooking` | `shared/skip_proposal.py` | NO_REPLY (edits the proposal message in place; "Skipped. 🎩") |
| `confirm:txncat-<gmailid>-<slug>` (slug = a short category code, e.g. `dine`/`groc`/`pers`/`oth`/`keep`/`rcpt` — split on the LAST hyphen; the id is hex. `keep` = confirm the auto-category on a large-purchase prompt; `rcpt` = wait for a receipt photo; some slugs chain into a follow-up step, e.g. a delivery-service charge that is really groceries also asks for the receipt) | `<finance-skill>/scripts/finance_txn_categorize.py --id <gmailid> --slug <slug>` | NO_REPLY (edits its own message) |
| `confirm:txnrule-<token>` | `<finance-skill>/scripts/finance_txn_apply_rule.py --token <token>` | NO_REPLY (edits the proposal message in place) |
| `skip:txnrule-<token>` (same token) | `<finance-skill>/scripts/finance_txn_apply_rule.py --token <token> --skip` | NO_REPLY (edits the proposal message in place) |
| `confirm:filter` | `email-monitor/scripts/confirm_filter.py` | NO_REPLY (edits the proposal message in place) |
| `skip:filter` | `email-monitor/scripts/skip_filter.py` | NO_REPLY (edits the proposal message in place) |
| `confirm:loopclose-<id>` (id = 8-char hash from `loop_closer.py`) | `loop-closer/scripts/loop_closer.py close --id <id> --resolution done` | NO_REPLY (edits the escalation message in place) |
| `skip:loopclose-<id>` (same id) | `loop-closer/scripts/loop_closer.py keep-open --id <id>` | NO_REPLY (edits the escalation message in place) |
| `confirm:unsub` | `email-monitor/scripts/confirm_unsub.py` | NO_REPLY (edits the proposal message in place) |
| `skip:unsub` | `email-monitor/scripts/skip_unsub.py` | NO_REPLY (edits the proposal message in place) |
| `confirm:grocery-used-<id>` (id is the digest entry's id, e.g. `20260913-001`) | `grocery-expiration/scripts/mark_item.py --id <id> --action used` | NO_REPLY (edits its own message) |
| `confirm:grocery-toss-<id>` | `grocery-expiration/scripts/mark_item.py --id <id> --action tossed` | NO_REPLY (edits its own message) |
| `confirm:trade-<32hex>` / `skip:trade-<32hex>` | **Run NOTHING.** Only the callback-dispatch plugin may act on a trade tap (`brokerage-trading/scripts/trade_callback.py`); trading-guard blocks you from it. | Reply exactly: "That trade tap didn't go through, Master Wayne — nothing was placed. Ask me to propose it again if you still want it. 🎩" |
