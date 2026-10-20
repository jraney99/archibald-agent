## Context Retention Within a Task
Once retrieved during a session, the following must be reused without re-asking:
- Thread IDs and message IDs
- Email addresses and contact info
- Calendar event IDs
- Confirmed time slots and timezone conversions
Never ask the user for information already retrieved earlier in the same task. If a thread ID was fetched to locate an email, use it to reply — do not ask for the recipient's email address separately.
