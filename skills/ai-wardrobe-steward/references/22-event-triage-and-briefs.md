## Which events get an outfit, and the finished brief (2026-09-20)

The user: no outfit recommendations for working out — only for golf, work meetings, travel and similar; and some named friends never need one.

**`scripts/event_triage.py`** decides, deterministically and **silent by default** — an event type it has never seen gets nothing:

| Gets an outfit | Never |
|---|---|
| a real round of golf — and **only** when nothing says otherwise | workouts: gym, swim, runs, yoga — and the simulator + driving range (he dresses for those as workouts) |
| | **indoor golf** (2026-10-13): any `indoor_golf_markers` word in the title or venue — indoor, simulator, sim, bay, lounge, Topgolf, Five Iron, X-Golf, Trackman, Puttery, Drive Shack |
| | **a tour or visit** (`visit_phrases`: tour, visit, walkthrough, open house, trial, demo, consultation, showing, membership) |
| in person with someone marked **business / date / family** | **friends** |
| flight day (one board per travel day) | Zoom / phone / Meet |
| hosted or ticketed events | coaching, doctor, facial, massage, errands, cleaning, "Leave for…" |
| a restaurant dinner with nobody named — only if the venue is dressy | anything it cannot positively classify |
| anything with a stated dress code | |

- People are identified by **invite email** (`fetch_calendar.py` now returns `organizer_email`, `attendees`), the title only as a fallback. Categories live in `profile/people.json`; colleagues on `internal_domains` never count. A person with no category yields `ask-person` — never guess a category.
- Restaurant verdicts are remembered per venue in `profile/venues.json` (`{"dressy": bool, "evidence": …}`).
- `python3 scripts/event_triage.py replay --from … --to … [--all]` prints every verdict with its reason. Use it whenever the user says a board was or wasn't wanted, fix the rule, and re-run until the list is right.

**`scripts/event_forecast.py`** — the forecast for the event's own place and hour (Open-Meteo). Returns nothing, with the reason, when it cannot know. Never substitute a number of your own or a different city.

**`scripts/outfit_brief.py`** — calendar → triage → forecast → travelling? → `outfit_score.py` with every flag set → a brief with the top 3 complete outfits, the learned reasons and his guidance for that activity. With a brief in hand your job is only: choose among the three with style judgment and write the caption. Do not re-run the scorer with your own flags.

**Status: LIVE since 2026-09-20** (The user approved the trigger, caption, both questions and the 🚫 button).

`calendar_outfit_watch.py` (every 15 min, 6am–9pm, 2 days ahead) runs `outfit_brief.py`, holds tomorrow's boards until 7:30pm tonight (one "👔 Tomorrow:" line, references/23), and then:
- sends the one-tap question itself when the triage cannot know — *who is this person* (Business / Date / Family / Friend, no outfits) or *is this restaurant dressy* (Yes / No, casual). `outfit_questions.py`; once per person/venue per event; an unanswered question means no outfit; answers are remembered in `profile/people.json` / `profile/venues.json`.
- wakes you with finished briefs. **Your whole job per brief is one command:**
  `python3 scripts/propose_from_brief.py --briefs <file> --index <i> --choice <1|2|3> --reason "<ONE short sentence, max 140 chars>"`
  It creates the proposal, picks the belt, renders the board (photos only) and sends it. Do not run the scorer, fetch weather, or change items.
  **The reason is one short sentence saying the one thing the photos cannot** — "Shorts today, it's 94°F", "Add a blazer, your rule for in-person business". Never describe the clothes: the caption already lists every piece by brand and colour, and the board shows them. 10–140 characters, enforced.

Every board carries a fourth-row **🚫 button** whose label says what one tap switches off (that person → `no-outfits` in people.json; that venue → not dressy; flights / golf rounds → `never_kinds`, hosted events → `never_phrases` in `profile/triage_preferences.json`). To undo one when the user asks, edit that file / that person's category and say what you changed.

If the user says a board was or wasn't wanted: `python3 scripts/event_triage.py replay --from … --to … --all`, find the verdict and its reason, fix the rule (vocab phrase lists are generic; people/venues/preferences are his), re-run until right. Never widen the trigger on your own — that changes what he receives and needs his go-ahead.

**Why the golf rule is shaped that way (2026-10-13).** The vocab alias `golf: golf-round` made every event with "golf" in it a round, and only a one-entry venue file (`profile/activity_preferences.json` → `venue_aliases`, one simulator venue) kept the simulator quiet. So a TOUR of an indoor golf club got a smart-casual board captioned "a real round of golf"; the user: *"this is not a real round of golf. This is a tour of a new simulator club that I may look into joining."* A round is now a round only when neither list fires. Real rounds at outdoor clubs are untouched — his titles are just "Golf <> the user @ <venue>", so requiring a positive phrase like "tee time" was tried and rejected: it silenced his regular outdoor club too. Regression: `tests/test_triage_golf.py`.
