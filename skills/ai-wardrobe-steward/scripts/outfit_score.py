#!/usr/bin/env python3
"""Deterministically rank complete outfit combinations from active items."""
import argparse, itertools, json, subprocess, sys
from datetime import date, datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent; SCRIPTS=Path(__file__).resolve().parent


def item_score(item, occasion, activity):
    s=0; reasons=[]
    if occasion in item.get("occasion_tags",[]): s+=18; reasons.append("occasion-tag")
    verdict=(item.get("palette_match") or {}).get("verdict")
    if verdict=="in-palette": s+=8
    elif verdict=="borderline": s+=2
    elif verdict=="reject": s-=12
    last=item.get("last_worn")
    if last:
        try:
            days=(date.today()-datetime.strptime(last,"%Y-%m-%d").date()).days
            if days<7: s-=12-days
        except ValueError: pass
    if item.get("condition")=="worn": s-=4
    policy=item.get("activity_policy") or {}
    if activity in policy.get("preferred_activities",[]): s+=16; reasons.append("activity-preferred")
    return s,reasons


sys.path.insert(0,str(SCRIPTS))
from learned_adjustments import learned  # noqa: E402
from pinned_looks import looks as pinned_looks, overlap_bonus  # noqa: E402


def recently_proposed(days=14):
    """How often each item was put in front of the wearer lately. A stylist rotates; ties must not always fall to the same piece."""
    path=ROOT/'profile/outfit_proposals.json'; counts={}
    for p in (json.loads(path.read_text()) if path.exists() else []):
        try: age=(datetime.now().astimezone()-datetime.fromisoformat(p.get('created_at',''))).days
        except (ValueError,TypeError): continue
        if 0<=age<days:
            for i in p.get('items',[]): counts[i]=counts.get(i,0)+1
    return counts


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--occasion",required=True); ap.add_argument("--season"); ap.add_argument("--activity",default="general"); ap.add_argument("--explicit-occasion",action="store_true"); ap.add_argument("--outdoor",action="store_true"); ap.add_argument("--feels-like-f",type=float); ap.add_argument("--limit",type=int,default=3)
    args=ap.parse_args(); cmd=[sys.executable,str(SCRIPTS/'outfit_candidates.py'),'--occasion',args.occasion]
    if args.season: cmd += ['--season',args.season]
    cmd += ['--activity',args.activity]
    if args.explicit_occasion: cmd += ['--explicit-occasion']
    if args.outdoor: cmd += ['--outdoor']
    if args.feels_like_f is not None: cmd += ['--feels-like-f',str(args.feels_like_f)]
    r=subprocess.run(cmd,capture_output=True,text=True); 
    if r.returncode: sys.exit(r.stdout+r.stderr)
    data=json.loads(r.stdout); c=data['candidates']; req=data.get('requirements',{}); occasion=data['occasion']
    slots=list(req.get('all',[])); one=req.get('one_of',[])
    if one:
        groups=[[x] if isinstance(x,str) else list(x) for x in one]
        available=next((g for g in groups if all(c.get(x) for x in g)),None)
        if not available: print(json.dumps({"outfits":[],"warnings":data['warnings']},indent=2)); return
        slots += available
    if any(not c.get(x) for x in slots): print(json.dumps({"outfits":[],"warnings":data['warnings']},indent=2)); return
    optional=[]
    if args.feels_like_f is not None and args.feels_like_f < 65 and c.get('outerwear'): optional=['outerwear']
    pools=[c[x][:20] for x in slots+optional]; recent=json.loads((ROOT/'profile/outfit_history.json').read_text())
    recent_sets={frozenset(h.get('items',[])):h.get('favorite',False) for h in recent[-30:]}
    ranked=[]; proposed=recently_proposed(); lessons=learned(args.activity,args.feels_like_f)
    # 2026-10-13: what he PINS finally reaches what he is shown. An outfit that puts together
    # pieces he has pinned together scores higher; see pinned_looks.py for why it is the best
    # single look rather than the sum.
    try: pinned=pinned_looks()
    except Exception: pinned=[]  # noqa: BLE001 - inspiration is a bonus, never a blocker
    rejected={frozenset(x['items']):x for x in lessons['combos']}
    for combo in itertools.product(*pools):
        ids=[x['id'] for x in combo]
        if len(ids)!=len(set(ids)): continue
        score=sum(item_score(x,occasion,args.activity)[0]-min(9,3*proposed.get(x['id'],0)) for x in combo)
        prior=recent_sets.get(frozenset(ids))
        if prior is True: score+=10
        elif prior is False: score-=15
        because=[{"item":i,**why} for i in ids for why in lessons['items'].get(i,[])]
        score+=sum(x['delta'] for x in because)
        pin_bonus,pin_why=overlap_bonus(ids,pinned)
        if pin_why: score+=pin_bonus; because.append(pin_why)
        if frozenset(ids) in rejected: score+=rejected[frozenset(ids)]['delta']; because.append({k:v for k,v in rejected[frozenset(ids)].items() if k!='items'})
        ranked.append({"score":score,"items":ids,"learned":because,"summary":[{"id":x['id'],"category":x.get('category'),"color":x.get('color')} for x in combo]})
    ranked.sort(key=lambda x:(-x['score'],x['items']))
    print(json.dumps({"occasion":occasion,"activity":args.activity,"requirements":req,"outfits":ranked[:args.limit],"warnings":data['warnings']},indent=2))


if __name__ == '__main__': main()
