#!/usr/bin/env python3
"""Resolve and maintain activity-specific garment usage policies."""
import argparse,json,sys
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parent.parent; CLOSET=ROOT/'profile/closet.json'; OVERRIDES=ROOT/'profile/activity_overrides.json'; PREFERENCES=ROOT/'profile/activity_preferences.json'; VOCAB=ROOT/'vocab.yaml'


def load_json(path, default): return json.loads(path.read_text()) if path.exists() else default


def activity_preference(activity):
    """The wearer's own stated way of dressing for an activity (occasion tier + slot categories), or {}.

    vocab.yaml holds the generic convention; this is the personal layer over it and wins when present.
    """
    return load_json(PREFERENCES,{}).get('activities',{}).get(activity,{})


def comfort_rules():
    """Personal comfort rules that cut across activities (e.g. no long sleeves in heat), or {}."""
    return load_json(PREFERENCES,{}).get('comfort_rules',{})


def venue_aliases():
    """Personal venue phrase -> activity, e.g. a named simulator lounge. Merged over vocab activity_aliases."""
    return {phrase:row['activity'] for phrase,row in load_json(PREFERENCES,{}).get('venue_aliases',{}).items()}


def inferred_policy(item):
    text=' '.join(str(item.get(k,'')) for k in ('id','brand','model','notes')).lower(); category=item.get('category')
    policy={"allowed_activities":[],"preferred_activities":[],"excluded_activities":[],"activity_exclusive":False,"source":"default"}
    # "golf" in the name marks a functionally different garment for pants, shorts, shoes and layers.
    # A polo is golf-appropriate whatever its label says, so a brand called "Polo Golf" must not outrank every other polo.
    if 'golf' in text and category!='polo':
        policy['preferred_activities']=['golf-round','driving-range']
        policy['source']='catalog-inference'
    if category=='specialty_footwear' and 'golf' in text:
        policy.update({"allowed_activities":['golf-round','driving-range'],"preferred_activities":['golf-round'],"excluded_activities":['golf-simulator','golf-clubhouse','golf-spectator','general','work','formal-event'],"activity_exclusive":True,"source":"specialty-category"})
    elif category in ('activewear','athletic_shoes'):
        policy['preferred_activities']=['gym','running']; policy['source']='category-default'
    return policy


def effective_policy(item, overrides=None):
    overrides=overrides if overrides is not None else load_json(OVERRIDES,{})
    base=inferred_policy(item); explicit=overrides.get(item.get('id'),{})
    if explicit: base.update(explicit); base['source']='explicit-override'
    return base


def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    show=sub.add_parser('show'); show.add_argument('--item'); show.add_argument('--activity')
    setp=sub.add_parser('set'); setp.add_argument('--item',required=True); setp.add_argument('--allowed',default=''); setp.add_argument('--preferred',default=''); setp.add_argument('--excluded',default=''); setp.add_argument('--exclusive',choices=['true','false'],required=True); setp.add_argument('--evidence',required=True)
    clear=sub.add_parser('clear'); clear.add_argument('--item',required=True)
    prefer=sub.add_parser('prefer'); prefer.add_argument('--activity',required=True); prefer.add_argument('--occasion'); prefer.add_argument('--slot',action='append',default=[],help='slot=cat1,cat2 (repeatable)'); prefer.add_argument('--slot-temp',action='append',default=[],help='slot:threshold_f:below_cats:at_or_above_cats, e.g. bottoms:80:pants:shorts'); prefer.add_argument('--guidance',help='soft styling judgment the filters cannot express'); prefer.add_argument('--merge',action='store_true',help='keep the existing rule and change only the fields passed'); prefer.add_argument('--evidence',required=True)
    heat=sub.add_parser('heat-rule'); heat.add_argument('--limit-f',type=float,required=True); heat.add_argument('--dressy-indoor-min-formality',type=int,help='long sleeves stay allowed at/above this formality when indoors and not exerting; omit for never'); heat.add_argument('--evidence',required=True)
    venue=sub.add_parser('venue'); venue.add_argument('--phrase',required=True); venue.add_argument('--activity',required=True); venue.add_argument('--evidence',required=True)
    sub.add_parser('preferences')
    a=ap.parse_args(); closet=load_json(CLOSET,[]); by={x.get('id'):x for x in closet}; overrides=load_json(OVERRIDES,{})
    with open(VOCAB) as f: vocab=yaml.safe_load(f)
    valid=set(vocab['activities'])
    if a.cmd=='preferences':
        print(json.dumps(load_json(PREFERENCES,{}),indent=2)); return
    if a.cmd=='heat-rule':
        prefs=load_json(PREFERENCES,{}); prefs.setdefault('comfort_rules',{})['long_sleeve_heat']={"limit_f":a.limit_f,"dressy_indoor_min_formality":a.dressy_indoor_min_formality,"evidence":a.evidence}
        PREFERENCES.write_text(json.dumps(prefs,indent=2)); print(json.dumps(prefs['comfort_rules'],indent=2)); return
    if a.cmd in ('prefer','venue'):
        if a.activity not in valid: sys.exit('Unknown activity: '+a.activity)
        prefs=load_json(PREFERENCES,{})
        if a.cmd=='venue':
            prefs.setdefault('venue_aliases',{})[a.phrase.strip().lower()]={"activity":a.activity,"evidence":a.evidence}
        else:
            if a.occasion and a.occasion not in vocab['occasion_formality']: sys.exit('Unknown occasion: '+a.occasion)
            slots={}
            for pair in a.slot:
                slot,_,cats=pair.partition('='); cats=[x.strip() for x in cats.split(',') if x.strip()]
                if slot not in vocab['outfit_slots'] or not cats: sys.exit('Bad --slot: '+pair)
                bad=set(cats)-set(vocab['outfit_slots'][slot])
                if bad: sys.exit(f"Categories not in slot '{slot}': "+', '.join(sorted(bad)))
                slots[slot]=cats
            temps={}
            for spec in a.slot_temp:
                parts=spec.split(':')
                if len(parts)!=4 or parts[0] not in vocab['outfit_slots']: sys.exit('Bad --slot-temp: '+spec)
                slot=parts[0]; below,above=([x.strip() for x in p.split(',') if x.strip()] for p in parts[2:])
                try: threshold=float(parts[1])
                except ValueError: sys.exit('Bad --slot-temp threshold: '+spec)
                bad=(set(below)|set(above))-set(vocab['outfit_slots'][slot])
                if bad or not below or not above: sys.exit(f"Bad --slot-temp categories for '{slot}': "+spec)
                temps[slot]={"threshold_f":threshold,"below":below,"at_or_above":above}
            existing=prefs.get('activities',{}).get(a.activity,{}) if a.merge else {}
            row={**existing,"evidence":' | '.join(x for x in (existing.get('evidence'),a.evidence) if x)}
            if a.occasion: row['occasion']=a.occasion
            if slots: row['slot_categories']=slots
            if temps: row['temperature_slot_categories']=temps
            if a.guidance: row['guidance']=a.guidance
            prefs.setdefault('activities',{})[a.activity]=row
        PREFERENCES.write_text(json.dumps(prefs,indent=2)); print(json.dumps(prefs,indent=2)); return
    if a.cmd=='set':
        if a.item not in by: sys.exit('Unknown closet item.')
        parse=lambda s:[x.strip() for x in s.split(',') if x.strip()]
        vals={"allowed_activities":parse(a.allowed),"preferred_activities":parse(a.preferred),"excluded_activities":parse(a.excluded)}
        bad=set(sum(vals.values(),[]))-valid
        if bad: sys.exit('Unknown activities: '+', '.join(sorted(bad)))
        overrides[a.item]={**vals,"activity_exclusive":a.exclusive=='true',"evidence":a.evidence}; OVERRIDES.write_text(json.dumps(overrides,indent=2)); print(json.dumps(overrides[a.item],indent=2)); return
    if a.cmd=='clear':
        overrides.pop(a.item,None); OVERRIDES.write_text(json.dumps(overrides,indent=2)); print('Cleared.'); return
    rows=[]
    for item in closet:
        if a.item and item.get('id')!=a.item: continue
        policy=effective_policy(item,overrides)
        if a.activity and a.activity not in policy['allowed_activities']+policy['preferred_activities']+policy['excluded_activities']: continue
        rows.append({"id":item.get('id'),"category":item.get('category'),"policy":policy})
    print(json.dumps(rows,indent=2))


if __name__=='__main__': main()
