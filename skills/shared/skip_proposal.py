#!/usr/bin/env python3
"""
Generic "skip"/"decline" handler for confirm:*/skip:* proposals whose
pending file just needs to be discarded (or kept, e.g. "keep as draft") and
the original proposal message updated in place - instead of leaving its
button visibly stuck and posting a separate, redundant reply underneath it
(same root cause fixed in log_meals.py and the 11-flow follow-up tonight).

Usage:
    python3 skip_proposal.py --pending /path/to/pending_X.json --text "Skipped. 🎩" [--keep]

--keep leaves the pending file in place (for a skip that means "keep this
around", not "discard it") - default is to delete it, matching the `unlink`
these dispatch-table entries already had before this fix.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pending_proposal import load_message_id, edit_or_fallback  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    pending = Path(args.pending)
    if not pending.exists():
        # Nothing to edit in place (already resolved / never existed) - just
        # say so plainly rather than silently pretending something happened.
        print(args.text)
        return

    msg_id = load_message_id(pending)
    if not args.keep:
        pending.unlink(missing_ok=True)
    print(edit_or_fallback(msg_id, args.text))


if __name__ == "__main__":
    main()
