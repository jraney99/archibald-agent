## Fit Detail Reply (typed words, not a callback)

After a delivery, the wardrobe asks "📦 <item> arrived. How's the fit?" with 👍 / 👌 / 👎.
A 👌 or 👎 tap edits that message into a question: *"Tell me what's off — where it's tight or
loose, long or short."* the user chose to describe it in words (2026-10-12) because a sentence
carries more than a reason button. The tap writes
`workspace/Skills/ai-wardrobe-steward/state/pending_fit_detail.json`, and
`plugins/callback-dispatch/pending-context.mjs` puts one line in front of the agent's turn
saying the question is open (12-hour window).

**While that file exists, his next message about how something fits is the answer.** Record
it with one call, his words verbatim, nothing invented or summarised:

```
python3 workspace/Skills/ai-wardrobe-steward/scripts/fit_detail.py --id <id from the pending file> --text "<his words>"
```

The script appends the note to the purchase, adds it as evidence on that brand's sizing in
the body/fit profile, and closes the question. Reply with the one line it prints, ending 🎩.
**Never say it was recorded unless the script ran this turn and printed that line.** If his
message is plainly about something else, answer that and leave the file alone; it expires.
Do not ask follow-up questions about the fit and do not propose a return — the purchase
tracker handles return deadlines on its own.
