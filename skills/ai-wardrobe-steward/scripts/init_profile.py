#!/usr/bin/env python3
"""Create an empty private profile for a fresh skill installation."""
import argparse,json
from pathlib import Path
import yaml
ap=argparse.ArgumentParser(); ap.add_argument('--target',default=str(Path(__file__).resolve().parent.parent/'profile')); a=ap.parse_args(); p=Path(a.target); p.mkdir(parents=True,exist_ok=True); (p/'photos/closet').mkdir(parents=True,exist_ok=True); (p/'photos/inspiration').mkdir(parents=True,exist_ok=True)
json_defaults={"closet.json":[],"outfit_history.json":[],"outfit_proposals.json":[],"style_signals.json":[],"shopping_watchlist.json":[],"product_history.json":[],"purchase_decisions.json":[],"purchase_lessons.json":[],"purchase_lifecycle.json":[],"source_health.json":{}}
for name,value in json_defaults.items():
 path=p/name
 if not path.exists(): path.write_text(json.dumps(value,indent=2))
comfort=p/'comfort_profile.yaml'
if not comfort.exists(): comfort.write_text(yaml.safe_dump({"version":1,"last_updated":None,"runs_temperature":"unknown","observations":[]},sort_keys=False))
print(f"Profile ready: {p}")
