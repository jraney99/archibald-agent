## Automation, calibration, and lifecycle

### Progressive calibration

There is no mandatory onboarding. `scripts/init_profile.py` creates private empty data files, and the first real request works with conservative defaults. When weather comfort materially affects a recommendation and `comfort_profile.py predict` returns confidence below 60, ask one concrete scenario question. Include feels-like temperature, wind/rain, activity, time outdoors and context. Record the explicit answer with `comfort_profile.py observe`. Never infer "runs hot/cold" from one answer.

### Calendar and weather

`calendar_outfit_watch.py` is a no-model poller. It reads all calendars, ignores cancelled/declined events, deduplicates exact event versions, and wakes the agent only for new attire-bearing events. The agent resolves forecast and context, calls `outfit_score.py`, saves the proposal, and sends the JPEG. Event edits supersede earlier proposals. `post_event_watch.py` asks once after planned outfits; only "Wore it" becomes wear history.

Structured rejection buttons record whether an outfit was too formal, casual, warm, cold, unavailable, or simply unwanted. These stay as low-confidence signals until repeated evidence or explicit feedback supports a confirmed profile diff.

### Store and purchase lifecycle

Every inspected product goes through `product_history.py`, preferably keyed by retailer SKU/product ID plus color code and size. `source_health.py` records blocked, timed-out and malformed sources separately; a source failure never means out of stock. Prefer authorized feeds and retailer emails over page automation.

`purchase_lifecycle.py` tracks ordered, shipped, delivered, kept and returned states, return deadlines and first-fit verdicts. Order-email extraction may populate a draft record, but purchases, returns and closet additions still require the user's statement or confirmation. When a delivered item is kept, link the corresponding purchase decision and add the confirmed garment through normal closet intake.

`purchase_email_watch.py` performs a read-only Gmail scan for apparel order lifecycle messages and wakes the agent only for new candidates. It never replies, cancels, returns or contacts a retailer. Delivery/return questions are sent only when action is useful.

### Travel

`calendar_travel_watch.py` is a no-model detector. It wakes the packing workflow only for new travel evidence. Resolve destination, dates and activities from verified calendar/itinerary data; if a material fact is missing, ask once. Build a reusable capsule from `travel_packing.py`, destination weather and baggage constraints. Never book or alter travel.
