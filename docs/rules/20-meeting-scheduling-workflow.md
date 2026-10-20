## Meeting Scheduling Workflow
When the user asks to schedule, confirm, or reply to a meeting request, run this chain in one turn without waiting for step-by-step prompts:
1. Find the availability email (apply email search fallback if needed)
2. Extract ALL constraints from the requester's email: specific dates, time-of-day preference (morning/afternoon/evening), duration, and any other stated requirements. List these explicitly before touching the calendar — do not assume, do not skip.
3. Check the user's calendar only within the constrained windows (e.g., if requester said "afternoon," check 12 PM–6 PM only — never offer morning times in response to an afternoon request)
4. Draft the reply confirming or proposing an alternative, using the user's email template (see Writing section)
5. Show: extracted constraints, calendar status, and full draft — then Confirmation Protocol → ✅ → send via thread reply → verify with message ID
