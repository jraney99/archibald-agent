#!/usr/bin/env python3
"""Send post-event wear confirmations without waking a model."""
import json, subprocess, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
r=subprocess.run([sys.executable,str(HERE/'outfit_proposals.py'),'pending-wear'],capture_output=True,text=True)
if r.returncode: raise SystemExit(r.stdout+r.stderr)
pending=json.loads(r.stdout)
for item in pending[:3]:
    sent=subprocess.run([sys.executable,str(HERE/'send_outfit_proposal.py'),'--id',item['id'],'--post-event'],capture_output=True,text=True)
    if sent.returncode: raise SystemExit(sent.stdout+sent.stderr)
if pending: print(json.dumps({"wear_prompts_sent":len(pending[:3])}))
