#!/usr/bin/env python3
"""Track wardrobe orders through delivery, fit review, keep/return and deadlines."""
import argparse,json,sys
from datetime import date,datetime,timedelta
from pathlib import Path
PATH=Path(__file__).resolve().parent.parent/'profile/purchase_lifecycle.json'
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _store import load_json, save_json  # noqa: E402
def load(): return load_json(PATH, [])
def save(x): save_json(PATH, x)
ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
add=sub.add_parser('add'); add.add_argument('--retailer',required=True); add.add_argument('--item',required=True); add.add_argument('--order-id',required=True); add.add_argument('--ordered-date',default=date.today().isoformat()); add.add_argument('--return-days',type=int); add.add_argument('--decision-id',default='')
upd=sub.add_parser('update'); upd.add_argument('--id',required=True); upd.add_argument('--status',choices=['ordered','shipped','delivered','kept','returning','returned'],required=True); upd.add_argument('--date',default=date.today().isoformat()); upd.add_argument('--fit-verdict',choices=['great','acceptable','poor']); upd.add_argument('--note',default='')
sub.add_parser('due'); a=ap.parse_args(); rows=load()
if a.cmd=='add':
 pid='purchase-'+__import__('hashlib').sha256((a.retailer+'|'+a.order_id+'|'+a.item).encode()).hexdigest()[:12]
 if any(x['id']==pid for x in rows): raise SystemExit('Purchase already tracked.')
 deadline=(datetime.strptime(a.ordered_date,'%Y-%m-%d').date()+timedelta(days=a.return_days)).isoformat() if a.return_days else None
 rows.append({'id':pid,'retailer':a.retailer,'item':a.item,'order_id':a.order_id,'decision_id':a.decision_id,'status':'ordered','ordered_date':a.ordered_date,'return_deadline':deadline,'fit_verdict':None,'notes':[]}); save(rows); print(pid)
 # 2026-10-12: a real purchase closes the loop on the recommendation and the want behind it
 try:
  from purchase_match import close_loop
  linked=close_loop(a.retailer,a.item); rows=load(); row=next(x for x in rows if x['id']==pid)
  if linked.get('decision') and not row.get('decision_id'): row['decision_id']=linked['decision']
  row['want_id']=linked.get('want'); save(rows); print(json.dumps({'closed_loop':linked}))
 except Exception as exc: print(json.dumps({'closed_loop_error':str(exc)[:120]}))
elif a.cmd=='update':
 row=next((x for x in rows if x['id']==a.id),None)
 if not row: raise SystemExit('Unknown purchase id.')
 row['status']=a.status; row[a.status+'_date']=a.date
 if a.fit_verdict: row['fit_verdict']=a.fit_verdict
 if a.note: row['notes'].append({'date':a.date,'note':a.note})
 save(rows); print(f"Updated {a.id}: {a.status}")
 if a.status=='delivered':
  # 2026-10-12: ask how it fits (send_fit_prompt.py holds the question until fit prompts are enabled)
  import subprocess; r=subprocess.run([sys.executable,str(Path(__file__).resolve().parent/'send_fit_prompt.py'),'--purchase-id',a.id],capture_output=True,text=True); print(r.stdout.strip()[:200])
else:
 today=date.today(); due=[]
 for x in rows:
  if x['status'] in ('kept','returned'): continue
  deadline=x.get('return_deadline')
  if x['status']=='delivered' or (deadline and 0 <= (datetime.strptime(deadline,'%Y-%m-%d').date()-today).days <= 7): due.append(x)
 print(json.dumps(due,indent=2))
