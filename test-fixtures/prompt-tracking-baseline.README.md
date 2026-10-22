# prompt-tracking-baseline.json

Button-prompt senders that satisfy neither half of the contract in
`test_prompt_tracking_contract.py`: they do not register the message with
`chat_cleanup`, they do not reach Telegram through `send_telegram.send_chunk()`
(which registers automatically since 2026-10-12), and no dispatch handler for
their callbacks edits the message in place.

**This list may only shrink.** A new sender, or an existing one newly given
buttons, has to satisfy the contract rather than be added here.

## Why each entry is here

The list is empty in this repository: every button-prompt sender shipped here
satisfies the contract.
