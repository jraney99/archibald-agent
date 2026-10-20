#!/usr/bin/env python3
"""Mark one-wear washable garments dirty after a confirmed wear."""
import argparse,json
from datetime import date
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parent.parent; CLOSET=ROOT/'profile/closet.json'; RULES=ROOT/'care_heuristics.yaml'
ap=argparse.ArgumentParser(); ap.add_argument('--items',required=True); a=ap.parse_args(); ids=set(x.strip() for x in a.items.split(',') if x.strip())
closet=json.loads(CLOSET.read_text()); rules=yaml.safe_load(RULES.read_text()).get('by_category',{}); changed=[]
for item in closet:
 if item.get('id') in ids and item.get('lifecycle_state')=='active' and rules.get(item.get('category'),{}).get('interval_wears')==1:
  item['lifecycle_state']='dirty'; item['dirty_since']=item.get('last_worn') or date.today().isoformat(); changed.append(item['id'])  # laundry_day.py reads this
CLOSET.write_text(json.dumps(closet,indent=2)); print(json.dumps({'marked_dirty':changed}))
