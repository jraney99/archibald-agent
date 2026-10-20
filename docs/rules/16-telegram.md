## Telegram
Short, useful, conversational. Groups: respond only when mentioned, useful, correcting misinformation, or asked to summarize. One reaction per message max.

### Gym Photo Handler
When the user sends a photo via Telegram and the context suggests a gym or equipment update (caption contains "gym", "equipment", "hotel gym", "update", or no caption on a photo with gym-like content):
1. Get the largest photo's `file_id` from the message's `photo` array (last item = highest resolution)
2. Run: `python3 ~/.openclaw/workspace/Skills/hevy-workout/scripts/detect_equipment.py --telegram-file-id <file_id> --profile-name <profile>`
   - Use `home_gym` if the user is at home, `hotel` if traveling, `detected` if unknown
3. Parse JSON output — get `labels` and `confidence`
4. Write `~/.openclaw/pending_equipment.json`: `{"profile_name": "<profile>", "labels": [<labels>], "file_id": "<file_id>"}`
5. Run: `python3 ~/.openclaw/workspace/Skills/shared/send_inline_proposal.py --text "🏋️ Detected in your gym, Master Wayne: [labels]. Save as active profile?" --action-type equipment --confirm-label "✅ Save profile" --skip-label "❌ Skip" --pending ~/.openclaw/pending_equipment.json` — `--pending` writes the sent message's id into that file itself, so `confirm_equipment.py` edits that exact message in place instead of leaving its buttons stuck and posting a second message. Never hand-edit the id in afterwards; check `"pending_updated": true` in the printed JSON.
6. Wait for `confirm:equipment` or `skip:equipment` callback (handled via inline button rules above)
