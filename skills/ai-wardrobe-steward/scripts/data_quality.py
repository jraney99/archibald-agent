#!/usr/bin/env python3
"""Compact readiness audit for outfit, learning and shopping automation."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    path = ROOT / "profile" / name
    return json.loads(path.read_text()) if path.exists() else []


closet = load("closet.json")
active = [x for x in closet if x.get("lifecycle_state") == "active"]
history = load("outfit_history.json")
decisions = load("purchase_decisions.json")
issues = {
    "active_items_without_photo": [x.get("id") for x in active if not x.get("photo") or not (ROOT / str(x.get("photo", ""))).exists()],
    "items_without_size": [x.get("id") for x in closet if not x.get("size")],
    "active_items_without_palette_match": [x.get("id") for x in active if not x.get("palette_match")],
    "items_without_purchase_price": [x.get("id") for x in closet if x.get("purchase_price") is None],
    "shopping_decisions_without_outcome": [x.get("id") for x in decisions if x.get("user_action", "unknown") == "unknown"],
}
print(json.dumps({"closet_items": len(closet), "active_items": len(active), "confirmed_wear_events": len(history),
                  "issues": issues}, indent=2))
