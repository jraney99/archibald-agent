## Airport Flight Log Reply (free text, not a callback)
After a flight, `prompt_flight_log.py` sends a message asking for two numbers and writes `~/.openclaw/pending_flight_log.json`. This is the ONLY case where a bare short numeric reply is a command rather than conversation.

- **If `~/.openclaw/pending_flight_log.json` exists** AND the user's inbound message is a short reply that is either two numbers (`8 22`, `8,22`, `8, 22`, `sec 8 gate 22`) or one of `skip`/`none`/`-`:
  run `python3 -W ignore ~/.openclaw/workspace/Skills/email-monitor/scripts/log_flight.py --reply "<his exact message>"` — then `report` stdout, end with 🎩.
- **If that file does NOT exist**, a short numeric message is normal conversation — do not run the script.
- **If the user replies `lower airport buffer` (or `apply the buffer suggestion` / `yes` directly under a buffer-suggestion message)**:
  run `python3 -W ignore ~/.openclaw/workspace/Skills/email-monitor/scripts/log_flight.py --apply-suggestion` — `report`.

Never claim the log was recorded unless the script ran this turn and its output says so (same rule as the callback table).
