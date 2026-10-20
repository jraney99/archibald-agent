## Wardrobe: a bare photo, and replies to wardrobe questions

**A photo with no words** (not a reply to a nudge, no receipt question open — the gateway's open-question note tells you what is open):
1. Look at it. If it shows a person wearing clothes — the user in a mirror, or a photo of himself — match every visible piece to a **real closet id** (`Skills/ai-wardrobe-steward/profile/closet.json`, or `python3 Skills/ai-wardrobe-steward/scripts/show_photos.py --search "<words>"`). Never invent an id; leave out anything you cannot match.
2. Run `python3 Skills/ai-wardrobe-steward/scripts/propose_wear_from_photo.py --items <id1,id2,...>` — it asks him *"Looks like: … Log as worn today?"* with ✅ / ✏️ buttons. That script is the only way to ask; **never type the question yourself and never log a wear from a photo without his tap.** Reply with nothing else (`NO_REPLY` after the script confirms it asked).
3. A photo of a single garment laid flat, a tag, or a shop listing is closet intake (references/06), not a wear. A receipt is finance (rule 09). If it is clearly none of these, answer normally.

**"Not quite" follow-up** (the open-question note says he tapped "Not quite" on a photo guess): his message says what was wrong. Work out the corrected closet ids from his words and run
`python3 Skills/ai-wardrobe-steward/scripts/wear_photo_callback.py --id <question id> --answer correct --items <corrected ids>` — it logs the wear and edits the original question in place. Reply `NO_REPLY` when its output is exactly that; otherwise relay its one line, ending 🎩. If you cannot tell which pieces he means, ask **one** short question.

**"Wore something else" follow-up** (the note names a proposal): `Skills/ai-wardrobe-steward/references/21-wear-reports.md`.

**Delivered-order questions** ("📦 Delivered: … Add it to your closet?") are sent by `propose_closet_addition.py` from the purchase watcher and answered by buttons; you never write them.
