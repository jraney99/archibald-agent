#!/usr/bin/env python3
"""Summarize behavioral style evidence without silently rewriting preferences."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    path = ROOT / "profile" / name
    return json.loads(path.read_text()) if path.exists() else []


signals = load("style_signals.json")
history = load("outfit_history.json")
closet = {x.get("id"): x for x in load("closet.json")}
accepted = Counter()
rejected = Counter()
accepted_combinations = Counter()
rejected_context = Counter()
for outfit in history:
    attrs=[]
    for item_id in outfit.get("items", []):
        item = closet.get(item_id, {})
        attrs.append(str(item.get("category") or item_id))
        for value in (item.get("category"), item.get("color"), item.get("brand")):
            if value: accepted[str(value)] += 1
    for pair in __import__('itertools').combinations(sorted(set(attrs)), 2): accepted_combinations[' + '.join(pair)] += 1
# style_signals.json carries several kinds of signal and only ONE of them is a rejection.
# 2026-10-13: this loop treated every row as a rejection, so a 👍 on a shopping find
# ("shopping-find-feedback", action "liked") would have taught the engine to AVOID what he
# liked. learned_adjustments.py and rule_proposals.py already filter the same way.
for signal in signals:
    if signal.get("source") != "outfit-proposal-response":
        continue
    rejected_context[f"{signal.get('occasion','unknown')}:{signal.get('activity','general')}:{signal.get('action','skip')}"] += 1
    for item_id in signal.get("items", []):
        item = closet.get(item_id, {})
        for value in (item.get("category"), item.get("color"), item.get("brand")):
            if value: rejected[str(value)] += 1
emerging = []
for value in set(accepted) | set(rejected):
    total = accepted[value] + rejected[value]
    if total >= 3:
        emerging.append({"value": value, "accepted": accepted[value], "rejected": rejected[value],
                         "direction": "positive" if accepted[value] > rejected[value] else "negative"})
print(json.dumps({"wear_events": len(history), "feedback_signals": len(signals),
                  "emerging_patterns": sorted(emerging, key=lambda x: x["accepted"] + x["rejected"], reverse=True),
                  "successful_combinations": [{"combination":k,"wears":v} for k,v in accepted_combinations.most_common() if v>=3],
                  "rejection_contexts": [{"context":k,"count":v} for k,v in rejected_context.most_common()]}, indent=2))
