## Queries

For "what do I own," "show me my jackets," etc. — read `profile/closet.json` directly and answer from it. Don't guess at contents; if it's empty or thin, say so plainly rather than fabricating an answer.

For "show me a photo/picture of X" (one item, or several — "show me all the X options"): **run `python3 scripts/show_photos.py <item-id> [<item-id> ...]`** (or `--search "<term>"` to match by id/brand/model/color) and relay its stdout lines verbatim, one per line, in your reply — don't retype, reformat, or summarize them, and don't hand-construct `MEDIA:` lines yourself. This exists because hand-typing multiple `MEDIA:<path>` lines in a long reply has proven unreliable in practice (2026-09-16: the model said "let me send the individual photos" twice in a row, never emitted a single `MEDIA:` line either time, then falsely claimed it already had — a fabrication, not a fluke). The script also never fabricates a path: an unknown id or missing photo file prints a plain "(no photo...)" line instead, which you should relay as-is rather than describing the item as a substitute for showing it.

For "show me the proposed outfit": **run `python3 scripts/outfit_board.py <item-id> [<item-id> ...] --title "<occasion>"`** and relay its stdout verbatim. It creates a local JPEG flat lay from the exact catalog photos and labels each piece; it does not invent a person or alter the garments. If it exits unsuccessfully, immediately fall back to `show_photos.py` with the same IDs. Never promise the board before checking for a `MEDIA:` line.

Never read the bare `profile/` or `profile/photos/closet/` directory directly (that's a directory, not a file, and will fail) and never build an HTML/canvas grid. `outfit_board.py` is the only supported combined image because it produces a validated JPEG; multiple individual photo sends remain the fallback.

After running the script, check its output actually contains the `MEDIA:` lines you expected before telling the user the photos are on the way — if the tool call itself failed or returned nothing, say that plainly instead of claiming success.

For "how much is my closet worth" / "what have I spent on X" — `python3 scripts/closet_value.py` rather than summing by hand; it excludes and flags items with no recorded price instead of treating them as free.

For "what needs cleaning/servicing" — `python3 scripts/care.py check` rather than guessing from wear count by eye.

For "is this colour good on me?" / "what are my best colours?" / "what should I avoid?" — `python3 scripts/color_match.py --hex "<hex>"` for a specific colour, `--list-family <family>` or `--families` to show the palette. Answer from the palette and the `exclude` rules in `color_analysis.yaml`, not a generic sense of what "suits a summer." Translate ΔE into plain language ("a near-perfect match" / "close but a touch warm"), don't quote the number.

For "am I actually buying less" / "how's my shopping discipline been" — `python3 scripts/purchase_decisions.py report [--days N]` rather than trying to recall past conversations.
