"""Reissue an email/calendar proposal with buttons bound to its current contents."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path.home() / ".openclaw"
sys.path.insert(0, str(ROOT / "workspace/Skills/email-monitor/scripts"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["email", "cal"])
    parser.add_argument("--id", help="Exact Gmail message ID (required for email)")
    args = parser.parse_args()
    import daemon
    if args.kind == "email":
        if not args.id:
            parser.error("email requires --id; never choose the latest draft implicitly")
        state = daemon._load()
        entry = state.get("gmail_pending", {}).get(args.id)
        if not entry:
            raise ValueError("No pending draft with that ID")
        entry["gmail_msg_id"] = args.id
        text = f"To: {daemon._h(entry['to_email'])}\nSubject: {daemon._h(entry['subject'])}\n\n{daemon._h(entry['draft'])}"
        mid = daemon._tg_send_with_buttons(text, "email", "✅ Send", "❌ Cancel", proposal=entry)
        if not mid:
            raise RuntimeError("Proposal delivery failed")
        entry["source_tg_msg_id"] = mid
        latest = daemon._load()
        latest.setdefault("gmail_pending", {})[args.id] = entry
        latest.setdefault("tg_pending", {})[str(mid)] = entry
        daemon._save(latest)
    else:
        pending = ROOT / "pending_cal_event.json"
        entry = json.loads(pending.read_text())
        mid = daemon._tg_send_with_buttons(entry["proposal_text"], "cal", proposal=entry)
        if not mid:
            raise RuntimeError("Proposal delivery failed")
        entry["source_tg_msg_id"] = mid
        pending.write_text(json.dumps(entry, indent=2))
    print("Proposal delivered with a specific approval button.")


if __name__ == "__main__":
    main()
