## Checked-Bag Question Reply (free text, not a callback)
The T-24h check-in reminder (`prompt_checkin.py`) sometimes asks "Checking a bag this trip?" and writes `~/.openclaw/pending_checked_bag.json`. As above, this is the ONLY case where a bare yes/no reply is a command rather than conversation.

- **If `~/.openclaw/pending_checked_bag.json` exists** AND the user's inbound message is a short affirmative (`yes`, `yeah`, `bag`, `checking a bag`, etc.):
  run `python3 -W ignore ~/.openclaw/workspace/Skills/email-monitor/scripts/set_checked_bag.py --confirmation "<confirmation from the pending file>"` — `report` stdout, end with 🎩. (The script deletes the pending file itself.)
- **If that message is a short negative** (`no`, `nope`, `carry-on`, etc.): delete `~/.openclaw/pending_checked_bag.json`, reply "Noted — carry-on only. 🎩".
- **If that file does NOT exist**, a bare "yes"/"no" is normal conversation — do not touch either script.

The user can also just mention checking a bag unprompted for an upcoming flight (no pending file needed) — same script, see the "Checking a bag mentioned in conversation" rule above.
