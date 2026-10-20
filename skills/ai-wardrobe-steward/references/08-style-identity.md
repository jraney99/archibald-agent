## Style Identity

No calibration step, no reference-image quota, no "let's set up your style" conversation. Preferences accumulate from ordinary mentions and reactions — "I like how JFK dressed," a Pinterest screenshot dropped in because it was handy, a complaint about a purchase — each one a confidence-scored entry, not a form field. Trigger this whenever a like/dislike, a style description, or a visual reference comes up — never only during some dedicated session.

1. **Extract attributes and assign confidence + importance** from whatever was shared: a described preference, a reaction to an image (colors, silhouette, formality, materials), an explicit dislike. See "Confidence-scored memory, in practice" above for how to set both numbers, and attach a short `evidence` note describing what was actually said or shown.
2. **Draft a diff, not a full rewrite.** `add` for new entries — `[{"value": "workwear", "confidence": 75, "importance": 60, "evidence": [{"date": "2026-07-13", "note": "mentioned liking Steve McQueen's style"}]}]` — reinforcing an existing value (matched case-insensitively) merges into it rather than duplicating. `set` for scalar fields (`desired_persona`, `fit_notes`), each `{value, confidence, importance}` or `null`. `remove` for anything no longer true, matched by plain value. Write to `profile/pending_style_update.json` with a one-line `review_summary`.
3. **Show the summary in plain language, not the raw diff, and wait for confirmation** — "Sounds like you're drawn to understated, natural-fabric classics — add that?" Never let one data point silently overwrite the profile; this is what keeps it from whiplashing on a single offhand comment.
4. **On confirmation:** `python3 scripts/confirm_style_update.py` (`--show` first to double check). On rejection: `python3 scripts/cancel_style_update.py`.
5. If a visual reference came in as an actual image file, copy it to `profile/photos/inspiration/<slug>.jpg` on confirmation (not before — there's no draft-time id to name it after until the diff is confirmed) only if there's a real reason to re-analyze it later; the point of the exchange is the extracted attributes, not archiving the file.

**Photos are one knowledge source among many, never a required upload.** When a read is genuinely fuzzy and visual references would sharpen it, offer — with the actual reason attached, not a quota: "I've got a decent read on your style, but a few outfits you actually love would sharpen it — if you've got 5-10 handy." Never "please upload 30 photos before continuing." If nothing prompts it, don't ask.

Only one style update should be pending at a time — if a new one arrives while one's still unconfirmed, resolve or discard the old one first rather than silently overwriting `pending_style_update.json`.

### Style Identity / Genome layer

Individual preferences (colors, materials, brands) are one layer; a coherent *identity* is one level above that — not "likes navy" but "classic American, coastal, rugged, refined." A human stylist forms this synthesis naturally after a handful of conversations; it's never built by asking directly ("what's your style archetype?" is exactly the kind of question this skill doesn't ask). It emerges from connecting things already said — a couple of inspirations, a cluster of style_categories, a pattern in what gets avoided — into a higher-level read, the same way `style_reflection.py` connects individual entries rather than any single one being asked for in isolation.

Stored in `profile/style_identity.yaml`, same `{value, confidence, importance, evidence}` shape as everything else:
- **`archetype`** (scalar) — a short, human phrase like "American Gentleman Adventurer," not a category picked from a fixed list. Starts at low-to-moderate confidence off a couple of signals (a named inspiration, a cluster of style_categories) and only rises with repetition/confirmation, same as any other preference — don't assign 90 confidence off one data point just because it feels like a satisfying synthesis.
- **`pillars`** (list) — the handful of threads that make up the archetype ("Ivy League refinement," "coastal freedom," "rugged heritage"). Several can and should coexist; importance is what distinguishes a dominant pillar from a minor one.
- **`desired_impression`** (list) — how the person wants to come across (competent, approachable, confident, understated) — inferred from stated goals ("I want to look put-together without trying too hard") or a consistent pattern in reactions, never asked as a direct question.
- **`anti_identity`** (list) — what they're consciously moving away from (flashy luxury, trend-chasing, excessive branding) — usually surfaces as a rejection ("I don't want to look like I'm trying too hard") more often than a proactive statement.

**Draft and confirm exactly like Style Identity preferences above** — `add`/`set`/`remove` into `profile/pending_style_identity_update.json`, `python3 scripts/confirm_style_identity_update.py` (`--show` first) to apply, `python3 scripts/cancel_style_identity_update.py` to discard. Never surface this as a questionnaire or a "let's figure out your archetype" moment — it should read as the agent occasionally sharing a synthesis it's arrived at ("it sounds like you're drawn to a classic, rugged-but-refined look — does that sound right?"), offered the same way an explanation would be, and just as easy to correct.

**Use the archetype/pillars to ground reasoning, not just to store it** — see "Identity Fit" in Shopping Advisor and Outfit Engine below.


## Pinterest coverage is capped by Pinterest (2026-10-14)

His board held 155 pins and 101 were imported. The importer is not at fault and there is no
setting to change:

- the `.rss` feed returns the same ~25 items whatever you ask for — `page`, `limit`, `offset`
  and `count` were all tried and all ignored;
- the board page embeds FEWER pins than the feed does (27 ids against the feed's union), so
  reading the HTML is a step backwards;
- what DOES vary is which slice is served, and it varies over hours rather than seconds: six
  fetches two seconds apart saw 34 distinct pins, while fetches a day apart had seen 46.

So the importer merges 4 fetches per run and the cron runs every 3 hours instead of nightly —
running often is the only lever. `pinterest_board_watch.py --coverage` reports imported
against the board's own `pin_count`, so the gap is visible rather than assumed.

The honest ceiling: new pins always arrive the same day, older ones trickle in as the rotation
shifts, and some may never appear. The only complete fix is the Pinterest API (trial access
reads a whole board), which needs a Business account — the user declined that on 2026-10-14 as
not worth the hassle.
