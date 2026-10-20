#!/usr/bin/env python3
"""Wake wardrobe packing only for newly detected calendar travel."""
import argparse,hashlib,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent; FETCH=ROOT.parent/'shared/fetch_calendar.py'; STATE=ROOT/'state/travel_watch.json'; CHAT='1000000001'
ap=argparse.ArgumentParser(); ap.add_argument('--dry-run',action='store_true'); args=ap.parse_args()
r=subprocess.run([sys.executable,str(FETCH),'--days','21','--json'],capture_output=True,text=True,timeout=60)
if r.returncode: raise SystemExit(r.stdout+r.stderr)
events=json.loads(r.stdout).get('events',[]); words=('flight','hotel','trip','travel','depart','arrival','vacation')
matches=[]
for e in events:
 text=' '.join(str(e.get(k,'')) for k in ('title','description','location')).lower()
 if any(w in text for w in words):
  e['travel_key']=hashlib.sha256((e.get('event_id','')+'|'+e.get('start_iso','')+'|'+text).encode()).hexdigest()[:20]; matches.append(e)
state=json.loads(STATE.read_text()) if STATE.exists() else {'dispatched':[]}; known=set(state['dispatched']); fresh=[e for e in matches if e['travel_key'] not in known]
if fresh:
 if args.dry_run:
  print(json.dumps({'new_travel_events':fresh},indent=2)); raise SystemExit(0)
 payload=ROOT/'state/travel_batch.json'; payload.write_text(json.dumps(fresh,indent=2))
 prompt=f'''Use ai-wardrobe-steward Travel Packing for the new calendar travel evidence in {payload}. Resolve destination/dates only from verified event data and connected itinerary sources. Get destination weather, infer required occasions cautiously, run travel_packing.py, and propose a compact reusable capsule with actual active closet items and photos. Ask one concise question if destination or activities are materially ambiguous. Never book, purchase, or alter travel. Send at most one Telegram message.'''
 oc=shutil.which('openclaw') or 'openclaw'; run=subprocess.run([oc,'agent','--agent','main','--session-key','agent:main:wardrobe-travel','--channel','telegram','--reply-channel','telegram','--reply-to',CHAT,'--message',prompt,'--timeout','600','--json'],capture_output=True,text=True,timeout=660)
 if run.returncode: raise SystemExit(run.stderr or run.stdout)
 known.update(e['travel_key'] for e in fresh)
state['dispatched']=list(known)[-200:]; STATE.parent.mkdir(parents=True,exist_ok=True); STATE.write_text(json.dumps(state,indent=2))
if fresh: print(json.dumps({'travel_events_dispatched':len(fresh)}))
