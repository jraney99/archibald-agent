#!/usr/bin/env python3
"""Eval harness for the AI Wardrobe Steward skill (DESIGN.md's "still open
before build" test-framework question, resolved: scripted scenarios over
synthetic data, not unit tests over internal functions — this skill's real
logic lives in how the scripts behave against closet/profile data, so that's
what's tested).

Every test resets profile/ from a pristine backup first, so tests can't leak
state into each other. The real profile/ directory is backed up once at the
start and restored once at the end, no matter what happens in between.

Usage: python3 tests/run_eval.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "profile"
SCRIPTS_DIR = ROOT / "scripts"

TESTS = []


def test(name):
    def decorator(fn):
        TESTS.append((name, fn))
        return fn
    return decorator


def run(script, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        capture_output=True, text=True, env={**os.environ, "WARDROBE_NO_SEND": "1", "WARDROBE_NO_NETWORK": "1", "TELEGRAM_SEND_DISABLED": "1",
                                              "WARDROBE_STATE_DIR": str(PROFILE_DIR / "_state")},
    )


def write_json(relpath, data):
    with open(PROFILE_DIR / relpath, "w") as f:
        json.dump(data, f, indent=2)


def read_json(relpath):
    with open(PROFILE_DIR / relpath) as f:
        return json.load(f)


def write_yaml(relpath, data):
    with open(PROFILE_DIR / relpath, "w") as f:
        yaml.dump(data, f, sort_keys=False)


def read_yaml(relpath):
    with open(PROFILE_DIR / relpath) as f:
        return yaml.safe_load(f)


BASIC_ITEM = {
    "id": "test-item", "category": "shirt", "color": "white", "material": "cotton",
    "formality": 3, "season": "all-season", "lifecycle_state": "active",
    "condition": "good", "wear_count": 0, "added_via": "manual", "added_date": "2026-01-01",
}


# Canonical empty state for the accumulating profile files. The tests assume a
# blank profile (version 0, empty lists); the live instance this repo doubles
# as accumulates real data into those same files, so each test resets them
# here rather than trusting whatever happens to be committed. color_analysis.yaml
# is deliberately NOT in this list — it legitimately ships populated (expert
# reference data), and several tests read the real palette.
_EMPTY_MEASUREMENTS = {k: None for k in (
    "height", "weight", "chest", "waist", "inseam", "shoe_size",
    "shoulder_width", "sleeve_length", "neck", "hip", "thigh", "measured_date")}
CANONICAL_EMPTY = {
    "style_profile.yaml": {
        "version": 0, "last_updated": None,
        "calibration_last_offered": None, "closet_catalog_last_offered": None,
        "style_categories": [], "preferred_colors": [], "preferred_materials": [],
        "preferred_brands": [], "fit_notes": None, "desired_persona": None,
        "avoidances": [], "inspirations": [], "change_log": [],
    },
    "style_identity.yaml": {
        "version": 0, "last_updated": None, "archetype": None,
        "pillars": [], "desired_impression": [], "anti_identity": [], "change_log": [],
    },
    "body_fit_profile.yaml": {
        "version": 0, "last_updated": None, "units": None,
        "measurements": dict(_EMPTY_MEASUREMENTS),
        "fit_preferences": {"tops": None, "outerwear": None,
                            "bottoms": {"rise": None, "leg_opening": None, "length": None, "break": None}},
        "brand_sizing": [], "change_log": [],
    },
    "closet.json": [],
    "outfit_history.json": [],
    "purchase_lessons.json": [],
    "purchase_decisions.json": [],
    "purchase_lifecycle.json": [],
    "product_history.json": [],
    "outfit_proposals.json": [],
    "style_signals.json": [],
    "source_health.json": {},
    "activity_overrides.json": {},
    "activity_preferences.json": {},
    "rule_proposals.json": {},
    "laundry.json": {"laundry_days": [], "max_dirty_days": 10},
    "people.json": {"self_emails": [], "internal_domains": [], "people": {}},
    "outfit_questions.json": {},
    "triage_preferences.json": {},
    "venues.json": {},
    "shopping_watchlist.json": [],
    "pending_style_update.json": {},
    "pending_style_identity_update.json": {},
    "pending_body_fit_update.json": {},
    "pending_color_analysis_update.json": {},
    "pending_closet_additions.json": [],
    "pending_purchase_lessons.json": [],
    "comfort_profile.yaml": {"version": 1, "last_updated": None, "runs_temperature": "unknown", "observations": []},
}


def reset_profile():
    """Restore the accumulating profile files to a blank state before a test."""
    for relpath, content in CANONICAL_EMPTY.items():
        path = PROFILE_DIR / relpath
        if relpath.endswith(".json"):
            path.write_text(json.dumps(content, indent=2))
        else:
            with open(path, "w") as f:
                yaml.dump(content, f, sort_keys=False)


def item(**overrides):
    d = dict(BASIC_ITEM)
    d.update(overrides)
    return d


# ---------- validate_closet.py ----------

@test("validate_closet: valid item passes")
def _():
    write_json("closet.json", [item()])
    r = run("validate_closet.py")
    assert r.returncode == 0, r.stdout + r.stderr


@test("validate_closet: missing required field fails")
def _():
    bad = item()
    del bad["season"]
    write_json("closet.json", [bad])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "season" in r.stdout


@test("validate_closet: invalid category fails")
def _():
    write_json("closet.json", [item(category="spaceship")])
    r = run("validate_closet.py")
    assert r.returncode == 1


@test("validate_closet: invalid formality fails")
def _():
    write_json("closet.json", [item(formality=9)])
    r = run("validate_closet.py")
    assert r.returncode == 1


@test("validate_closet: missing/null formality is allowed, not required (a real intake gap, not an error)")
def _():
    # Reproduces a real live bug: confirm_closet_item.py wrote a legitimately
    # incomplete draft (formality unknown at intake time), and the
    # post-write validate_closet.py call immediately failed on it — every
    # other formality-reading script (outfit_candidates.py, travel_packing.py,
    # closet_report.py) already treats a missing formality as a normal,
    # gracefully-handled gap, not an error, so the validator must agree.
    missing_key = item()
    del missing_key["formality"]
    write_json("closet.json", [missing_key])
    assert run("validate_closet.py").returncode == 0

    null_value = item(formality=None)
    write_json("closet.json", [null_value])
    assert run("validate_closet.py").returncode == 0


@test("validate_closet: duplicate id fails")
def _():
    write_json("closet.json", [item(), item()])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "duplicate id" in r.stdout


@test("validate_closet: non-list top level fails cleanly, no traceback")
def _():
    with open(PROFILE_DIR / "closet.json", "w") as f:
        json.dump({"oops": True}, f)
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "Traceback" not in r.stdout + r.stderr


@test("validate_closet: estimated_frequency accepted from vocab, rejected otherwise")
def _():
    write_json("closet.json", [item(estimated_frequency="regularly")])
    assert run("validate_closet.py").returncode == 0

    write_json("closet.json", [item(estimated_frequency="all the time")])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "estimated_frequency" in r.stdout


@test("validate_closet: disposition requires lifecycle_state retired")
def _():
    write_json("closet.json", [item(lifecycle_state="retired", disposition="donated")])
    assert run("validate_closet.py").returncode == 0

    write_json("closet.json", [item(lifecycle_state="active", disposition="donated")])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "not 'retired'" in r.stdout


@test("validate_closet: invalid disposition rejected, boolean/non-numeric disposition_value rejected")
def _():
    write_json("closet.json", [item(lifecycle_state="retired", disposition="thrown-in-the-ocean")])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "disposition_types" in r.stdout

    write_json("closet.json", [item(lifecycle_state="retired", disposition="sold", disposition_value=True)])
    r2 = run("validate_closet.py")
    assert r2.returncode == 1
    assert "disposition_value" in r2.stdout

    write_json("closet.json", [item(lifecycle_state="retired", disposition="sold", disposition_value=40)])
    assert run("validate_closet.py").returncode == 0


@test("validate_closet: malformed disposition_date rejected")
def _():
    write_json("closet.json", [item(lifecycle_state="retired", disposition="donated", disposition_date="not-a-date")])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "disposition_date" in r.stdout


# ---------- closet_dedup.py ----------

@test("closet_dedup: exact match found")
def _():
    write_json("closet.json", [item(id="wf-navy-blazer", category="blazer", brand="J.Crew", color="navy")])
    r = run("closet_dedup.py", "--category", "blazer", "--brand", "J.Crew", "--color", "navy")
    assert "wf-navy-blazer" in r.stdout


@test("closet_dedup: color-variant match found (navy / navy blue)")
def _():
    write_json("closet.json", [item(id="wf-navy-blazer", category="blazer", brand="J.Crew", color="navy")])
    r = run("closet_dedup.py", "--category", "blazer", "--brand", "J.Crew", "--color", "navy blue")
    assert "wf-navy-blazer" in r.stdout


@test("closet_dedup: no match on different category")
def _():
    write_json("closet.json", [item(id="wf-navy-blazer", category="blazer", color="navy")])
    r = run("closet_dedup.py", "--category", "shirt", "--color", "navy")
    assert "No likely duplicates" in r.stdout


@test("closet_dedup: empty closet, no crash")
def _():
    write_json("closet.json", [])
    r = run("closet_dedup.py", "--category", "shirt", "--color", "white")
    assert r.returncode == 0


# ---------- confirm/cancel closet item ----------

@test("confirm_closet_item: valid confirm moves item and validates")
def _():
    write_json("closet.json", [])
    write_json("pending_closet_additions.json", [item(review_summary="test")])
    r = run("confirm_closet_item.py", "--all")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(read_json("closet.json")) == 1
    assert read_json("pending_closet_additions.json") == []


@test("confirm_closet_item: a real intake gap (formality genuinely unknown at confirm time) confirms cleanly")
def _():
    # This is the exact real-world shape: brand/material/size/price/formality
    # all still unknown, flagged, confirmed as-is per the "gaps included,
    # fill in later" design — should never fail validation right after a
    # successful write.
    write_json("closet.json", [])
    draft = item(
        id="wf-navy-blazer", brand=None, material=None, size=None, purchase_price=None, formality=None,
        flagged_fields=["brand", "material", "size", "purchase_price", "formality"],
        review_summary="Navy blazer purchased today",
    )
    write_json("pending_closet_additions.json", [draft])
    r = run("confirm_closet_item.py", "wf-navy-blazer")
    assert r.returncode == 0, r.stdout + r.stderr
    assert read_json("closet.json")[0]["formality"] is None


@test("confirm_closet_item: item missing id rejected, nothing written")
def _():
    write_json("closet.json", [])
    bad = item()
    del bad["id"]
    write_json("pending_closet_additions.json", [bad])
    r = run("confirm_closet_item.py", "--all")
    assert r.returncode == 1
    assert read_json("closet.json") == []
    assert len(read_json("pending_closet_additions.json")) == 1


@test("confirm_closet_item: confirming nonexistent id errors cleanly")
def _():
    write_json("closet.json", [])
    write_json("pending_closet_additions.json", [])
    r = run("confirm_closet_item.py", "some-id")
    assert r.returncode == 1


@test("confirm_closet_item: two pending drafts sharing an id refused before writing, not caught after")
def _():
    write_json("closet.json", [])
    write_json("pending_closet_additions.json", [
        item(id="dup-item", category="shirt", review_summary="first"),
        item(id="dup-item", category="pants", review_summary="second"),
    ])
    r = run("confirm_closet_item.py", "dup-item")
    assert r.returncode == 1
    assert "dup-item" in r.stdout + r.stderr
    assert read_json("closet.json") == []


@test("confirm_closet_item: confirming an id that already exists in closet.json refused before writing")
def _():
    write_json("closet.json", [item(id="existing-id")])
    write_json("pending_closet_additions.json", [item(id="existing-id", review_summary="oops same id")])
    r = run("confirm_closet_item.py", "existing-id")
    assert r.returncode == 1
    assert len(read_json("closet.json")) == 1


@test("cancel_closet_item: removes from pending without writing to closet")
def _():
    write_json("closet.json", [])
    write_json("pending_closet_additions.json", [item()])
    r = run("cancel_closet_item.py", "--all")
    assert r.returncode == 0
    assert read_json("closet.json") == []
    assert read_json("pending_closet_additions.json") == []


# ---------- confirm/cancel style update ----------

@test("confirm_style_update: valid diff applies and version bumps")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "add": {"style_categories": [{"value": "workwear", "confidence": 75, "importance": 60}]}})
    r = run("confirm_style_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    p = read_yaml("style_profile.yaml")
    assert p["version"] == 1
    assert p["style_categories"] == [{"value": "workwear", "confidence": 75, "importance": 60}]


@test("confirm_style_update: set on a meta date field (closet_catalog_last_offered) writes a plain string, not the scored-entry shape")
def _():
    write_json("pending_style_update.json", {"review_summary": "offered closet cataloging", "set": {"closet_catalog_last_offered": "2026-07-23"}})
    r = run("confirm_style_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    p = read_yaml("style_profile.yaml")
    assert p["closet_catalog_last_offered"] == "2026-07-23"


@test("confirm_style_update: meta date field rejects a non-string value before writing")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "set": {"calibration_last_offered": 12345}})
    r = run("confirm_style_update.py")
    assert r.returncode == 1
    p = read_yaml("style_profile.yaml")
    assert p["calibration_last_offered"] is None


@test("confirm_style_update: add reinforces an existing value (confidence/importance/evidence) instead of duplicating")
def _():
    write_json("pending_style_update.json", {"review_summary": "t1", "add": {"preferred_colors": [
        {"value": "navy", "confidence": 55, "importance": 50, "evidence": [{"date": "2026-07-13", "note": "wore a navy blazer"}]}
    ]}})
    run("confirm_style_update.py")
    write_json("pending_style_update.json", {"review_summary": "t2", "add": {"preferred_colors": [
        {"value": "navy", "confidence": 95, "importance": 90, "evidence": [{"date": "2026-07-14", "note": "bought a third navy item"}]}
    ]}})
    r = run("confirm_style_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    p = read_yaml("style_profile.yaml")
    assert len(p["preferred_colors"]) == 1
    entry = p["preferred_colors"][0]
    assert entry["confidence"] == 95 and entry["importance"] == 90
    assert len(entry["evidence"]) == 2


@test("confirm_style_update: reinforcement match ignores surrounding whitespace, doesn't create a near-duplicate")
def _():
    write_json("pending_style_update.json", {"review_summary": "t1", "add": {"preferred_colors": [
        {"value": "navy", "confidence": 55, "importance": 50}
    ]}})
    run("confirm_style_update.py")
    write_json("pending_style_update.json", {"review_summary": "t2", "add": {"preferred_colors": [
        {"value": "navy ", "confidence": 90, "importance": 80}
    ]}})
    r = run("confirm_style_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    colors = read_yaml("style_profile.yaml")["preferred_colors"]
    assert len(colors) == 1, f"expected reinforcement, got {colors!r}"
    assert colors[0]["confidence"] == 90


@test("confirm_style_update: add entry missing importance rejected")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "add": {"preferred_colors": [{"value": "olive", "confidence": 80}]}})
    r = run("confirm_style_update.py")
    assert r.returncode == 1


@test("confirm_style_update: boolean confidence/importance rejected BEFORE writing, not just caught by the post-write validator")
def _():
    # Python's bool is a subclass of int, so a naive `isinstance(n, int)`
    # check would accept JSON `true`/`false` as a valid 0-100 score. Checking
    # only the exit code here would be satisfied even if confirm's own gate
    # let this through and only the separate post-write validate_style_profile.py
    # call caught it after the bad value was already written — this asserts
    # the write itself never happened (version stays 0), which is what
    # actually matters (mutation testing caught this exact gap).
    write_json("pending_style_update.json", {"review_summary": "t", "add": {"preferred_colors": [{"value": "teal", "confidence": True, "importance": 60}]}})
    assert run("confirm_style_update.py").returncode == 1
    assert read_yaml("style_profile.yaml")["version"] == 0
    assert read_yaml("style_profile.yaml")["preferred_colors"] == []

    write_json("pending_style_update.json", {"review_summary": "t", "add": {"preferred_colors": [{"value": "teal", "confidence": 60, "importance": False}]}})
    assert run("confirm_style_update.py").returncode == 1
    assert read_yaml("style_profile.yaml")["version"] == 0
    assert read_yaml("style_profile.yaml")["preferred_colors"] == []


@test("confirm_style_update: empty/whitespace-only value rejected BEFORE writing, not just caught by the post-write validator")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "add": {"preferred_colors": [{"value": "", "confidence": 80, "importance": 60}]}})
    assert run("confirm_style_update.py").returncode == 1
    assert read_yaml("style_profile.yaml")["preferred_colors"] == []

    write_json("pending_style_update.json", {"review_summary": "t", "add": {"preferred_colors": [{"value": "   ", "confidence": 80, "importance": 60}]}})
    assert run("confirm_style_update.py").returncode == 1
    assert read_yaml("style_profile.yaml")["preferred_colors"] == []


@test("confirm_style_update: add entry missing confidence rejected")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "add": {"preferred_colors": [{"value": "olive"}]}})
    r = run("confirm_style_update.py")
    assert r.returncode == 1


@test("confirm_style_update: add entry with out-of-range confidence rejected")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "add": {"preferred_colors": [{"value": "olive", "confidence": 150}]}})
    r = run("confirm_style_update.py")
    assert r.returncode == 1


@test("confirm_style_update: add on non-list field rejected")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "add": {"fit_notes": ["x"]}})
    r = run("confirm_style_update.py")
    assert r.returncode == 1
    assert read_yaml("style_profile.yaml")["version"] == 0


@test("confirm_style_update: remove on non-list field rejected")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "remove": {"desired_persona": ["x"]}})
    r = run("confirm_style_update.py")
    assert r.returncode == 1


@test("confirm_style_update: set with wrong type on list field rejected")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "set": {"preferred_colors": "olive"}})
    r = run("confirm_style_update.py")
    assert r.returncode == 1


@test("confirm_style_update: unknown field rejected")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "set": {"nonexistent_field": "x"}})
    r = run("confirm_style_update.py")
    assert r.returncode == 1


@test("cancel_style_update: missing pending file doesn't crash")
def _():
    (PROFILE_DIR / "pending_style_update.json").unlink()
    r = run("cancel_style_update.py")
    assert r.returncode == 0
    assert "Traceback" not in r.stdout + r.stderr


# ---------- confirm/cancel body_fit update ----------

@test("confirm_body_fit_update: valid diff applies (nested set + brand_sizing add)")
def _():
    diff = {
        "review_summary": "t",
        "set": {"units": "imperial", "measurements.chest": {"value": 40, "confidence": 85, "importance": 50}},
        "add": {"brand_sizing": [{"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": 70}]},
    }
    write_json("pending_body_fit_update.json", diff)
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    p = read_yaml("body_fit_profile.yaml")
    assert p["measurements"]["chest"] == {"value": 40, "confidence": 85, "importance": 50}
    assert len(p["brand_sizing"]) == 1


@test("confirm_body_fit_update: brand_sizing add reinforces existing (brand, category) pair")
def _():
    write_json("pending_body_fit_update.json", {"review_summary": "t1", "add": {"brand_sizing": [{"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": 55}]}})
    run("confirm_body_fit_update.py")
    write_json("pending_body_fit_update.json", {"review_summary": "t2", "add": {"brand_sizing": [{"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": 95}]}})
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    p = read_yaml("body_fit_profile.yaml")
    assert len(p["brand_sizing"]) == 1
    assert p["brand_sizing"][0]["confidence"] == 95


@test("confirm_body_fit_update: measurement missing confidence rejected")
def _():
    write_json("pending_body_fit_update.json", {"review_summary": "t", "set": {"measurements.waist": {"value": 32}}})
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 1


@test("confirm_body_fit_update: path typo rejected")
def _():
    write_json("pending_body_fit_update.json", {"review_summary": "t", "set": {"measurements.chset": 40}})
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 1
    assert "chset" not in read_yaml("body_fit_profile.yaml")["measurements"]


@test("confirm_body_fit_update: enum violation rejected")
def _():
    write_json("pending_body_fit_update.json", {"review_summary": "t", "set": {"fit_preferences.tops": "baggy"}})
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 1


@test("confirm_body_fit_update: bad units rejected")
def _():
    write_json("pending_body_fit_update.json", {"review_summary": "t", "set": {"units": "furlongs"}})
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 1


@test("confirm_body_fit_update: set brand_sizing with scalar rejected")
def _():
    write_json("pending_body_fit_update.json", {"review_summary": "t", "set": {"brand_sizing": "oops"}})
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 1


@test("cancel_body_fit_update: missing pending file doesn't crash")
def _():
    (PROFILE_DIR / "pending_body_fit_update.json").unlink()
    r = run("cancel_body_fit_update.py")
    assert r.returncode == 0
    assert "Traceback" not in r.stdout + r.stderr


# ---------- outfit_candidates.py ----------

@test("outfit_candidates: candidates grouped by slot correctly")
def _():
    write_json("closet.json", [
        item(id="shirt1", category="shirt", formality=3),
        item(id="pants1", category="pants", formality=3),
        item(id="shoes1", category="loafers", formality=3),
    ])
    r = run("outfit_candidates.py", "--occasion", "business-casual")
    data = json.loads(r.stdout)
    assert "shirt1" in [i["id"] for i in data["candidates"]["base_top"]]
    assert data["warnings"] == []


@test("outfit_candidates: missing formality item skipped and flagged")
def _():
    no_formality = item(id="mystery")
    del no_formality["formality"]
    write_json("closet.json", [no_formality])
    r = run("outfit_candidates.py", "--occasion", "casual")
    data = json.loads(r.stdout)
    assert any("missing or invalid formality" in w for w in data["warnings"])


@test("outfit_candidates: non-numeric formality skipped and flagged, not a crash")
def _():
    write_json("closet.json", [item(id="junk", formality="high")])
    r = run("outfit_candidates.py", "--occasion", "casual")
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert any("missing or invalid formality" in w for w in data["warnings"])


@test("outfit_candidates: full_body doesn't fake-cover footwear")
def _():
    write_json("closet.json", [item(id="suit1", category="suit", formality=4)])
    r = run("outfit_candidates.py", "--occasion", "business-formal")
    data = json.loads(r.stdout)
    assert any("footwear" in w for w in data["warnings"])


@test("outfit_candidates: athletic_wear covers top and bottoms")
def _():
    write_json("closet.json", [
        item(id="tracksuit", category="activewear", formality=1),
        item(id="shoes1", category="athletic_shoes", formality=1),
    ])
    r = run("outfit_candidates.py", "--occasion", "athletic")
    data = json.loads(r.stdout)
    assert data["warnings"] == []


# ---------- outfit_history.py ----------

@test("outfit_history: log bumps wear_count and flags first wear")
def _():
    write_json("closet.json", [item(id="shirt1", wear_count=0)])
    write_json("outfit_history.json", [])
    r = run("outfit_history.py", "log", "--occasion", "casual", "--items", "shirt1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "First wear" in r.stdout
    assert read_json("closet.json")[0]["wear_count"] == 1


@test("outfit_history: log rejects an occasion not in vocab.yaml's occasion_tiers")
def _():
    write_json("closet.json", [item(id="shirt1", wear_count=0)])
    write_json("outfit_history.json", [])
    r = run("outfit_history.py", "log", "--occasion", "made-up-occasion", "--items", "shirt1")
    assert r.returncode == 1
    assert read_json("outfit_history.json") == []
    assert read_json("closet.json")[0]["wear_count"] == 0


@test("outfit_history: duplicate items in one log rejected")
def _():
    write_json("closet.json", [item(id="shirt1", wear_count=0)])
    write_json("outfit_history.json", [])
    r = run("outfit_history.py", "log", "--occasion", "casual", "--items", "shirt1,shirt1")
    assert r.returncode == 1
    assert read_json("closet.json")[0]["wear_count"] == 0


@test("outfit_history: bad wear_count rejected atomically, nothing written")
def _():
    write_json("closet.json", [item(id="good", wear_count=2), item(id="bad", wear_count="oops")])
    write_json("outfit_history.json", [])
    r = run("outfit_history.py", "log", "--occasion", "casual", "--items", "good,bad")
    assert r.returncode == 1
    assert read_json("outfit_history.json") == []
    closet_after = {i["id"]: i["wear_count"] for i in read_json("closet.json")}
    assert closet_after["good"] == 2


@test("outfit_history: recent skips malformed date entries with a note, no crash")
def _():
    write_json("outfit_history.json", [
        {"id": "bad", "occasion": "casual", "items": ["x"], "favorite": False, "notes": "", "feedback": ""},
        {"id": "good", "date": (date.today() - timedelta(days=5)).isoformat(), "occasion": "casual", "items": ["y"], "favorite": False, "notes": "", "feedback": ""},
    ])
    r = run("outfit_history.py", "recent", "--days", "30")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "missing/invalid date" in r.stdout
    assert "good" in r.stdout


@test("outfit_history: favorite and feedback work")
def _():
    write_json("closet.json", [item(id="shirt1", wear_count=0)])
    write_json("outfit_history.json", [])
    run("outfit_history.py", "log", "--occasion", "casual", "--items", "shirt1", "--date", "2026-07-13")
    outfit_id = read_json("outfit_history.json")[0]["id"]
    r = run("outfit_history.py", "feedback", outfit_id, "--note", "great fit", "--favorite")
    assert r.returncode == 0, r.stdout + r.stderr
    entry = read_json("outfit_history.json")[0]
    assert entry["favorite"] is True
    assert entry["feedback"] == "great fit"


# ---------- shopping_closet_check.py ----------

@test("shopping_closet_check: category match returned with cost-per-wear")
def _():
    write_json("closet.json", [item(id="blazer1", category="blazer", purchase_price=200, wear_count=10)])
    r = run("shopping_closet_check.py", "--category", "blazer")
    data = json.loads(r.stdout)
    assert data["match_count"] == 1
    assert data["matches"][0]["realized_cost_per_wear"] == 20.0


@test("shopping_closet_check: formality-unknown item kept and flagged")
def _():
    no_formality = item(id="blazer1", category="blazer")
    del no_formality["formality"]
    write_json("closet.json", [no_formality])
    r = run("shopping_closet_check.py", "--category", "blazer", "--formality", "4")
    data = json.loads(r.stdout)
    assert data["match_count"] == 1
    assert data["matches"][0]["formality_unknown"] is True


# ---------- closet_value.py ----------

@test("closet_value: total computed correctly, unpriced item excluded and flagged")
def _():
    write_json("closet.json", [item(id="a", purchase_price=100), item(id="b", purchase_price=None)])
    r = run("closet_value.py")
    assert "$100.00" in r.stdout
    assert "missing a purchase_price" in r.stdout


@test("closet_value: recouped value from retired items reported, net invested computed")
def _():
    write_json("closet.json", [
        item(id="a", purchase_price=100, lifecycle_state="retired", disposition="sold", disposition_value=40),
        item(id="b", purchase_price=50, lifecycle_state="active"),
    ])
    r = run("closet_value.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Recouped from retired items: $40.00 across 1 item(s)" in r.stdout
    assert "Net invested (total minus recouped): $110.00" in r.stdout
    assert "sold: $40.00 (1 item)" in r.stdout


@test("closet_value: no recouped section when nothing has a disposition_value")
def _():
    write_json("closet.json", [item(id="a", purchase_price=100, lifecycle_state="retired")])
    r = run("closet_value.py")
    assert "Recouped" not in r.stdout


# ---------- closet_report.py ----------

@test("closet_report: usage section shows worn and never-worn items")
def _():
    write_json("closet.json", [
        item(id="worn", wear_count=10, category="shirt", formality=3),
        item(id="never", wear_count=0, category="pants", formality=3),
    ])
    r = run("closet_report.py")
    assert "worn: 10 wears" in r.stdout
    assert "never" in r.stdout


@test("closet_report: zero-wear item with estimated_frequency reported separately from never-worn")
def _():
    write_json("closet.json", [
        item(id="genuinely-new", wear_count=0, category="shirt", formality=3),
        item(id="bulk-catalogued", wear_count=0, category="pants", formality=3, estimated_frequency="regularly"),
    ])
    r = run("closet_report.py")
    assert "Never worn, no tracked wears yet" in r.stdout
    assert "genuinely-new" in r.stdout
    assert "self-reported usage on file" in r.stdout
    assert "bulk-catalogued" in r.stdout
    assert "reported 'regularly'" in r.stdout
    # Not double-counted into the "never worn" bucket just because wear_count is 0.
    never_worn_section = r.stdout.split("Never worn, no tracked wears yet")[1].split("self-reported usage on file")[0]
    assert "bulk-catalogued" not in never_worn_section


@test("closet_report: color-variant duplicate caught")
def _():
    write_json("closet.json", [
        item(id="a", category="blazer", color="navy", formality=4),
        item(id="b", category="blazer", color="navy blue", formality=4),
    ])
    r = run("closet_report.py")
    assert "a <-> b" in r.stdout or "b <-> a" in r.stdout


@test("closet_report: athletic_wear counted, no phantom gap")
def _():
    write_json("closet.json", [
        item(id="tracksuit", category="activewear", formality=1),
        item(id="shoes", category="athletic_shoes", formality=1),
    ])
    r = run("closet_report.py")
    assert "athletic: no active" not in r.stdout


@test("closet_report: formality-missing excluded from gaps but flagged")
def _():
    no_formality = item(id="mystery")
    del no_formality["formality"]
    write_json("closet.json", [no_formality])
    r = run("closet_report.py")
    assert "missing or invalid formality" in r.stdout


@test("closet_report: non-numeric/boolean formality doesn't crash duplicates, clusters, or gaps")
def _():
    write_json("closet.json", [
        item(id="c1", category="sweater", color="navy", formality="high"),
        item(id="c2", category="sweater", color="navy", formality=2),
    ])
    r = run("closet_report.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stdout + r.stderr
    assert "formality unknown on one side" in r.stdout

    write_json("closet.json", [item(id="c3", category="jacket", formality=True)])
    r2 = run("closet_report.py")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "Traceback" not in r2.stdout + r2.stderr


# ---------- care.py ----------

@test("care: wool sweater flagged via category rule (not generic material rule)")
def _():
    write_json("closet.json", [item(id="sweater1", category="sweater", material="wool", wear_count=4)])
    r = run("care.py", "check")
    assert "sweater1" in r.stdout
    assert "hand wash or dry clean" in r.stdout


@test("care: cotton blazer not prematurely flagged (category wins over material)")
def _():
    write_json("closet.json", [item(id="blazer1", category="blazer", material="cotton", wear_count=2)])
    r = run("care.py", "check")
    assert "blazer1" not in r.stdout


@test("care: canvas bag gets fabric-appropriate care, not 'condition leather'")
def _():
    write_json("closet.json", [item(id="tote1", category="bag", material="canvas", wear_count=15)])
    r = run("care.py", "check")
    assert "tote1" in r.stdout
    assert "condition leather" not in r.stdout


@test("care: wool beanie gets no default rule")
def _():
    write_json("closet.json", [item(id="beanie1", category="hat", material="wool", wear_count=100)])
    r = run("care.py", "check")
    assert "beanie1" not in r.stdout


@test("care: override with interval-wears 0 accepted")
def _():
    write_json("closet.json", [item(id="jeans1", category="jeans", wear_count=0)])
    r = run("care.py", "override", "--item", "jeans1", "--interval-wears", "0")
    assert r.returncode == 0, r.stdout + r.stderr


@test("care: malformed maintenance log entry surfaced, not crashed")
def _():
    watch = item(id="watch1", category="watch", material="steel")
    watch["maintenance_log"] = [{"action": "serviced"}]
    write_json("closet.json", [watch])
    r = run("care.py", "check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "missing/invalid date" in r.stdout


@test("care: laundry aggregates dirty items by category, ignores non-dirty items")
def _():
    write_json("closet.json", [
        item(id="shirt1", category="shirt", lifecycle_state="dirty"),
        item(id="shirt2", category="shirt", lifecycle_state="dirty"),
        item(id="pants1", category="pants", lifecycle_state="dirty"),
        item(id="clean1", category="shirt", lifecycle_state="active"),
    ])
    r = run("care.py", "laundry")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 item(s) dirty, across 2 categories" in r.stdout
    assert "shirt: 2 (shirt1, shirt2)" in r.stdout
    assert "pants: 1 (pants1)" in r.stdout
    assert "clean1" not in r.stdout


@test("care: laundry reports cleanly when nothing is dirty")
def _():
    write_json("closet.json", [item(id="clean1", lifecycle_state="active")])
    r = run("care.py", "laundry")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Nothing marked dirty" in r.stdout


# ---------- seasonal_review.py ----------

@test("seasonal_review: matches packed-away items for the season or all-season, ignores active items")
def _():
    write_json("closet.json", [
        item(id="wool-coat", category="coat", season="winter", lifecycle_state="packed-away"),
        item(id="scarf1", category="scarf", season="all-season", lifecycle_state="packed-away"),
        item(id="summer-shorts", category="shorts", season="summer", lifecycle_state="packed-away"),
        item(id="active-coat", category="coat", season="winter", lifecycle_state="active"),
    ])
    r = run("seasonal_review.py", "--season", "winter")
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["packed_away_total"] == 3
    matched_ids = {m["id"] for m in data["matches"]}
    assert matched_ids == {"wool-coat", "scarf1"}


@test("seasonal_review: invalid season rejected")
def _():
    write_json("closet.json", [item()])
    r = run("seasonal_review.py", "--season", "spooky")
    assert r.returncode != 0


# ---------- travel_packing.py ----------

@test("travel_packing: multi-occasion pool with matches_occasions")
def _():
    write_json("closet.json", [item(id="shirt1", category="shirt", formality=3)])
    r = run("travel_packing.py", "--occasions", "business-casual,casual")
    data = json.loads(r.stdout)
    match = data["candidates"]["base_top"][0]
    assert set(match["matches_occasions"]) == {"business-casual", "casual"}


@test("travel_packing: duplicate occasions deduped")
def _():
    write_json("closet.json", [item(id="shirt1", category="shirt", formality=3)])
    r = run("travel_packing.py", "--occasions", "casual,,casual")
    data = json.loads(r.stdout)
    assert data["occasions"] == ["casual"]


@test("travel_packing: empty occasions rejected")
def _():
    write_json("closet.json", [])
    r = run("travel_packing.py", "--occasions", "")
    assert r.returncode == 1


@test("travel_packing: suit doesn't fake-cover footwear")
def _():
    write_json("closet.json", [item(id="suit1", category="suit", formality=4)])
    r = run("travel_packing.py", "--occasions", "business-formal")
    data = json.loads(r.stdout)
    assert any("footwear" in w for w in data["warnings"])


@test("travel_packing: non-numeric formality skipped and flagged, not a crash")
def _():
    write_json("closet.json", [item(id="junk", formality="high")])
    r = run("travel_packing.py", "--occasions", "casual")
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert any("missing or invalid formality" in w for w in data["warnings"])


# ---------- validators ----------

@test("validate_style_profile: valid profile passes")
def _():
    r = run("validate_style_profile.py")
    assert r.returncode == 0, r.stdout + r.stderr


@test("validate_style_profile: corrupted list field caught")
def _():
    p = read_yaml("style_profile.yaml")
    p["preferred_colors"] = "olive"
    write_yaml("style_profile.yaml", p)
    r = run("validate_style_profile.py")
    assert r.returncode == 1


@test("validate_body_fit_profile: valid profile passes")
def _():
    r = run("validate_body_fit_profile.py")
    assert r.returncode == 0, r.stdout + r.stderr


@test("validate_body_fit_profile: corrupted units/measurement/fit_preference all caught")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["units"] = "furlongs"
    p["measurements"]["chest"] = "forty"
    p["fit_preferences"]["tops"] = "baggy"
    write_yaml("body_fit_profile.yaml", p)
    r = run("validate_body_fit_profile.py")
    assert r.returncode == 1
    assert "3 issue" in r.stdout


# ---------- purchase_decisions.py ----------

@test("purchase_decisions: log + outcome + report with override detection")
def _():
    write_json("purchase_decisions.json", [])
    write_json("closet.json", [item(id="blazer2", category="blazer")])
    r = run("purchase_decisions.py", "log", "--category", "blazer", "--recommendation", "skip", "--reasoning", "already own one")
    assert r.returncode == 0, r.stdout + r.stderr
    decision_id = read_json("purchase_decisions.json")[0]["id"]
    run("purchase_decisions.py", "outcome", decision_id, "--action", "bought", "--linked-item", "blazer2")
    r = run("purchase_decisions.py", "report")
    assert "Overrides (advised skip/already-own, bought anyway): 1" in r.stdout


@test("purchase_decisions: an 'already-own' recommendation bought anyway also counts as an override, not just 'skip'")
def _():
    write_json("purchase_decisions.json", [])
    r = run("purchase_decisions.py", "log", "--category", "blazer", "--recommendation", "already-own", "--reasoning", "already own a similar one")
    assert r.returncode == 0, r.stdout + r.stderr
    decision_id = read_json("purchase_decisions.json")[0]["id"]
    run("purchase_decisions.py", "outcome", decision_id, "--action", "bought")
    r = run("purchase_decisions.py", "report")
    assert "Overrides (advised skip/already-own, bought anyway): 1" in r.stdout


@test("purchase_decisions: invalid category/recommendation rejected")
def _():
    write_json("purchase_decisions.json", [])
    r = run("purchase_decisions.py", "log", "--category", "spaceship", "--recommendation", "buy", "--reasoning", "x")
    assert r.returncode == 1
    r2 = run("purchase_decisions.py", "log", "--category", "shirt", "--recommendation", "maybe", "--reasoning", "x")
    assert r2.returncode == 1


@test("purchase_decisions: outcome on nonexistent decision rejected")
def _():
    write_json("purchase_decisions.json", [])
    r = run("purchase_decisions.py", "outcome", "decision-fake", "--action", "bought")
    assert r.returncode == 1


# ---------- brand_sizing_lookup.py ----------

@test("brand_sizing_lookup: exact and fuzzy match work")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["brand_sizing"] = [{"brand": "J.Crew", "category": "shirt", "size": "M", "notes": ""}]
    write_yaml("body_fit_profile.yaml", p)
    r = run("brand_sizing_lookup.py", "--brand", "J Crew")
    assert "size M" in r.stdout


@test("brand_sizing_lookup: category filter narrows results")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["brand_sizing"] = [
        {"brand": "J.Crew", "category": "shirt", "size": "M", "notes": ""},
        {"brand": "J.Crew", "category": "pants", "size": "32x32", "notes": ""},
    ]
    write_yaml("body_fit_profile.yaml", p)
    r = run("brand_sizing_lookup.py", "--brand", "J.Crew", "--category", "pants")
    assert "32x32" in r.stdout
    assert "size M" not in r.stdout


@test("brand_sizing_lookup: corrupted brand_sizing handled gracefully")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["brand_sizing"] = "oops"
    write_yaml("body_fit_profile.yaml", p)
    r = run("brand_sizing_lookup.py", "--brand", "J.Crew")
    assert r.returncode == 1
    assert "Traceback" not in r.stdout + r.stderr


# ---------- closet_bulk_state.py ----------

@test("closet_bulk_state: dry run doesn't write")
def _():
    write_json("closet.json", [item(id="shorts1", category="shorts")])
    r = run("closet_bulk_state.py", "--category", "shorts", "--set-state", "packed-away")
    assert "Dry run" in r.stdout
    assert read_json("closet.json")[0]["lifecycle_state"] == "active"


@test("closet_bulk_state: apply writes and validates")
def _():
    write_json("closet.json", [item(id="shorts1", category="shorts")])
    r = run("closet_bulk_state.py", "--category", "shorts", "--set-state", "packed-away", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert read_json("closet.json")[0]["lifecycle_state"] == "packed-away"


@test("closet_bulk_state: no filter rejected")
def _():
    write_json("closet.json", [item()])
    r = run("closet_bulk_state.py", "--set-state", "active")
    assert r.returncode == 1


@test("closet_bulk_state: missing id in --ids warned, valid ones still processed")
def _():
    write_json("closet.json", [item(id="shorts1", category="shorts")])
    r = run("closet_bulk_state.py", "--ids", "shorts1,nonexistent", "--set-state", "packed-away", "--apply")
    assert "not found" in r.stdout
    assert read_json("closet.json")[0]["lifecycle_state"] == "packed-away"


@test("closet_bulk_state: disposition applies alongside retired, defaults disposition_date to today")
def _():
    write_json("closet.json", [item(id="old-coat")])
    r = run("closet_bulk_state.py", "--ids", "old-coat", "--set-state", "retired", "--disposition", "donated", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    updated = read_json("closet.json")[0]
    assert updated["lifecycle_state"] == "retired"
    assert updated["disposition"] == "donated"
    assert updated["disposition_date"] == date.today().isoformat()
    assert "disposition_value" not in updated


@test("closet_bulk_state: disposition-value recorded, explicit disposition-date respected")
def _():
    write_json("closet.json", [item(id="old-boots")])
    r = run(
        "closet_bulk_state.py", "--ids", "old-boots", "--set-state", "retired",
        "--disposition", "sold", "--disposition-value", "45", "--disposition-date", "2026-01-10", "--apply",
    )
    assert r.returncode == 0, r.stdout + r.stderr
    updated = read_json("closet.json")[0]
    assert updated["disposition"] == "sold"
    assert updated["disposition_value"] == 45.0
    assert updated["disposition_date"] == "2026-01-10"


@test("closet_bulk_state: disposition without --set-state retired rejected")
def _():
    write_json("closet.json", [item(id="shorts1", category="shorts")])
    r = run("closet_bulk_state.py", "--ids", "shorts1", "--set-state", "packed-away", "--disposition", "donated", "--apply")
    assert r.returncode == 1
    assert "retired" in r.stdout + r.stderr
    assert read_json("closet.json")[0]["lifecycle_state"] == "active"


@test("closet_bulk_state: invalid disposition value rejected before writing")
def _():
    write_json("closet.json", [item(id="old-coat")])
    r = run("closet_bulk_state.py", "--ids", "old-coat", "--set-state", "retired", "--disposition", "burned", "--apply")
    assert r.returncode == 1
    assert read_json("closet.json")[0]["lifecycle_state"] == "active"


@test("closet_bulk_state: disposition-value without --disposition rejected")
def _():
    write_json("closet.json", [item(id="old-coat")])
    r = run("closet_bulk_state.py", "--ids", "old-coat", "--set-state", "retired", "--disposition-value", "20", "--apply")
    assert r.returncode == 1
    assert read_json("closet.json")[0]["lifecycle_state"] == "active"


# ---------- check_photos.py ----------

@test("check_photos: broken reference, missing photo, and orphan all detected")
def _():
    closet_photos_dir = PROFILE_DIR / "photos" / "closet"
    closet_photos_dir.mkdir(parents=True, exist_ok=True)
    (closet_photos_dir / "orphan.jpg").write_text("x")
    write_json("closet.json", [
        item(id="broken", photo="profile/photos/closet/does-not-exist.jpg"),
        item(id="nopic", photo=""),
    ])
    r = run("check_photos.py")
    assert "Broken references" in r.stdout
    assert "no photo yet" in r.stdout
    assert "Orphaned" in r.stdout


# ---------- malformed-type robustness ----------
# Found by adversarial testing after the fact — every script here assumed a
# field's type (string vs. list vs. number) without checking. Same root cause
# as bugs found and fixed earlier in outfit_history.py's date handling and
# the Style Profile diff-applier, recurring in scripts that hadn't been hit
# with non-string/non-list data yet.

@test("brand_sizing_lookup: non-string brand field doesn't crash")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["brand_sizing"] = [{"brand": 12345, "category": "shirt", "size": "M"}]
    write_yaml("body_fit_profile.yaml", p)
    r = run("brand_sizing_lookup.py", "--brand", "J.Crew")
    assert r.returncode == 0
    assert "Traceback" not in r.stdout + r.stderr


@test("closet_dedup: non-string color on existing item doesn't crash")
def _():
    write_json("closet.json", [item(id="a", category="blazer", color=123, formality=4)])
    r = run("closet_dedup.py", "--category", "blazer", "--brand", "J.Crew", "--color", "navy")
    assert r.returncode == 0
    assert "Traceback" not in r.stdout + r.stderr


@test("closet_report: non-string color doesn't crash mid-report")
def _():
    write_json("closet.json", [
        item(id="a", category="blazer", color=123, formality=4),
        item(id="b", category="blazer", color="navy", formality=4),
    ])
    r = run("closet_report.py")
    assert r.returncode == 0
    assert "Traceback" not in r.stdout + r.stderr


@test("check_photos: non-string photo field reported, not crashed")
def _():
    write_json("closet.json", [item(id="weird", photo=["not", "a", "string"])])
    r = run("check_photos.py")
    assert r.returncode == 0
    assert "malformed 'photo' field" in r.stdout


@test("outfit_history: non-list items field shown as malformed, not char-split")
def _():
    write_json("outfit_history.json", [
        {"id": "outfit-bad", "date": (date.today() - timedelta(days=5)).isoformat(), "occasion": "casual", "items": "shirt1",
         "favorite": False, "notes": "", "feedback": ""},
    ])
    r = run("outfit_history.py", "recent", "--days", "30")
    assert r.returncode == 0
    assert "malformed 'items' field" in r.stdout
    assert "s, h, i, r, t" not in r.stdout


@test("confirm_closet_item: non-list flagged_fields doesn't char-split in --list")
def _():
    write_json("pending_closet_additions.json", [
        {"id": "test2", "category": "shirt", "color": "white", "review_summary": "test", "flagged_fields": "brand"},
    ])
    r = run("confirm_closet_item.py", "--list")
    assert "b, r, a, n, d" not in r.stdout


@test("validate_closet: non-list occasion_tags gives a clear type error")
def _():
    write_json("closet.json", [item(occasion_tags="casual")])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "occasion_tags must be a list" in r.stdout


@test("validate_closet: boolean formality caught, not silently accepted as formality 1")
def _():
    # Python's bool is an int subclass, so `True in {1: ..., 2: ...}` is True
    # (True == 1) — without an explicit bool check, formality: true would
    # silently pass as if it were formality: 1.
    write_json("closet.json", [item(formality=True)])
    r = run("validate_closet.py")
    assert r.returncode == 1


@test("care: log notes when item is still dirty/at-cleaner-or-repair")
def _():
    write_json("closet.json", [item(id="dirty-shirt", lifecycle_state="dirty", wear_count=3)])
    r = run("care.py", "log", "--item", "dirty-shirt", "--action", "washed")
    assert r.returncode == 0
    assert "still marked 'dirty'" in r.stdout
    assert "closet_bulk_state.py --ids dirty-shirt --set-state active --apply" in r.stdout


@test("care: log does not note anything for an already-active item")
def _():
    write_json("closet.json", [item(id="clean-shirt", lifecycle_state="active", wear_count=1)])
    r = run("care.py", "log", "--item", "clean-shirt", "--action", "washed")
    assert r.returncode == 0
    assert "still marked" not in r.stdout


# ---------- round-3 audit findings ----------

@test("validate_closet: non-dict list element reported, other items still checked")
def _():
    write_json("closet.json", ["not a dict", item(id="real")])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "must be a mapping" in r.stdout
    assert "Traceback" not in r.stdout + r.stderr


@test("outfit_history: favorite/feedback don't crash on a history entry missing id")
def _():
    write_json("outfit_history.json", [
        {"date": "2026-01-01", "occasion": "casual", "items": ["x"], "favorite": False, "notes": "", "feedback": ""},
    ])
    r1 = run("outfit_history.py", "favorite", "some-id")
    assert r1.returncode == 1
    assert "Traceback" not in r1.stdout + r1.stderr
    r2 = run("outfit_history.py", "feedback", "some-id", "--note", "test")
    assert r2.returncode == 1
    assert "Traceback" not in r2.stdout + r2.stderr


@test("closet_value: unpriced item missing id doesn't crash")
def _():
    write_json("closet.json", [{"category": "shirt", "purchase_price": None}])
    r = run("closet_value.py")
    assert r.returncode == 0
    assert "Traceback" not in r.stdout + r.stderr


@test("closet_value: non-numeric purchase_price excluded and named, not a crash")
def _():
    write_json("closet.json", [
        item(id="c1", purchase_price="250"),
        item(id="c2", purchase_price=True),
        item(id="c3", purchase_price=100),
    ])
    r = run("closet_value.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stdout + r.stderr
    assert "non-numeric purchase_price" in r.stdout
    assert "c1" in r.stdout and "c2" in r.stdout
    assert "across 1 item(s)" in r.stdout


@test("shopping_closet_check: non-numeric purchase_price doesn't crash cost-per-wear calc")
def _():
    write_json("closet.json", [item(category="shirt", purchase_price="250", wear_count=5)])
    r = run("shopping_closet_check.py", "--category", "shirt")
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    assert result["matches"][0]["realized_cost_per_wear"] is None


@test("shopping_closet_check: non-numeric formality on an item doesn't crash --formality filtering")
def _():
    write_json("closet.json", [item(category="shirt", formality="high")])
    r = run("shopping_closet_check.py", "--category", "shirt", "--formality", "3")
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    assert result["match_count"] == 1
    assert result["matches"][0]["formality_unknown"] is True


@test("validate_closet: non-numeric purchase_price and wear_count caught")
def _():
    write_json("closet.json", [item(purchase_price="250")])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "purchase_price must be numeric" in r.stdout

    write_json("closet.json", [item(purchase_price=True)])
    assert run("validate_closet.py").returncode == 1

    write_json("closet.json", [item(wear_count=True)])
    r2 = run("validate_closet.py")
    assert r2.returncode == 1
    assert "wear_count must be numeric" in r2.stdout


@test("closet_dedup: non-string category on existing item doesn't crash")
def _():
    write_json("closet.json", [{"id": "weird", "category": 123, "color": "navy"}])
    r = run("closet_dedup.py", "--category", "blazer", "--color", "navy")
    assert r.returncode == 0
    assert "Traceback" not in r.stdout + r.stderr


@test("closet_dedup: non-string purchase_date on an existing item doesn't crash the date-proximity check")
def _():
    write_json("closet.json", [{"id": "c1", "category": "jacket", "brand": "J.Crew", "color": "navy", "purchase_date": 20260101}])
    r = run("closet_dedup.py", "--category", "jacket", "--brand", "J.Crew", "--color", "navy", "--purchase-date", "2026-01-05")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stdout + r.stderr
    assert "c1" in r.stdout


@test("care: leather jacket gets material-specific care, not generic spot-clean")
def _():
    write_json("closet.json", [item(id="leather-jacket", category="jacket", material="leather", wear_count=15)])
    r = run("care.py", "check")
    assert "leather-jacket" in r.stdout
    assert "condition" in r.stdout


@test("care: watch movement_type sharpens the service interval")
def _():
    write_json("closet.json", [
        item(id="quartz-watch", category="watch", material="steel", movement_type="quartz", wear_count=0),
    ])
    watch = read_json("closet.json")[0]
    watch["maintenance_log"] = [{"date": "2025-01-01", "action": "battery change", "wear_count_at_maintenance": 0}]
    write_json("closet.json", [watch])
    r = run("care.py", "check")
    assert "quartz-watch" in r.stdout
    assert "battery change" in r.stdout


@test("care: watch with no movement_type falls back to generic watch rule")
def _():
    write_json("closet.json", [item(id="plain-watch", category="watch", material="steel", wear_count=0, added_date="2020-01-01")])
    r = run("care.py", "check")
    assert "plain-watch" in r.stdout
    assert "movement service check" in r.stdout


@test("care: non-numeric wear_count on a wear-based category is skipped and noted, not a crash")
def _():
    write_json("closet.json", [item(id="bad-sweater", category="sweater", material="wool", wear_count="five", added_date="2026-01-01")])
    r = run("care.py", "check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stdout + r.stderr
    assert "bad-sweater" in r.stdout
    assert "non-numeric wear_count" in r.stdout


# ---------- preferred_brands + shopping watchlist ----------

@test("confirm_style_update: preferred_brands add/remove works like other list fields")
def _():
    write_json("pending_style_update.json", {"review_summary": "t", "add": {"preferred_brands": [{"value": "J.Crew", "confidence": 80, "importance": 55}]}})
    r = run("confirm_style_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    p = read_yaml("style_profile.yaml")
    assert p["preferred_brands"] == [{"value": "J.Crew", "confidence": 80, "importance": 55}]
    write_json("pending_style_update.json", {"review_summary": "t2", "remove": {"preferred_brands": ["J.Crew"]}})
    r2 = run("confirm_style_update.py")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert read_yaml("style_profile.yaml")["preferred_brands"] == []


@test("validate_style_profile: preferred_brands wrong type caught")
def _():
    p = read_yaml("style_profile.yaml")
    p["preferred_brands"] = "J.Crew"
    write_yaml("style_profile.yaml", p)
    r = run("validate_style_profile.py")
    assert r.returncode == 1


@test("shopping_watchlist: add with valid criteria, list shows it")
def _():
    r = run("shopping_watchlist.py", "add", "--category", "coat", "--max-price", "400", "--formality", "4", "--season", "winter", "--brands", "J.Crew,Uniqlo", "--notes", "navy wool")
    assert r.returncode == 0, r.stdout + r.stderr
    r2 = run("shopping_watchlist.py", "list")
    assert "coat" in r2.stdout
    assert "under $400" in r2.stdout


@test("shopping_watchlist: add rejects bad category/formality/season/source")
def _():
    assert run("shopping_watchlist.py", "add", "--category", "spaceship").returncode == 1
    assert run("shopping_watchlist.py", "add", "--category", "coat", "--formality", "99").returncode == 1
    assert run("shopping_watchlist.py", "add", "--category", "coat", "--season", "monsoon").returncode == 1
    assert run("shopping_watchlist.py", "add", "--category", "coat", "--source", "made-up").returncode == 1


@test("shopping_watchlist: add rejects zero/negative max-price, which could never be satisfied")
def _():
    assert run("shopping_watchlist.py", "add", "--category", "coat", "--max-price", "-50").returncode == 1
    assert run("shopping_watchlist.py", "add", "--category", "coat", "--max-price", "0").returncode == 1
    assert run("shopping_watchlist.py", "add", "--category", "coat", "--max-price", "400").returncode == 0


@test("shopping_watchlist: list defaults to active only, --status all shows everything")
def _():
    write_json("shopping_watchlist.json", [
        {"id": "w1", "created_date": "2026-01-01", "category": "coat", "criteria": {}, "source": "user-requested", "status": "active", "last_checked": None, "linked_closet_item_id": None},
        {"id": "w2", "created_date": "2026-01-01", "category": "boots", "criteria": {}, "source": "user-requested", "status": "fulfilled", "last_checked": None, "linked_closet_item_id": None},
    ])
    r_active = run("shopping_watchlist.py", "list")
    assert "w1" in r_active.stdout
    assert "w2" not in r_active.stdout
    r_all = run("shopping_watchlist.py", "list", "--status", "all")
    assert "w1" in r_all.stdout and "w2" in r_all.stdout


@test("shopping_watchlist: fulfill links a real closet item, rejects a fake one")
def _():
    write_json("closet.json", [item(id="overcoat-1", category="coat")])
    write_json("shopping_watchlist.json", [
        {"id": "w1", "created_date": "2026-01-01", "category": "coat", "criteria": {}, "source": "user-requested", "status": "active", "last_checked": None, "linked_closet_item_id": None},
    ])
    r = run("shopping_watchlist.py", "fulfill", "w1", "--linked-item", "overcoat-1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert read_json("shopping_watchlist.json")[0]["status"] == "fulfilled"

    write_json("shopping_watchlist.json", [
        {"id": "w2", "created_date": "2026-01-01", "category": "coat", "criteria": {}, "source": "user-requested", "status": "active", "last_checked": None, "linked_closet_item_id": None},
    ])
    r2 = run("shopping_watchlist.py", "fulfill", "w2", "--linked-item", "nonexistent")
    assert r2.returncode == 1
    assert read_json("shopping_watchlist.json")[0]["status"] == "active"


@test("shopping_watchlist: cancel and touch work, error cleanly on nonexistent id")
def _():
    write_json("shopping_watchlist.json", [
        {"id": "w1", "created_date": "2026-01-01", "category": "coat", "criteria": {}, "source": "user-requested", "status": "active", "last_checked": None, "linked_closet_item_id": None},
    ])
    r = run("shopping_watchlist.py", "touch", "w1", "--date", "2026-07-13")
    assert r.returncode == 0, r.stdout + r.stderr
    assert read_json("shopping_watchlist.json")[0]["last_checked"] == "2026-07-13"

    r2 = run("shopping_watchlist.py", "cancel", "w1")
    assert r2.returncode == 0
    assert read_json("shopping_watchlist.json")[0]["status"] == "cancelled"

    assert run("shopping_watchlist.py", "cancel", "fake-id").returncode == 1
    assert run("shopping_watchlist.py", "touch", "fake-id").returncode == 1


# ---------- fit-preference contexts (per-occasion overrides) ----------

@test("confirm_body_fit_update: sets a default fit preference plus a context override")
def _():
    diff = {"review_summary": "t", "set": {"fit_preferences.tops": {
        "value": "relaxed", "confidence": 85, "importance": 60,
        "contexts": {"business-formal": {"value": "classic", "confidence": 70, "importance": 80}},
    }}}
    write_json("pending_body_fit_update.json", diff)
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    tops = read_yaml("body_fit_profile.yaml")["fit_preferences"]["tops"]
    assert tops["value"] == "relaxed"
    assert tops["contexts"]["business-formal"]["value"] == "classic"


@test("confirm_body_fit_update: boolean confidence/importance rejected BEFORE writing, not just caught by the post-write validator")
def _():
    diff = {"review_summary": "t", "set": {"fit_preferences.tops": {"value": "relaxed", "confidence": True, "importance": 60}}}
    write_json("pending_body_fit_update.json", diff)
    assert run("confirm_body_fit_update.py").returncode == 1
    profile = read_yaml("body_fit_profile.yaml")
    assert profile["version"] == 0
    assert profile["fit_preferences"]["tops"] is None


@test("confirm_body_fit_update: boolean measurement value rejected, not silently accepted as numeric")
def _():
    diff = {"review_summary": "t", "set": {"measurements.chest": {"value": True, "confidence": 80, "importance": 50}}}
    write_json("pending_body_fit_update.json", diff)
    assert run("confirm_body_fit_update.py").returncode == 1


@test("confirm_body_fit_update: empty-string fit-preference value rejected, numeric measurement of 0 still allowed")
def _():
    diff = {"review_summary": "t", "set": {"fit_preferences.tops": {"value": "   ", "confidence": 70, "importance": 60}}}
    write_json("pending_body_fit_update.json", diff)
    assert run("confirm_body_fit_update.py").returncode == 1

    diff2 = {"review_summary": "t", "set": {"measurements.chest": {"value": 0, "confidence": 70, "importance": 60}}}
    write_json("pending_body_fit_update.json", diff2)
    assert run("confirm_body_fit_update.py").returncode == 0


@test("confirm_body_fit_update: partial context update merges, doesn't wipe the default or other fields")
def _():
    write_json("pending_body_fit_update.json", {"review_summary": "t1", "set": {"fit_preferences.tops": {
        "value": "relaxed", "confidence": 85, "importance": 60,
        "contexts": {"business-formal": {"value": "classic", "confidence": 70, "importance": 80}},
    }}})
    run("confirm_body_fit_update.py")
    write_json("pending_body_fit_update.json", {"review_summary": "t2", "set": {"fit_preferences.tops": {
        "contexts": {"business-formal": {"confidence": 95}},
    }}})
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    tops = read_yaml("body_fit_profile.yaml")["fit_preferences"]["tops"]
    assert tops["value"] == "relaxed" and tops["confidence"] == 85 and tops["importance"] == 60
    ctx = tops["contexts"]["business-formal"]
    assert ctx["confidence"] == 95 and ctx["value"] == "classic" and ctx["importance"] == 80


@test("confirm_body_fit_update: invalid context key (not an occasion tier) rejected")
def _():
    diff = {"review_summary": "t", "set": {"fit_preferences.tops": {
        "value": "relaxed", "confidence": 80, "importance": 50,
        "contexts": {"weekend-vibes": {"value": "relaxed", "confidence": 80, "importance": 50}},
    }}}
    write_json("pending_body_fit_update.json", diff)
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 1


@test("confirm_body_fit_update: invalid context value (not in the fit-preference scale) rejected")
def _():
    diff = {"review_summary": "t", "set": {"fit_preferences.tops": {
        "value": "relaxed", "confidence": 80, "importance": 50,
        "contexts": {"athletic": {"value": "structured", "confidence": 80, "importance": 50}},
    }}}
    write_json("pending_body_fit_update.json", diff)
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 1


@test("confirm_body_fit_update: bottoms.length is enum-constrained like its siblings (rise/leg_opening/break)")
def _():
    diff = {"review_summary": "t", "set": {"fit_preferences.bottoms.length": {"value": "nonsense-value", "confidence": 70, "importance": 60}}}
    write_json("pending_body_fit_update.json", diff)
    assert run("confirm_body_fit_update.py").returncode == 1

    diff2 = {"review_summary": "t", "set": {"fit_preferences.bottoms.length": {"value": "ankle", "confidence": 70, "importance": 60}}}
    write_json("pending_body_fit_update.json", diff2)
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert read_yaml("body_fit_profile.yaml")["fit_preferences"]["bottoms"]["length"]["value"] == "ankle"


@test("validate_body_fit_profile: hand-edited bottoms.length outside the enum caught")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["fit_preferences"]["bottoms"]["length"] = {"value": "nonsense-value", "confidence": 70, "importance": 60}
    write_yaml("body_fit_profile.yaml", p)
    r = run("validate_body_fit_profile.py")
    assert r.returncode == 1
    assert "fit_preferences.bottoms.length" in r.stdout


@test("confirm_body_fit_update: contexts on a path that doesn't support them (measurements) rejected")
def _():
    diff = {"review_summary": "t", "set": {"measurements.chest": {
        "value": 40, "confidence": 80, "importance": 50,
        "contexts": {"casual": {"value": 42, "confidence": 60, "importance": 40}},
    }}}
    write_json("pending_body_fit_update.json", diff)
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 1


# ---------- evidence trail ----------

@test("confirm_style_update: evidence appends and caps at 5 most recent")
def _():
    for i in range(7):
        write_json("pending_style_update.json", {"review_summary": f"t{i}", "add": {"avoidances": [
            {"value": "logos", "confidence": 80, "importance": 70, "evidence": [{"date": f"2026-01-0{i+1}", "note": f"observation {i}"}]}
        ]}})
        run("confirm_style_update.py")
    entry = read_yaml("style_profile.yaml")["avoidances"][0]
    assert len(entry["evidence"]) == 5
    assert entry["evidence"][-1]["note"] == "observation 6"
    assert entry["evidence"][0]["note"] == "observation 2"


@test("confirm_body_fit_update: brand_sizing evidence merges and caps at 5")
def _():
    write_json("pending_body_fit_update.json", {"review_summary": "t1", "add": {"brand_sizing": [
        {"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": 60, "evidence": [{"date": "2026-01-01", "note": "first mention"}]}
    ]}})
    run("confirm_body_fit_update.py")
    write_json("pending_body_fit_update.json", {"review_summary": "t2", "add": {"brand_sizing": [
        {"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": 95, "evidence": [{"date": "2026-01-02", "note": "confirmed again"}]}
    ]}})
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    p = read_yaml("body_fit_profile.yaml")
    assert len(p["brand_sizing"]) == 1
    assert p["brand_sizing"][0]["confidence"] == 95
    assert len(p["brand_sizing"][0]["evidence"]) == 2


@test("confirm_body_fit_update: brand_sizing match ignores surrounding whitespace in brand/category")
def _():
    write_json("pending_body_fit_update.json", {"review_summary": "t1", "add": {"brand_sizing": [
        {"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": 60}
    ]}})
    run("confirm_body_fit_update.py")
    write_json("pending_body_fit_update.json", {"review_summary": "t2", "add": {"brand_sizing": [
        {"brand": "J.Crew ", "category": " shirt", "size": "M", "confidence": 90}
    ]}})
    r = run("confirm_body_fit_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    sizing = read_yaml("body_fit_profile.yaml")["brand_sizing"]
    assert len(sizing) == 1, f"expected reinforcement, got {sizing!r}"
    assert sizing[0]["confidence"] == 90


# ---------- validators catch the expanded schema ----------

@test("validate_style_profile: entry missing importance caught")
def _():
    p = read_yaml("style_profile.yaml")
    p["preferred_colors"] = [{"value": "navy", "confidence": 90}]
    write_yaml("style_profile.yaml", p)
    r = run("validate_style_profile.py")
    assert r.returncode == 1


@test("validate_style_profile: hand-edited boolean confidence caught, not silently accepted as 0/1")
def _():
    p = read_yaml("style_profile.yaml")
    p["preferred_colors"] = [{"value": "navy", "confidence": True, "importance": 60}]
    write_yaml("style_profile.yaml", p)
    r = run("validate_style_profile.py")
    assert r.returncode == 1


@test("validate_style_profile: hand-edited non-string closet_catalog_last_offered caught")
def _():
    p = read_yaml("style_profile.yaml")
    p["closet_catalog_last_offered"] = 20260723
    write_yaml("style_profile.yaml", p)
    r = run("validate_style_profile.py")
    assert r.returncode == 1
    assert "closet_catalog_last_offered" in r.stdout


@test("validate_body_fit_profile: malformed contexts caught")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["fit_preferences"]["tops"] = {"value": "relaxed", "confidence": 85, "importance": 60, "contexts": "not-a-dict"}
    write_yaml("body_fit_profile.yaml", p)
    r = run("validate_body_fit_profile.py")
    assert r.returncode == 1


@test("validate_body_fit_profile: hand-edited boolean confidence caught (measurement and brand_sizing)")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["measurements"]["chest"] = {"value": 40, "confidence": True, "importance": 50}
    write_yaml("body_fit_profile.yaml", p)
    assert run("validate_body_fit_profile.py").returncode == 1

    p = read_yaml("body_fit_profile.yaml")
    p["measurements"]["chest"] = None
    p["brand_sizing"] = [{"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": True}]
    write_yaml("body_fit_profile.yaml", p)
    assert run("validate_body_fit_profile.py").returncode == 1


@test("validate_body_fit_profile: boolean measurement value caught, not silently accepted as numeric")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["measurements"]["chest"] = {"value": True, "confidence": 80, "importance": 50}
    write_yaml("body_fit_profile.yaml", p)
    assert run("validate_body_fit_profile.py").returncode == 1


@test("brand_sizing_lookup: shows most recent evidence note")
def _():
    p = read_yaml("body_fit_profile.yaml")
    p["brand_sizing"] = [{"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": 90,
                           "evidence": [{"date": "2026-07-13", "note": "confirmed twice"}]}]
    write_yaml("body_fit_profile.yaml", p)
    r = run("brand_sizing_lookup.py", "--brand", "J.Crew")
    assert "confirmed twice" in r.stdout


# ---------- style identity (Genome layer) ----------

@test("confirm_style_identity_update: set archetype + add pillar")
def _():
    diff = {"review_summary": "t", "set": {"archetype": {
        "value": "American Gentleman Adventurer", "confidence": 60, "importance": 90,
        "evidence": [{"date": "2026-07-14", "note": "referenced JFK and Steve McQueen"}],
    }}, "add": {"pillars": [{"value": "Rugged heritage", "confidence": 75, "importance": 70}]}}
    write_json("pending_style_identity_update.json", diff)
    r = run("confirm_style_identity_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    identity = read_yaml("style_identity.yaml")
    assert identity["archetype"]["value"] == "American Gentleman Adventurer"
    assert identity["pillars"][0]["value"] == "Rugged heritage"
    assert identity["version"] == 1


@test("confirm_style_identity_update: add reinforces an existing pillar instead of duplicating")
def _():
    write_json("pending_style_identity_update.json", {"review_summary": "t1", "add": {"pillars": [
        {"value": "Coastal freedom", "confidence": 50, "importance": 60, "evidence": [{"date": "2026-01-01", "note": "first mention"}]}
    ]}})
    run("confirm_style_identity_update.py")
    write_json("pending_style_identity_update.json", {"review_summary": "t2", "add": {"pillars": [
        {"value": "coastal freedom", "confidence": 80, "importance": 65, "evidence": [{"date": "2026-07-14", "note": "reconfirmed"}]}
    ]}})
    r = run("confirm_style_identity_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    pillars = read_yaml("style_identity.yaml")["pillars"]
    assert len(pillars) == 1
    assert pillars[0]["confidence"] == 80
    assert len(pillars[0]["evidence"]) == 2


@test("confirm_style_identity_update: reinforcement match ignores surrounding whitespace, doesn't create a near-duplicate")
def _():
    write_json("pending_style_identity_update.json", {"review_summary": "t1", "add": {"pillars": [
        {"value": "Rugged heritage", "confidence": 60, "importance": 60}
    ]}})
    run("confirm_style_identity_update.py")
    write_json("pending_style_identity_update.json", {"review_summary": "t2", "add": {"pillars": [
        {"value": "Rugged heritage ", "confidence": 80, "importance": 70}
    ]}})
    r = run("confirm_style_identity_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    pillars = read_yaml("style_identity.yaml")["pillars"]
    assert len(pillars) == 1, f"expected reinforcement, got {pillars!r}"
    assert pillars[0]["confidence"] == 80


@test("confirm_style_identity_update: entry missing importance rejected")
def _():
    diff = {"review_summary": "t", "add": {"anti_identity": [{"value": "flashy luxury", "confidence": 70}]}}
    write_json("pending_style_identity_update.json", diff)
    r = run("confirm_style_identity_update.py")
    assert r.returncode == 1


@test("confirm_style_identity_update: boolean confidence/importance rejected BEFORE writing, not just caught by the post-write validator")
def _():
    diff = {"review_summary": "t", "add": {"pillars": [{"value": "x", "confidence": True, "importance": 60}]}}
    write_json("pending_style_identity_update.json", diff)
    assert run("confirm_style_identity_update.py").returncode == 1
    identity = read_yaml("style_identity.yaml")
    assert identity["version"] == 0
    assert identity["pillars"] == []


@test("confirm_style_identity_update: empty/whitespace-only value rejected BEFORE writing, not just caught by the post-write validator")
def _():
    diff = {"review_summary": "t", "add": {"pillars": [{"value": "   ", "confidence": 70, "importance": 60}]}}
    write_json("pending_style_identity_update.json", diff)
    assert run("confirm_style_identity_update.py").returncode == 1
    assert read_yaml("style_identity.yaml")["pillars"] == []


@test("cancel_style_identity_update: discards without writing")
def _():
    write_json("pending_style_identity_update.json", {"review_summary": "t", "set": {"archetype": {"value": "x", "confidence": 50, "importance": 50}}})
    r = run("cancel_style_identity_update.py")
    assert r.returncode == 0
    assert read_json("pending_style_identity_update.json") == {}
    assert read_yaml("style_identity.yaml")["version"] == 0


@test("confirm_style_identity_update: malformed pending file (list, not mapping) rejected cleanly, not a traceback")
def _():
    write_json("pending_style_identity_update.json", [{"id": "x"}])
    r = run("confirm_style_identity_update.py")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr


@test("validate_style_identity: malformed entry caught")
def _():
    identity = read_yaml("style_identity.yaml")
    identity["pillars"] = [{"value": "Rugged heritage", "confidence": 90}]
    write_yaml("style_identity.yaml", identity)
    r = run("validate_style_identity.py")
    assert r.returncode == 1


@test("validate_style_identity: hand-edited boolean confidence caught")
def _():
    identity = read_yaml("style_identity.yaml")
    identity["pillars"] = [{"value": "Rugged heritage", "confidence": True, "importance": 60}]
    write_yaml("style_identity.yaml", identity)
    r = run("validate_style_identity.py")
    assert r.returncode == 1


# ---------- purchase lessons (regret memory) ----------

@test("confirm_purchase_lesson: valid lesson applies")
def _():
    write_json("pending_purchase_lessons.json", [{
        "id": "lesson-slim-chinos", "lesson_type": "fit", "item_description": "slim chinos",
        "lesson": "uncomfortable through the thigh, never worn", "confidence": 75,
        "evidence": [{"date": "2026-07-14", "note": "user said never wears these pants"}],
    }])
    r = run("confirm_purchase_lesson.py", "--all")
    assert r.returncode == 0, r.stdout + r.stderr
    lessons = read_json("purchase_lessons.json")
    assert len(lessons) == 1 and lessons[0]["id"] == "lesson-slim-chinos"
    assert read_json("pending_purchase_lessons.json") == []


@test("confirm_purchase_lesson: reinforces a similar existing lesson instead of duplicating")
def _():
    write_json("pending_purchase_lessons.json", [{
        "id": "lesson-slim-chinos", "lesson_type": "fit", "item_description": "slim chinos",
        "lesson": "uncomfortable", "confidence": 60,
        "evidence": [{"date": "2026-01-01", "note": "first mention"}],
    }])
    run("confirm_purchase_lesson.py", "--all")
    write_json("pending_purchase_lessons.json", [{
        "id": "lesson-slim-chinos-2", "lesson_type": "fit", "item_description": "slim-fit chinos",
        "lesson": "confirmed again, thigh too tight", "confidence": 90,
        "evidence": [{"date": "2026-07-14", "note": "reconfirmed"}],
    }])
    r = run("confirm_purchase_lesson.py", "--all")
    assert r.returncode == 0, r.stdout + r.stderr
    lessons = read_json("purchase_lessons.json")
    assert len(lessons) == 1
    assert lessons[0]["confidence"] == 90
    assert len(lessons[0]["evidence"]) == 2


@test("confirm_purchase_lesson: invalid lesson_type rejected, missing id rejected")
def _():
    write_json("pending_purchase_lessons.json", [{"id": "l1", "lesson_type": "vibes", "item_description": "x", "lesson": "y", "confidence": 50}])
    assert run("confirm_purchase_lesson.py", "--all").returncode == 1

    write_json("pending_purchase_lessons.json", [{"lesson_type": "fit", "item_description": "x", "lesson": "y", "confidence": 50}])
    assert run("confirm_purchase_lesson.py", "--all").returncode == 1


@test("confirm_purchase_lesson: boolean confidence rejected BEFORE writing, not just caught by the post-write validator")
def _():
    write_json("pending_purchase_lessons.json", [{"id": "l1", "lesson_type": "fit", "item_description": "x", "lesson": "y", "confidence": True}])
    assert run("confirm_purchase_lesson.py", "--all").returncode == 1
    assert read_json("purchase_lessons.json") == []


@test("confirm_purchase_lesson: linked_closet_item_id must exist in closet.json, rejected BEFORE writing")
def _():
    write_json("pending_purchase_lessons.json", [{
        "id": "lesson-1", "lesson_type": "fit", "item_description": "x", "lesson": "y",
        "confidence": 50, "linked_closet_item_id": "nonexistent-item",
    }])
    r = run("confirm_purchase_lesson.py", "--all")
    assert r.returncode == 1
    assert read_json("purchase_lessons.json") == []


@test("confirm_purchase_lesson: malformed pending file (dict, not list) rejected cleanly, not a traceback")
def _():
    write_json("pending_purchase_lessons.json", {"id": "x"})
    r = run("confirm_purchase_lesson.py", "--all")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr

    r2 = run("confirm_purchase_lesson.py", "--list")
    assert r2.returncode == 1
    assert "Traceback" not in r2.stderr


@test("cancel_purchase_lesson: malformed pending file (dict, not list) rejected cleanly")
def _():
    write_json("pending_purchase_lessons.json", {"id": "x"})
    r = run("cancel_purchase_lesson.py", "--all")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr


@test("cancel_purchase_lesson: one and --all both work")
def _():
    write_json("pending_purchase_lessons.json", [
        {"id": "l1", "lesson_type": "fit", "item_description": "a", "lesson": "b", "confidence": 50},
        {"id": "l2", "lesson_type": "brand", "item_description": "c", "lesson": "d", "confidence": 50},
    ])
    r1 = run("cancel_purchase_lesson.py", "l1")
    assert r1.returncode == 0
    remaining = read_json("pending_purchase_lessons.json")
    assert len(remaining) == 1 and remaining[0]["id"] == "l2"

    r2 = run("cancel_purchase_lesson.py", "--all")
    assert r2.returncode == 0
    assert read_json("pending_purchase_lessons.json") == []

    assert run("cancel_purchase_lesson.py", "fake-id").returncode == 1


@test("purchase_lessons_lookup: fuzzy keyword match and lesson-type filter")
def _():
    write_json("purchase_lessons.json", [
        {"id": "lesson-1", "date": "2026-07-14", "lesson_type": "fit", "item_description": "slim chinos",
         "lesson": "uncomfortable through the thigh", "confidence": 80, "evidence": []},
    ])
    r = run("purchase_lessons_lookup.py", "--keyword", "slim fit trousers")
    assert r.returncode == 0
    assert "slim chinos" in r.stdout

    r2 = run("purchase_lessons_lookup.py", "--keyword", "slim chinos", "--lesson-type", "brand")
    assert "No purchase lessons found" in r2.stdout


@test("validate_purchase_lessons: malformed entry and duplicate id both caught")
def _():
    write_json("purchase_lessons.json", [{"id": "l1", "date": "2026-07-14", "lesson_type": "not-real", "item_description": "x", "lesson": "y", "confidence": 50}])
    assert run("validate_purchase_lessons.py").returncode == 1

    write_json("purchase_lessons.json", [
        {"id": "l1", "date": "2026-07-14", "lesson_type": "fit", "item_description": "x", "lesson": "y", "confidence": 50, "evidence": []},
        {"id": "l1", "date": "2026-07-14", "lesson_type": "brand", "item_description": "z", "lesson": "w", "confidence": 50, "evidence": []},
    ])
    assert run("validate_purchase_lessons.py").returncode == 1


@test("validate_purchase_lessons: hand-edited boolean confidence caught")
def _():
    write_json("purchase_lessons.json", [{"id": "l1", "date": "2026-07-14", "lesson_type": "fit", "item_description": "x", "lesson": "y", "confidence": True}])
    assert run("validate_purchase_lessons.py").returncode == 1


# ---------- wardrobe system intelligence (closet_report.py extensions) ----------

@test("closet_report: redundancy cluster flags 3+ active items, not just pairwise duplicates")
def _():
    closet = [
        {"id": "c1", "category": "sweater", "color": "navy", "formality": 2, "season": "fall", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 5},
        {"id": "c2", "category": "sweater", "color": "navy blue", "formality": 2, "season": "fall", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 2},
        {"id": "c3", "category": "sweater", "color": "navy", "formality": 3, "season": "fall", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0},
    ]
    write_json("closet.json", closet)
    r = run("closet_report.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Redundancy clusters" in r.stdout
    assert "3x sweater in navy" in r.stdout


@test("closet_report: redundancy cluster respects formality tolerance, doesn't chain across it")
def _():
    # c1 (formality 1) and c2 (formality 2) are within tolerance of each
    # other; c2 and c3 (formality 3) are within tolerance of each other; but
    # c1 and c3 are NOT (difference of 2, tolerance is 1). A naive category
    #+color-only cluster would wrongly lump all three together via c2 as a
    # bridge — a loungewear crewneck and a business-casual v-neck, both navy,
    # are not the same purchase mistake as three identical casual sweaters.
    closet = [
        {"id": "c1", "category": "sweater", "color": "navy", "formality": 1, "season": "fall", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 5},
        {"id": "c2", "category": "sweater", "color": "navy", "formality": 2, "season": "fall", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 2},
        {"id": "c3", "category": "sweater", "color": "navy", "formality": 3, "season": "fall", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0},
    ]
    write_json("closet.json", closet)
    r = run("closet_report.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3x sweater" not in r.stdout
    assert "None found." in r.stdout.split("Redundancy clusters")[1]


@test("closet_report: role coverage reports occasion_tags counts, or skips cleanly if untagged")
def _():
    write_json("closet.json", [BASIC_ITEM])
    r = run("closet_report.py")
    assert r.returncode == 0
    assert "no active items have occasion_tags set" in r.stdout.lower()

    tagged = dict(BASIC_ITEM)
    tagged["occasion_tags"] = ["business-casual"]
    write_json("closet.json", [tagged])
    r2 = run("closet_report.py")
    assert "business-casual: 1 item(s) tagged" in r2.stdout


@test("basics category: validates, stays out of outfit candidates, and isn't flagged as redundant")
def _():
    basics = [
        {"id": "b1", "category": "socks", "color": "black", "formality": 1, "season": "all-season", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0, "estimated_frequency": "regularly", "occasion_tags": []},
        {"id": "b2", "category": "socks", "color": "black", "formality": 1, "season": "all-season", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0, "estimated_frequency": "regularly", "occasion_tags": []},
        {"id": "b3", "category": "socks", "color": "black", "formality": 1, "season": "all-season", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0, "estimated_frequency": "regularly", "occasion_tags": []},
        {"id": "b4", "category": "underwear", "color": "gray", "formality": 1, "season": "all-season", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0, "estimated_frequency": "regularly", "occasion_tags": []},
    ]
    write_json("closet.json", basics)
    assert run("validate_closet.py").returncode == 0

    # never surfaced as outfit candidates — basics fill no outfit slot
    cand = run("outfit_candidates.py", "--occasion", "casual")
    assert cand.returncode == 0, cand.stdout + cand.stderr
    assert "b1" not in cand.stdout and "b4" not in cand.stdout

    # three identical black socks is a stocked drawer, not a redundancy cluster
    rep = run("closet_report.py")
    assert rep.returncode == 0, rep.stdout + rep.stderr
    assert "3x socks" not in rep.stdout
    assert "None found." in rep.stdout.split("Possible duplicates")[1].split("Redundancy clusters")[0]

    # but care.py still knows how to launder them
    care = run("care.py", "check")
    assert care.returncode == 0, care.stdout + care.stderr


@test("vest category: validates, sits in the outerwear slot, never counts as a top")
def _():
    closet = [
        {"id": "v1", "category": "vest", "color": "navy", "formality": 3, "season": "all-season", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0, "occasion_tags": []},
        {"id": "b1", "category": "pants", "color": "grey", "formality": 3, "season": "all-season", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0, "occasion_tags": []},
        {"id": "s1", "category": "sneakers", "color": "white", "formality": 3, "season": "all-season", "lifecycle_state": "active", "condition": "good", "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0, "occasion_tags": []},
    ]
    write_json("closet.json", closet)
    assert run("validate_closet.py").returncode == 0

    cand = run("outfit_candidates.py", "--occasion", "business-casual")
    assert cand.returncode == 0, cand.stdout + cand.stderr
    out = json.loads(cand.stdout)
    assert "v1" in json.dumps(out["candidates"].get("outerwear", [])), "vest should land in the outerwear slot"
    # a vest is not a top — this closet has bottoms + shoes but no top, so the
    # engine must still warn about a missing top even with the vest present
    assert any("base_top" in w or "top" in w for w in out["warnings"]), out["warnings"]


@test("vest category: care.py has a real rule for a well-worn vest")
def _():
    worn = {"id": "x-vest", "category": "vest", "color": "navy", "material": "polyester", "formality": 3,
            "season": "all-season", "lifecycle_state": "active", "condition": "good", "added_via": "manual",
            "added_date": "2020-01-01", "wear_count": 30, "occasion_tags": [], "maintenance_log": []}
    write_json("closet.json", [worn])
    r = run("care.py", "check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "x-vest" in r.stdout


@test("basics category: care.py has a real laundering rule for socks/underwear/undershirt/sleepwear")
def _():
    for cat in ("socks", "underwear", "undershirt", "sleepwear"):
        worn = {"id": f"x-{cat}", "category": cat, "color": "white", "material": "cotton", "formality": 1,
                "season": "all-season", "lifecycle_state": "active", "condition": "good", "added_via": "manual",
                "added_date": "2020-01-01", "wear_count": 30, "occasion_tags": [],
                "maintenance_log": []}
        write_json("closet.json", [worn])
        r = run("care.py", "check")
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"x-{cat}" in r.stdout, f"{cat} should be due for care with a 30-wear history and no maintenance log"


# ---------- stylist self-reflection ----------

@test("style_reflection: buckets high/low confidence and flags a literal contradiction")
def _():
    style = read_yaml("style_profile.yaml")
    style["preferred_colors"] = [{"value": "navy", "confidence": 90, "importance": 60, "evidence": [{"date": "2026-07-14", "note": "x"}]}]
    style["avoidances"] = [{"value": "navy blue", "confidence": 40, "importance": 30}]
    write_yaml("style_profile.yaml", style)

    r = run("style_reflection.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "High-confidence beliefs" in r.stdout
    assert "navy" in r.stdout
    assert "Uncertain beliefs" in r.stdout
    assert "Possible contradictions" in r.stdout
    assert "worth reconciling" in r.stdout


@test("style_reflection: flags a contradiction across style_profile and style_identity, not just within one file")
def _():
    style = read_yaml("style_profile.yaml")
    style["preferred_colors"] = [{"value": "black", "confidence": 85, "importance": 60, "evidence": [{"date": "2026-07-14", "note": "x"}]}]
    write_yaml("style_profile.yaml", style)

    identity = read_yaml("style_identity.yaml")
    identity["anti_identity"] = [{"value": "all-black outfits", "confidence": 60, "importance": 50}]
    write_yaml("style_identity.yaml", identity)

    r = run("style_reflection.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "\"black\"" in r.stdout and "all-black outfits" in r.stdout


@test("style_reflection: flags stale high-importance entries by evidence age")
def _():
    style = read_yaml("style_profile.yaml")
    style["style_categories"] = [{"value": "workwear", "confidence": 85, "importance": 70, "evidence": [{"date": "2025-01-01", "note": "old"}]}]
    write_yaml("style_profile.yaml", style)

    r = run("style_reflection.py")
    assert r.returncode == 0
    assert "Possibly stale" in r.stdout
    assert "workwear" in r.stdout.split("Possibly stale")[1]


@test("style_reflection: empty profile reports plainly instead of fabricating")
def _():
    r = run("style_reflection.py")
    assert r.returncode == 0
    assert "Nothing learned yet" in r.stdout


# ---------- end-to-end workflow integration tests ----------
#
# Everything above tests one script at a time against synthetic data. These
# chain real script invocations together in the actual sequence a
# conversation would produce them, so a bug in how two scripts' outputs feed
# each other (not visible from either script in isolation) has a chance to
# surface. Each test is one full module lifecycle, start to finish.

@test("WORKFLOW: closet intake -> outfit engine sees it -> wearing it logs history -> analytics reflects it")
def _():
    # 1. Draft and confirm a closet item, the same way Intake does.
    write_json("closet.json", [])
    write_json("pending_closet_additions.json", [{
        "id": "wf-navy-blazer", "category": "blazer", "color": "navy", "material": "wool",
        "formality": 4, "season": "all-season", "lifecycle_state": "active", "condition": "excellent",
        "added_via": "manual", "added_date": "2026-01-01", "wear_count": 0,
        "review_summary": "navy wool blazer",
    }])
    r = run("confirm_closet_item.py", "wf-navy-blazer")
    assert r.returncode == 0, r.stdout + r.stderr
    assert read_json("closet.json")[0]["id"] == "wf-navy-blazer"
    assert read_json("pending_closet_additions.json") == []

    # 2. The Outfit Engine should now see it as a real candidate for a
    # matching occasion, in the right slot.
    r = run("outfit_candidates.py", "--occasion", "business-formal")
    candidates = json.loads(r.stdout)
    assert any(i["id"] == "wf-navy-blazer" for i in candidates["candidates"].get("outerwear", []))

    # 3. Wearing it logs history, bumps wear_count, and flags the first wear.
    r = run("outfit_history.py", "log", "--occasion", "business-formal", "--items", "wf-navy-blazer")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "First wear for: wf-navy-blazer" in r.stdout
    closet = read_json("closet.json")
    assert closet[0]["wear_count"] == 1
    assert closet[0]["last_worn"] == date.today().isoformat()

    # 4. Wardrobe Analytics should now reflect that real wear.
    r = run("closet_report.py")
    assert "wf-navy-blazer: 1 wears" in r.stdout
    assert "wf-navy-blazer" not in r.stdout.split("Never worn")[1].split("##")[0] if "Never worn" in r.stdout else True

    # 5. recent history shows it, and it can be favorited to survive the
    # repeat-avoidance cooldown.
    r = run("outfit_history.py", "recent", "--days", "14")
    assert "wf-navy-blazer" in r.stdout
    outfit_id = read_json("outfit_history.json")[0]["id"]
    r = run("outfit_history.py", "favorite", outfit_id)
    assert r.returncode == 0
    assert read_json("outfit_history.json")[0]["favorite"] is True


@test("WORKFLOW: style profile + style identity both confirmed, self-reflection surfaces both coherently")
def _():
    # A style preference and a higher-level identity synthesis, drafted and
    # confirmed independently (they're separate files/scripts), should both
    # show up together when asked "what do you believe about my style."
    write_json("pending_style_update.json", {"review_summary": "t", "add": {"style_categories": [
        {"value": "workwear", "confidence": 80, "importance": 65, "evidence": [{"date": "2026-07-14", "note": "referenced Steve McQueen"}]}
    ]}})
    r1 = run("confirm_style_update.py")
    assert r1.returncode == 0, r1.stdout + r1.stderr

    write_json("pending_style_identity_update.json", {"review_summary": "t", "set": {"archetype": {
        "value": "American Gentleman Adventurer", "confidence": 40, "importance": 85,
        "evidence": [{"date": "2026-07-14", "note": "referenced Steve McQueen and JFK"}],
    }}})
    r2 = run("confirm_style_identity_update.py")
    assert r2.returncode == 0, r2.stdout + r2.stderr

    r3 = run("style_reflection.py")
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert "workwear" in r3.stdout
    assert "American Gentleman Adventurer" in r3.stdout
    # workwear (confidence 80) should read as a high-confidence belief;
    # the archetype (confidence 40, below the 50 uncertainty threshold)
    # should read as still uncertain — a fresh identity synthesis off a
    # couple of signals shouldn't be treated as settled fact yet.
    high_section = r3.stdout.split("High-confidence beliefs")[1].split("Uncertain beliefs")[0]
    low_section = r3.stdout.split("Uncertain beliefs")[1].split("Possible contradictions")[0]
    assert "workwear" in high_section
    assert "American Gentleman Adventurer" in low_section


@test("WORKFLOW: body/fit context override end-to-end, then reinforced brand sizing")
def _():
    # A general fit preference plus a business-formal-specific override.
    write_json("pending_body_fit_update.json", {"review_summary": "t1", "set": {"fit_preferences.tops": {
        "value": "relaxed", "confidence": 80, "importance": 60,
        "contexts": {"business-formal": {"value": "classic", "confidence": 65, "importance": 75}},
    }}})
    r1 = run("confirm_body_fit_update.py")
    assert r1.returncode == 0, r1.stdout + r1.stderr

    # A brand/size fact mentioned once, then reconfirmed later — should
    # reinforce (merge), not duplicate.
    write_json("pending_body_fit_update.json", {"review_summary": "t2", "add": {"brand_sizing": [
        {"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": 60, "evidence": [{"date": "2026-01-01", "note": "said Medium fit well"}]}
    ]}})
    r2 = run("confirm_body_fit_update.py")
    assert r2.returncode == 0, r2.stdout + r2.stderr

    write_json("pending_body_fit_update.json", {"review_summary": "t3", "add": {"brand_sizing": [
        {"brand": "J.Crew", "category": "shirt", "size": "M", "confidence": 90, "evidence": [{"date": "2026-07-14", "note": "confirmed again while shopping"}]}
    ]}})
    r3 = run("confirm_body_fit_update.py")
    assert r3.returncode == 0, r3.stdout + r3.stderr

    profile = read_yaml("body_fit_profile.yaml")
    tops = profile["fit_preferences"]["tops"]
    assert tops["value"] == "relaxed" and tops["contexts"]["business-formal"]["value"] == "classic"
    assert len(profile["brand_sizing"]) == 1
    assert profile["brand_sizing"][0]["confidence"] == 90
    assert len(profile["brand_sizing"][0]["evidence"]) == 2

    # brand_sizing_lookup.py (the Shopping Advisor's grounding tool) should
    # surface the reinforced record with its most recent evidence.
    r4 = run("brand_sizing_lookup.py", "--brand", "J.Crew")
    assert "confirmed again while shopping" in r4.stdout


@test("WORKFLOW: full Shopping Advisor pipeline — closet overlap + past lesson + brand sizing feed one recommendation")
def _():
    # An existing, well-worn item in the same category as a potential
    # purchase — the closet-overlap signal.
    write_json("closet.json", [item(
        id="existing-chinos", category="pants", color="khaki", formality=2,
        wear_count=20, purchase_price=60, brand="Uniqlo",
    )])

    # A past regret about the exact cut being considered.
    write_json("pending_purchase_lessons.json", [{
        "id": "lesson-slim-chinos", "lesson_type": "fit", "item_description": "slim chinos",
        "lesson": "uncomfortable through the thigh, never wore them", "confidence": 80,
        "evidence": [{"date": "2026-01-01", "note": "user said never wears these pants"}],
    }])
    assert run("confirm_purchase_lesson.py", "--all").returncode == 0

    # A known brand-sizing fact for context.
    write_yaml("body_fit_profile.yaml", {
        **read_yaml("body_fit_profile.yaml"),
        "brand_sizing": [{"brand": "Uniqlo", "category": "pants", "size": "32", "confidence": 85, "evidence": []}],
    })

    # The three grounding lookups Shopping Advisor draws on for "should I
    # buy slim chinos from Uniqlo?":
    overlap = json.loads(run("shopping_closet_check.py", "--category", "pants").stdout)
    assert overlap["match_count"] == 1 and overlap["matches"][0]["id"] == "existing-chinos"

    lesson_hit = run("purchase_lessons_lookup.py", "--keyword", "slim chinos")
    assert "uncomfortable through the thigh" in lesson_hit.stdout

    sizing_hit = run("brand_sizing_lookup.py", "--brand", "Uniqlo", "--category", "pants")
    assert "32" in sizing_hit.stdout

    # The recommendation (skip, given real overlap + a real past regret)
    # gets logged, and a later override shows up in the report.
    r = run("purchase_decisions.py", "log", "--category", "pants", "--recommendation", "skip",
            "--reasoning", "already own similar pants, and slim fit was a past regret",
            "--item-description", "slim chinos")
    assert r.returncode == 0, r.stdout + r.stderr
    decision_id = read_json("purchase_decisions.json")[0]["id"]

    r2 = run("purchase_decisions.py", "outcome", decision_id, "--action", "bought")
    assert r2.returncode == 0, r2.stdout + r2.stderr

    r3 = run("purchase_decisions.py", "report")
    assert "Overrides (advised skip/already-own, bought anyway): 1" in r3.stdout


@test("WORKFLOW: watchlist full lifecycle — add, touch, fulfill against a real closet item")
def _():
    write_json("closet.json", [item(id="new-overcoat", category="coat", color="navy", formality=4)])

    r1 = run("shopping_watchlist.py", "add", "--category", "coat", "--max-price", "400", "--source", "user-requested")
    assert r1.returncode == 0, r1.stdout + r1.stderr
    watch_id = read_json("shopping_watchlist.json")[0]["id"]

    r2 = run("shopping_watchlist.py", "list", "--status", "active")
    assert watch_id in r2.stdout

    r3 = run("shopping_watchlist.py", "touch", watch_id)
    assert r3.returncode == 0
    assert read_json("shopping_watchlist.json")[0]["last_checked"] == date.today().isoformat()

    # A match gets bought and linked back to the real closet item.
    r4 = run("shopping_watchlist.py", "fulfill", watch_id, "--linked-item", "new-overcoat")
    assert r4.returncode == 0, r4.stdout + r4.stderr
    entry = read_json("shopping_watchlist.json")[0]
    assert entry["status"] == "fulfilled" and entry["linked_closet_item_id"] == "new-overcoat"

    r5 = run("shopping_watchlist.py", "list", "--status", "active")
    assert "No watchlist entries" in r5.stdout
    r6 = run("shopping_watchlist.py", "list", "--status", "all")
    assert watch_id in r6.stdout


@test("WORKFLOW: purchase lesson lifecycle — draft, confirm, get found by lookup, then reinforced by a second mention")
def _():
    write_json("pending_purchase_lessons.json", [{
        "id": "lesson-cheap-belts", "lesson_type": "quality", "item_description": "cheap leather belts",
        "lesson": "the buckle always breaks within a year", "confidence": 65,
        "evidence": [{"date": "2026-01-01", "note": "third belt in a row broke"}],
    }])
    r1 = run("confirm_purchase_lesson.py", "lesson-cheap-belts")
    assert r1.returncode == 0, r1.stdout + r1.stderr

    hit = run("purchase_lessons_lookup.py", "--keyword", "belt")
    assert "buckle always breaks" in hit.stdout

    # Mentioned again later, with a similar-but-not-identical description —
    # should reinforce the existing lesson, not create a second one.
    write_json("pending_purchase_lessons.json", [{
        "id": "lesson-cheap-belts-2", "lesson_type": "quality", "item_description": "cheap leather belt",
        "lesson": "confirmed again — another buckle failure", "confidence": 90,
        "evidence": [{"date": "2026-07-14", "note": "fourth belt broke the same way"}],
    }])
    r2 = run("confirm_purchase_lesson.py", "--all")
    assert r2.returncode == 0, r2.stdout + r2.stderr

    lessons = read_json("purchase_lessons.json")
    assert len(lessons) == 1
    assert lessons[0]["confidence"] == 90
    assert len(lessons[0]["evidence"]) == 2


@test("WORKFLOW: care lifecycle — fresh item not due, wears accumulate via outfit logging, becomes due, gets serviced, resets")
def _():
    write_json("closet.json", [item(id="wool-sweater", category="sweater", material="wool", color="navy", formality=2, wear_count=0)])

    # Fresh item, zero wears — nothing due yet.
    r1 = run("care.py", "check")
    assert "wool-sweater" not in r1.stdout or "Nothing due" in r1.stdout

    # Wear it 4 times via the real outfit-logging path (sweater's category
    # rule is interval_wears: 4) — each call is a separate "day" so ids
    # don't collide.
    for i, d in enumerate(["2026-07-10", "2026-07-11", "2026-07-12", "2026-07-13"]):
        r = run("outfit_history.py", "log", "--occasion", "casual", "--items", "wool-sweater", "--date", d)
        assert r.returncode == 0, r.stdout + r.stderr

    assert read_json("closet.json")[0]["wear_count"] == 4

    r2 = run("care.py", "check")
    assert "wool-sweater" in r2.stdout
    assert "due for care" in r2.stdout

    # Get it cleaned — resets the wear-since-care counter.
    r3 = run("care.py", "log", "--item", "wool-sweater", "--action", "dry cleaned")
    assert r3.returncode == 0, r3.stdout + r3.stderr

    r4 = run("care.py", "check")
    assert "Nothing due for care right now." in r4.stdout


@test("WORKFLOW: travel packing pool is consistent with what the outfit engine would independently recommend")
def _():
    write_json("closet.json", [
        item(id="wf-shirt", category="shirt", formality=3, occasion_tags=["business-casual"]),
        item(id="wf-pants", category="pants", formality=3, occasion_tags=["business-casual"]),
        item(id="wf-shoes", category="dress_shoes", formality=3, occasion_tags=["business-casual"]),
    ])

    pool = json.loads(run("travel_packing.py", "--occasions", "business-casual").stdout)
    packed_ids = {i["id"] for slot in pool["candidates"].values() for i in slot}
    assert packed_ids == {"wf-shirt", "wf-pants", "wf-shoes"}

    candidates = json.loads(run("outfit_candidates.py", "--occasion", "business-casual").stdout)
    outfit_ids = {i["id"] for slot in candidates["candidates"].values() for i in slot}
    assert packed_ids == outfit_ids


@test("WORKFLOW: retiring a closet item removes it from the Outfit Engine's candidates without deleting its history")
def _():
    write_json("closet.json", [item(id="worn-out-jacket", category="jacket", formality=2, wear_count=30)])

    before = json.loads(run("outfit_candidates.py", "--occasion", "casual").stdout)
    assert any(i["id"] == "worn-out-jacket" for slot in before["candidates"].values() for i in slot)

    r = run("closet_bulk_state.py", "--ids", "worn-out-jacket", "--set-state", "retired", "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert read_json("closet.json")[0]["lifecycle_state"] == "retired"

    after = json.loads(run("outfit_candidates.py", "--occasion", "casual").stdout)
    assert not any(i["id"] == "worn-out-jacket" for slot in after["candidates"].values() for i in slot)

    # Still visible to Shopping Advisor's closet-overlap check — a retired
    # item is relevant purchase history even though it's not wearable now.
    overlap = json.loads(run("shopping_closet_check.py", "--category", "jacket").stdout)
    assert any(m["id"] == "worn-out-jacket" for m in overlap["matches"])


@test("WORKFLOW: redundancy signal in closet_report agrees with shopping_closet_check's overlap read")
def _():
    write_json("closet.json", [
        item(id="navy-sw-1", category="sweater", color="navy", formality=2),
        item(id="navy-sw-2", category="sweater", color="navy", formality=2),
        item(id="navy-sw-3", category="sweater", color="navy", formality=2),
    ])

    report = run("closet_report.py")
    assert "3x sweater in navy" in report.stdout

    overlap = json.loads(run("shopping_closet_check.py", "--category", "sweater").stdout)
    assert overlap["match_count"] == 3


# ---------- color_analysis: validate / confirm / cancel ----------

MINI_ANALYSIS = {
    "version": 1,
    "source": "test",
    "season": "Soft Summer",
    "undertone": "neutral",
    "contrast_level": "low",
    "priority_families": ["blue-greens"],
    "core_colors": ["Blued Moss"],
    "palette": [
        {"hex": "#4C98A2", "name": "Blued Moss", "family": "blue-greens"},
        {"hex": "#405874", "name": "Navy", "family": "blues"},
        {"hex": "#775E4F", "name": "Mocha Brown", "family": "neutrals"},
        {"hex": "#F2EEEE", "name": "Bone White", "family": "neutrals"},
        {"hex": "#923E3E", "name": "Garnet", "family": "reds"},
    ],
    "leather_palette": [{"hex": "#B59E87", "name": "Tan Suede"}],
    "exclude": {
        "rule": "no warm undertones",
        "undertone_examples": ["camel", "rust"],
        "hard_reject": [
            {"hex": "#FF7A1A", "name": "Bright Orange"},
            {"hex": "#FFD21E", "name": "Sunshine Yellow"},
        ],
    },
    "change_log": [],
}


@test("validate_color_analysis: the shipped color_analysis.yaml is valid")
def _():
    r = run("validate_color_analysis.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Soft Summer" in r.stdout


@test("validate_color_analysis: absent file is fine, not an error")
def _():
    (PROFILE_DIR / "color_analysis.yaml").unlink()
    r = run("validate_color_analysis.py")
    assert r.returncode == 0
    assert "no color_analysis" in r.stdout.lower()


@test("validate_color_analysis: bad season, malformed hex, unknown family, phantom core_color all caught")
def _():
    bad = dict(MINI_ANALYSIS)
    bad["season"] = "Warm Autumn Vibes"
    bad["palette"] = [
        {"hex": "not-a-hex", "name": "X", "family": "blues"},
        {"hex": "#123456", "name": "Y", "family": "made-up-family"},
    ]
    bad["core_colors"] = ["Nonexistent Colour"]
    write_yaml("color_analysis.yaml", bad)
    r = run("validate_color_analysis.py")
    assert r.returncode == 1
    for expected in ("season", "hex", "family", "core_colors"):
        assert expected in r.stdout, f"{expected!r} not flagged: {r.stdout}"


@test("validate_color_analysis: duplicate palette hex caught")
def _():
    dup = dict(MINI_ANALYSIS)
    dup["palette"] = MINI_ANALYSIS["palette"] + [{"hex": "#4c98a2", "name": "Dup", "family": "blues"}]
    write_yaml("color_analysis.yaml", dup)
    r = run("validate_color_analysis.py")
    assert r.returncode == 1
    assert "duplicate hex" in r.stdout


@test("confirm_color_analysis_update: applies a set, bumps version, appends change_log")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    write_json("pending_color_analysis_update.json", {
        "review_summary": "add a deep teal",
        "set": {"palette": MINI_ANALYSIS["palette"] + [{"hex": "#326670", "name": "Midnight Teal", "family": "blue-greens"}]},
    })
    r = run("confirm_color_analysis_update.py")
    assert r.returncode == 0, r.stdout + r.stderr
    data = read_yaml("color_analysis.yaml")
    assert data["version"] == 2
    assert any(p["name"] == "Midnight Teal" for p in data["palette"])
    assert data["change_log"][-1]["summary"] == "add a deep teal"
    assert read_json("pending_color_analysis_update.json") == {}


@test("confirm_color_analysis_update: unknown top-level field in 'set' rejected, nothing written")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    write_json("pending_color_analysis_update.json", {"review_summary": "oops", "set": {"favourite_number": 7}})
    r = run("confirm_color_analysis_update.py")
    assert r.returncode != 0
    assert read_yaml("color_analysis.yaml")["version"] == 1
    assert "favourite_number" in r.stdout + r.stderr


@test("confirm_color_analysis_update: a set that passes field-name checks but yields invalid data is rolled back, not half-applied")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    original = (PROFILE_DIR / "color_analysis.yaml").read_text()
    # 'season' is a real field (clears validate_diff) but this value isn't in
    # vocab.yaml (fails validate_color_analysis) — the write must not stick.
    write_json("pending_color_analysis_update.json", {"review_summary": "bad season", "set": {"season": "Warm Spring Deluxe"}})
    r = run("confirm_color_analysis_update.py")
    assert r.returncode != 0
    assert (PROFILE_DIR / "color_analysis.yaml").read_text() == original, "rejected update left color_analysis.yaml modified"
    assert read_json("pending_color_analysis_update.json") != {}, "pending diff was cleared even though the update failed"


@test("confirm_color_analysis_update: empty 'set' rejected")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    write_json("pending_color_analysis_update.json", {"review_summary": "nothing", "set": {}})
    assert run("confirm_color_analysis_update.py").returncode != 0


@test("cancel_color_analysis_update: discards without touching color_analysis.yaml")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    write_json("pending_color_analysis_update.json", {"review_summary": "x", "set": {"season": "True Winter"}})
    r = run("cancel_color_analysis_update.py")
    assert r.returncode == 0
    assert read_yaml("color_analysis.yaml")["season"] == "Soft Summer"
    assert read_json("pending_color_analysis_update.json") == {}


# ---------- color_match.py ----------

@test("color_match: an exact palette hex is in-palette at ΔE 0")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    r = run("color_match.py", "--hex", "#4C98A2")
    d = json.loads(r.stdout)
    assert d["verdict"] == "in-palette"
    assert d["nearest_palette"]["name"] == "Blued Moss"
    assert d["nearest_palette"]["priority"] is True
    assert d["nearest_palette"]["delta_e"] == 0.0


@test("color_match: a colour closer to a hard-reject swatch than to the palette is rejected")
def _():
    # Runs against the shipped color_analysis.yaml (restored from the backup
    # each test) — its full palette is what makes the second case below a
    # clean test of the hard-reject branch specifically.
    d = json.loads(run("color_match.py", "--hex", "#FF7A1A").stdout)
    assert d["verdict"] == "reject"
    assert d["nearest_excluded"]["name"] == "Bright Orange"

    # #2FB07A is NOT an exact hard-reject swatch and does NOT trip the
    # warm-undertone flag, and its nearest palette swatch IS within the
    # borderline band — so the ONLY thing that can reject it is the
    # hard-reject-swatch check (it's closer to Emerald than to the palette).
    d2 = json.loads(run("color_match.py", "--hex", "#2FB07A").stdout)
    assert d2["warm_undertone_flag"] is False
    assert d2["nearest_palette"]["delta_e"] <= 20.0
    assert d2["nearest_excluded"]["name"] == "Emerald"
    assert d2["verdict"] == "reject"


@test("color_match: a warm/golden colour is rejected even when a palette hex looks close")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    d = json.loads(run("color_match.py", "--hex", "#C19A6B").stdout)  # camel
    assert d["verdict"] == "reject"
    assert d["warm_undertone_flag"] is True


@test("color_match: the same warm tan is allowed as leather")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    d = json.loads(run("color_match.py", "--hex", "#C19A6B", "--context", "leather").stdout)
    assert d["verdict"] == "in-palette"
    assert d["nearest_palette"]["name"] == "Tan Suede"
    assert d["warm_undertone_flag"] is False


@test("color_match: --list-family returns a family, flags priority, rejects a bad family")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    d = json.loads(run("color_match.py", "--list-family", "blue-greens").stdout)
    assert d["priority"] is True
    assert {c["name"] for c in d["colors"]} == {"Blued Moss"}
    assert run("color_match.py", "--list-family", "chartreuse").returncode == 1


@test("color_match: no color_analysis.yaml exits cleanly with a clear message, not a traceback")
def _():
    (PROFILE_DIR / "color_analysis.yaml").unlink()
    r = run("color_match.py", "--hex", "#4C98A2")
    assert r.returncode == 1
    assert "Traceback" not in r.stdout + r.stderr
    assert "analysis" in (r.stdout + r.stderr).lower()


@test("color_match: a malformed hex is rejected without a traceback")
def _():
    write_yaml("color_analysis.yaml", MINI_ANALYSIS)
    r = run("color_match.py", "--hex", "octarine")
    assert r.returncode != 0
    assert "Traceback" not in r.stdout + r.stderr


@test("validate_closet: palette_match.verdict is checked against vocab, other shapes tolerated")
def _():
    write_json("closet.json", [item(palette_match={"hex": "#405874", "name": "Navy", "family": "blues", "delta_e": 2.1, "verdict": "in-palette"})])
    assert run("validate_closet.py").returncode == 0

    write_json("closet.json", [item(palette_match={"verdict": "kinda-works"})])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "palette_verdicts" in r.stdout

    write_json("closet.json", [item(palette_match="navy-ish")])
    r = run("validate_closet.py")
    assert r.returncode == 1
    assert "palette_match must be a mapping" in r.stdout


@test("WORKFLOW: colour-analysis import -> color_match reads it -> closet intake stores palette_match -> closet_report reflects it")
def _():
    # Import the analysis through the confirm flow (the pending file is the checkpoint).
    (PROFILE_DIR / "color_analysis.yaml").unlink()
    write_json("pending_color_analysis_update.json", {
        "review_summary": "initial import",
        "set": {k: v for k, v in MINI_ANALYSIS.items() if k not in ("version", "change_log")},
    })
    assert run("confirm_color_analysis_update.py").returncode == 0
    assert read_yaml("color_analysis.yaml")["season"] == "Soft Summer"

    # color_match now scores against the freshly-imported palette.
    on = json.loads(run("color_match.py", "--hex", "#405874").stdout)
    off = json.loads(run("color_match.py", "--hex", "#FFD21E").stdout)
    assert on["verdict"] == "in-palette" and off["verdict"] == "reject"

    # Two items enter the closet carrying the palette_match snapshots.
    write_json("closet.json", [
        item(id="navy-shirt", color="navy",
             palette_match={"hex": "#405874", "name": on["nearest_palette"]["name"],
                            "family": "blues", "delta_e": on["nearest_palette"]["delta_e"], "verdict": on["verdict"]}),
        item(id="mustard-tee", category="t-shirt", color="mustard",
             palette_match={"hex": "#FFD21E", "name": off["nearest_palette"]["name"],
                            "family": off["nearest_palette"].get("family"), "delta_e": off["nearest_palette"]["delta_e"], "verdict": off["verdict"]}),
    ])
    assert run("validate_closet.py").returncode == 0

    report = run("closet_report.py").stdout
    assert "Palette coverage" in report
    assert "1/2" in report
    assert "off-palette: mustard-tee" in report


# ---------- runner ----------

@test("comfort_profile: learns only from explicit observations and predicts similar conditions")
def _():
    assert run("comfort_profile.py", "predict", "--feels-like-f", "58").returncode == 0
    empty = json.loads(run("comfort_profile.py", "predict", "--feels-like-f", "58").stdout)
    assert empty["prediction"] is None and empty["confidence"] == 0
    assert run("comfort_profile.py", "observe", "--feels-like-f", "58", "--preference", "jacket").returncode == 0
    learned = json.loads(run("comfort_profile.py", "predict", "--feels-like-f", "59").stdout)
    assert learned["prediction"] == "jacket"


@test("outfit_score: returns complete ranked combinations and never uses inactive items")
def _():
    write_json("closet.json", [
        item(id="shirt", category="shirt", occasion_tags=["casual"]),
        item(id="pants", category="pants", occasion_tags=["casual"]),
        item(id="shoes", category="sneakers", occasion_tags=["casual"]),
        item(id="dirty-shirt", category="shirt", lifecycle_state="dirty", occasion_tags=["casual"]),
    ])
    result = json.loads(run("outfit_score.py", "--occasion", "casual").stdout)
    assert result["outfits"]
    assert set(result["outfits"][0]["items"]) == {"shirt", "pants", "shoes"}


@test("product_history: SKU identity survives URL changes and detects price changes")
def _():
    first = run("product_history.py", "seen", "--url", "https://shop.test/a", "--retailer", "Shop", "--name", "Navy shirt", "--sku", "SKU1", "--color-code", "NVY", "--size", "L", "--price", "100", "--status", "in-stock")
    assert json.loads(first.stdout)["changed"] is True
    second = run("product_history.py", "seen", "--url", "https://shop.test/new-url", "--retailer", "Shop", "--name", "Renamed", "--sku", "SKU1", "--color-code", "NVY", "--size", "L", "--price", "90", "--status", "in-stock")
    data = json.loads(second.stdout)
    assert data["changed"] is True and len(read_json("product_history.json")) == 1


@test("purchase_lifecycle: tracks delivery, fit and return deadline")
def _():
    created = run("purchase_lifecycle.py", "add", "--retailer", "Shop", "--item", "Jacket", "--order-id", "A1", "--ordered-date", date.today().isoformat(), "--return-days", "30")
    pid = created.stdout.strip()
    assert created.returncode == 0 and pid.startswith("purchase-")
    updated = run("purchase_lifecycle.py", "update", "--id", pid, "--status", "delivered", "--fit-verdict", "great")
    assert updated.returncode == 0
    due = json.loads(run("purchase_lifecycle.py", "due").stdout)
    assert due[0]["fit_verdict"] == "great"


@test("activity policy: dedicated golf shoes never leak into general outfits")
def _():
    write_json("closet.json", [
        item(id="polo", category="polo"), item(id="pants", category="pants"),
        item(id="street", category="sneakers"),
        item(id="golf-shoes", category="specialty_footwear", model="Golf Shoes"),
    ])
    general = json.loads(run("outfit_candidates.py", "--occasion", "smart-casual", "--activity", "general").stdout)
    footwear = [x["id"] for x in general["candidates"]["footwear"]]
    assert "street" in footwear and "golf-shoes" not in footwear


@test("activity policy: golf round and clubhouse enforce different footwear")
def _():
    write_json("closet.json", [
        item(id="polo", category="polo"), item(id="pants", category="pants"),
        item(id="loafers", category="loafers"),
        item(id="golf-shoes", category="specialty_footwear", model="Golf Shoes"),
    ])
    playing = json.loads(run("outfit_candidates.py", "--occasion", "smart-casual", "--activity", "golf-round").stdout)
    clubhouse = json.loads(run("outfit_candidates.py", "--occasion", "smart-casual", "--activity", "golf-clubhouse").stdout)
    assert [x["id"] for x in playing["candidates"]["footwear"]] == ["golf-shoes"]
    assert [x["id"] for x in clubhouse["candidates"]["footwear"]] == ["loafers"]


def _workout_closet():
    return [
        item(id="button-up", category="shirt", formality=3, occasion_tags=["smart-casual"]),
        item(id="golf-pants", category="pants", formality=3, occasion_tags=["smart-casual"]),
        item(id="court-sneakers", category="sneakers", formality=2, occasion_tags=["smart-casual"]),
        item(id="gym-tee", category="t-shirt", formality=2, occasion_tags=["casual"]),
        item(id="training-shorts", category="shorts", formality=1, season="summer", occasion_tags=["athletic"]),
        item(id="trainers", category="athletic_shoes", formality=1, occasion_tags=["athletic"]),
    ]


@test("activity preference: personal simulator=workout rule beats the generic smart-casual mapping")
def _():
    write_json("closet.json", _workout_closet())
    before = json.loads(run("outfit_score.py", "--occasion", "smart-casual", "--activity", "golf-simulator").stdout)
    assert set(before["outfits"][0]["items"]) == {"button-up", "golf-pants", "court-sneakers"}
    set_result = run("activity_policy.py", "prefer", "--activity", "golf-simulator", "--occasion", "athletic",
                     "--slot", "base_top=t-shirt", "--slot", "bottoms=shorts", "--slot", "footwear=athletic_shoes",
                     "--evidence", "User treats simulator sessions as a workout")
    assert set_result.returncode == 0, set_result.stdout + set_result.stderr
    # Fall date + summer-tagged shorts: the bay is climate-controlled, so season must not empty the bottoms slot.
    after = json.loads(run("outfit_score.py", "--occasion", "smart-casual", "--activity", "golf-simulator", "--season", "fall").stdout)
    assert after["occasion"] == "athletic"
    assert [set(o["items"]) for o in after["outfits"]] == [{"gym-tee", "training-shorts", "trainers"}]
    assert any("personal preference" in w for w in after["warnings"])
    # Other activities are untouched by a simulator preference.
    general = json.loads(run("outfit_score.py", "--occasion", "smart-casual").stdout)
    assert set(general["outfits"][0]["items"]) == {"button-up", "golf-pants", "court-sneakers"}


@test("activity preference: a dress code stated on the invite still wins")
def _():
    write_json("closet.json", _workout_closet())
    run("activity_policy.py", "prefer", "--activity", "golf-simulator", "--occasion", "athletic",
        "--slot", "bottoms=shorts", "--evidence", "User treats simulator sessions as a workout")
    explicit = json.loads(run("outfit_score.py", "--occasion", "smart-casual", "--activity", "golf-simulator", "--explicit-occasion").stdout)
    assert explicit["occasion"] == "smart-casual"
    assert set(explicit["outfits"][0]["items"]) == {"button-up", "golf-pants", "court-sneakers"}


@test("activity preference: rejects unknown activity, occasion and slot categories")
def _():
    assert run("activity_policy.py", "prefer", "--activity", "pickleball", "--evidence", "x").returncode != 0
    assert run("activity_policy.py", "prefer", "--activity", "gym", "--occasion", "sporty", "--evidence", "x").returncode != 0
    assert run("activity_policy.py", "prefer", "--activity", "gym", "--slot", "footwear=shorts", "--evidence", "x").returncode != 0
    assert run("activity_policy.py", "venue", "--phrase", "Some Lounge", "--activity", "pickleball", "--evidence", "x").returncode != 0
    assert read_json("activity_preferences.json") == {}


@test("outfit_score: athletic separates always include bottoms")
def _():
    write_json("closet.json", [
        item(id="gym-tee", category="t-shirt", formality=1, occasion_tags=["athletic"]),
        item(id="trainers", category="athletic_shoes", formality=1, occasion_tags=["athletic"]),
    ])
    no_shorts = run("outfit_score.py", "--occasion", "athletic")
    assert json.loads(no_shorts.stdout)["outfits"] == []
    assert any("base_top + bottoms" in w for w in json.loads(run("outfit_candidates.py", "--occasion", "athletic").stdout)["warnings"])
    write_json("closet.json", read_json("closet.json") + [item(id="training-shorts", category="shorts", formality=1, occasion_tags=["athletic"])])
    complete = json.loads(run("outfit_score.py", "--occasion", "athletic").stdout)
    assert set(complete["outfits"][0]["items"]) == {"gym-tee", "training-shorts", "trainers"}


@test("calendar scan: workouts (incl. the simulator venue) get no board; a stated dress code there still does")
def _():
    run("activity_policy.py", "venue", "--phrase", "Birdie Barn", "--activity", "golf-simulator", "--evidence", "Simulator lounge")
    run("activity_policy.py", "prefer", "--activity", "golf-simulator", "--occasion", "athletic", "--evidence", "Workout")
    probe = (
        "import json, sys; sys.argv=['x']; sys.path.insert(0, %r); import calendar_outfit_candidates as c, subprocess, types\n"
        "events=[{'event_id':'e1','title':'Golf <> the user @ Birdie Barn','start_iso':'2030-01-01T13:30:00-06:00'},"
        "{'event_id':'e2','title':'Golf outing @ Birdie Barn','description':'Dress code: smart casual','start_iso':'2030-01-02T13:30:00-06:00'}]\n"
        "subprocess.run=lambda *a,**k: types.SimpleNamespace(returncode=0,stdout=json.dumps({'events':events}),stderr='')\n"
        "c.main()"
    ) % str(SCRIPTS_DIR)
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    found = {e["event_id"]: e for e in json.loads(r.stdout)["events"]}
    assert "e1" not in found                       # a plain simulator session is a workout: no board (The user 2026-09-20)
    assert (found["e2"]["activity"], found["e2"]["occasion"], found["e2"]["occasion_source"]) == ("golf-simulator", "smart-casual", "attire-text")


@test("activity preference: bottoms chosen by temperature, and the forecast outranks the season tag")
def _():
    write_json("closet.json", [
        item(id="polo", category="polo", formality=3), item(id="golf-pants", category="pants", formality=3),
        item(id="golf-shorts", category="shorts", formality=3, season="summer"),
        item(id="golf-shoes", category="specialty_footwear", model="Golf Shoes", formality=3),
    ])
    r = run("activity_policy.py", "prefer", "--activity", "golf-round", "--slot-temp", "bottoms:80:pants:shorts", "--evidence", "Pants unless it's hot")
    assert r.returncode == 0, r.stdout + r.stderr
    def bottoms(*extra):
        data = json.loads(run("outfit_candidates.py", "--occasion", "smart-casual", "--activity", "golf-round", "--season", "fall", *extra).stdout)
        return [x["id"] for x in data["candidates"].get("bottoms", [])], data["warnings"]
    assert bottoms("--feels-like-f", "76")[0] == ["golf-pants"]
    assert bottoms("--feels-like-f", "85")[0] == ["golf-shorts"]          # summer-tagged, fall date, 85F: still shorts
    ids, warnings = bottoms()
    assert ids == ["golf-pants"] and any("pass --feels-like-f" in w for w in warnings)   # no forecast: season filter stands, and it says why
    assert run("activity_policy.py", "prefer", "--activity", "golf-round", "--slot-temp", "bottoms:hot:pants:shorts", "--evidence", "x").returncode != 0
    assert run("activity_policy.py", "prefer", "--activity", "golf-round", "--slot-temp", "bottoms:80:pants:loafers", "--evidence", "x").returncode != 0


@test("comfort rule: long sleeves dropped in heat except for dressy indoor events")
def _():
    write_json("closet.json", [
        item(id="ls-shirt", category="shirt", formality=3, sleeve="long"), item(id="ss-polo", category="polo", formality=3, sleeve="short"),
        item(id="mystery-top", category="shirt", formality=3),
    ])
    run("activity_policy.py", "heat-rule", "--limit-f", "85", "--dressy-indoor-min-formality", "3", "--evidence", "Only for dressy indoor events")
    def tops(*extra):
        data = json.loads(run("outfit_candidates.py", "--occasion", "smart-casual", *extra).stdout)
        return {x["id"] for x in data["candidates"]["base_top"]}, data["warnings"]
    assert "ls-shirt" in tops("--feels-like-f", "90")[0]                                  # dressy + indoors: allowed
    assert "ls-shirt" in tops("--feels-like-f", "84", "--outdoor")[0]                     # under the limit
    ids, warnings = tops("--feels-like-f", "90", "--outdoor")
    assert ids == {"ss-polo", "mystery-top"} and any("mystery-top" in w for w in warnings)  # unknown sleeve is flagged, not silently trusted
    assert "ls-shirt" not in tops("--feels-like-f", "90", "--activity", "golf-simulator")[0]  # exertion is never "dressy"
    casual = json.loads(run("outfit_candidates.py", "--occasion", "casual", "--feels-like-f", "90").stdout)
    assert "ls-shirt" not in {x["id"] for x in casual["candidates"]["base_top"]}          # indoors but not dressy
    assert "ls-shirt" in tops()[0]                                                        # no forecast, no rule


@test("outfit_score: a tie goes to the piece that was not just proposed")
def _():
    write_json("closet.json", [
        item(id="a-shirt", category="shirt", occasion_tags=["casual"]), item(id="b-shirt", category="shirt", occasion_tags=["casual"]),
        item(id="pants", category="pants", occasion_tags=["casual"]), item(id="shoes", category="sneakers", occasion_tags=["casual"]),
    ])
    first = json.loads(run("outfit_score.py", "--occasion", "casual").stdout)["outfits"][0]["items"]
    assert "a-shirt" in first
    write_json("outfit_proposals.json", [{"id": "p1", "items": ["a-shirt", "pants", "shoes"], "status": "proposed", "created_at": datetime.now().astimezone().isoformat()}])
    second = json.loads(run("outfit_score.py", "--occasion", "casual").stdout)["outfits"][0]["items"]
    assert "b-shirt" in second
    write_json("outfit_proposals.json", [{"id": "p1", "items": ["a-shirt", "pants", "shoes"], "status": "proposed", "created_at": "2020-01-01T09:00:00-06:00"}])
    assert "a-shirt" in json.loads(run("outfit_score.py", "--occasion", "casual").stdout)["outfits"][0]["items"]   # old proposals stop counting


@test("activity policy: a brand called 'Golf' does not make one polo outrank the others")
def _():
    write_json("closet.json", [
        item(id="a-plain-polo", category="polo", formality=3), item(id="b-brand-polo", category="polo", brand="Polo Golf", formality=3),
        item(id="golf-pants", category="pants", model="Golf Pant", formality=3), item(id="chinos", category="pants", formality=3),
        item(id="golf-shoes", category="specialty_footwear", model="Golf Shoes", formality=3),
    ])
    top = json.loads(run("outfit_score.py", "--occasion", "smart-casual", "--activity", "golf-round").stdout)["outfits"][0]["items"]
    assert "a-plain-polo" in top and "golf-pants" in top      # golf pants still beat chinos; polos tie


@test("validate_closet: sleeve must be a known length")
def _():
    write_json("closet.json", [item(id="polo", category="polo", sleeve="short")])
    assert run("validate_closet.py").returncode == 0
    write_json("closet.json", [item(id="polo", category="polo", sleeve="three-quarter")])
    r = run("validate_closet.py")
    assert r.returncode == 1 and "sleeve" in r.stdout


def _learning_closet():
    return [
        item(id="a-dressy-shirt", category="shirt", formality=3, occasion_tags=["casual"]),
        item(id="b-plain-tee", category="t-shirt", formality=2, occasion_tags=["casual"]),
        item(id="pants", category="pants", formality=2, occasion_tags=["casual"]),
        item(id="shoes", category="sneakers", formality=2, occasion_tags=["casual"]),
    ]


def _top(*extra):
    return json.loads(run("outfit_score.py", "--occasion", "casual", *extra).stdout)["outfits"][0]


@test("learning: a 'too formal' tap demotes the dressiest piece for that activity only, and says why")
def _():
    write_json("closet.json", _learning_closet())
    assert "a-dressy-shirt" in _top("--activity", "dining")["items"]
    proposal_id = "k" * 12            # dated long ago so the "recently proposed" rotation penalty stays out of this test
    write_json("outfit_proposals.json", [{"id": proposal_id, "event_key": "k" * 20, "event_title": "Dinner", "event_start": "2030-01-01T19:00:00-06:00",
                                          "occasion": "casual", "activity": "dining", "items": ["a-dressy-shirt", "pants", "shoes"], "status": "proposed",
                                          "created_at": "2020-01-01T09:00:00-06:00"}])
    assert run("outfit_callback.py", "--id", proposal_id, "--action", "too-formal").returncode == 0
    after = _top("--activity", "dining")
    assert "b-plain-tee" in after["items"]
    explained = json.loads(run("outfit_score.py", "--occasion", "casual", "--activity", "dining", "--limit", "5").stdout)["outfits"]
    reasons = [w["because"] for o in explained for w in o["learned"]]
    assert any("too formal for dining" in r for r in reasons)
    assert all(w["item"] == "a-dressy-shirt" for o in explained for w in o["learned"])     # pants/shoes were not the dressy part
    assert "a-dressy-shirt" in _top("--activity", "travel")["items"]                        # other activities untouched


@test("learning: what was actually worn for an activity is favoured for it next time")
def _():
    write_json("closet.json", _learning_closet())
    long_ago = (date.today() - timedelta(days=30)).isoformat()
    logged = run("outfit_history.py", "log", "--occasion", "casual", "--activity", "dining", "--items", "b-plain-tee,pants,shoes", "--date", long_ago)
    assert logged.returncode == 0, logged.stdout + logged.stderr
    assert read_json("outfit_history.json")[0]["activity"] == "dining"
    def tee_boost(activity):
        outfits = json.loads(run("outfit_score.py", "--occasion", "casual", "--activity", activity, "--limit", "10").stdout)["outfits"]
        return [w for o in outfits for w in o["learned"] if w.get("item") == "b-plain-tee"]
    assert tee_boost("dining") and all(w["delta"] > 0 and "actually worn it for dining" in w["because"] for w in tee_boost("dining"))
    assert tee_boost("travel") == []                                       # worn for dinner says nothing about flights
    assert run("outfit_history.py", "log", "--occasion", "casual", "--activity", "pickleball", "--items", "pants").returncode != 0


@test("learning: a comfort tap only counts in similar weather, and evidence fades")
def _():
    write_json("closet.json", _learning_closet())
    recent = date.today().isoformat()
    signal = {"date": recent, "source": "outfit-proposal-response", "confidence": 35, "action": "too-warm", "occasion": "casual",
              "activity": "general", "feels_like_f": 88, "items": ["a-dressy-shirt", "pants", "shoes"], "note": ""}
    write_json("style_signals.json", [signal])
    assert "b-plain-tee" in _top("--feels-like-f", "90")["items"]          # similar heat: avoid what ran warm
    assert "a-dressy-shirt" in _top("--feels-like-f", "60")["items"]       # cool day: irrelevant
    assert "a-dressy-shirt" in _top()["items"]                             # no forecast: no claim
    write_json("style_signals.json", [{**signal, "date": "2020-01-01"}])
    assert "a-dressy-shirt" in _top("--feels-like-f", "90")["items"]       # years old: faded out


def _proposal(pid, activity="dining", status="proposed", hours_ago=3, occasion="casual", items=("a-dressy-shirt", "pants", "shoes")):
    start = (datetime.now().astimezone() - timedelta(hours=hours_ago)).isoformat()
    return {"id": pid, "event_key": pid + "00000000", "event_title": f"Event {pid}", "event_start": start, "occasion": occasion,
            "activity": activity, "items": list(items), "status": status, "created_at": "2020-01-01T09:00:00-06:00"}


@test("wear follow-up: asks after planned AND never-tapped outfits, not corrected, stale or well-known ones")
def _():
    write_json("closet.json", _learning_closet())
    write_json("outfit_proposals.json", [
        _proposal("aaaaaaaaaaa1", status="planned"), _proposal("aaaaaaaaaaa2", status="proposed"),
        _proposal("aaaaaaaaaaa3", status="too-formal"), _proposal("aaaaaaaaaaa4", status="skip"),
        _proposal("aaaaaaaaaaa5", status="proposed", hours_ago=72), _proposal("aaaaaaaaaaa6", status="proposed", hours_ago=-5),
        _proposal("aaaaaaaaaaa7", status="proposed", activity="golf-simulator"),
    ])
    due = lambda: sorted(p["id"] for p in json.loads(run("outfit_proposals.py", "pending-wear").stdout))
    assert due() == ["aaaaaaaaaaa1", "aaaaaaaaaaa2", "aaaaaaaaaaa7"]
    recent = (date.today() - timedelta(days=3)).isoformat()
    write_json("outfit_history.json", [{"id": f"o{i}", "date": recent, "occasion": "athletic", "activity": "golf-simulator",
                                        "items": ["pants"], "favorite": False, "notes": "", "feedback": ""} for i in range(3)])
    assert due() == ["aaaaaaaaaaa1", "aaaaaaaaaaa2"]              # 3 confirmed simulator wears this month: stop asking about it


@test("wear follow-up: 'Different outfit' asks what he wore, and the answer is logged against the activity")
def _():
    write_json("closet.json", _learning_closet())
    write_json("outfit_proposals.json", [_proposal("bbbbbbbbbbb1", status="wear-confirmation-sent")])
    r = run("outfit_callback.py", "--id", "bbbbbbbbbbb1", "--action", "not-worn")
    assert r.stdout.strip() == "Noted. What did you wear instead? A few words or a photo is plenty. 🎩", r.stdout + r.stderr
    pending = read_json("pending_wear_report.json")
    assert pending["proposal_id"] == "bbbbbbbbbbb1" and pending["activity"] == "dining" and pending["asked_at"]
    logged = run("outfit_proposals.py", "actual", "--id", "bbbbbbbbbbb1", "--items", "b-plain-tee,pants,shoes", "--note", "grey tee and my usual pants")
    assert logged.returncode == 0, logged.stdout + logged.stderr
    wear = read_json("outfit_history.json")[-1]
    assert wear["activity"] == "dining" and wear["items"] == ["b-plain-tee", "pants", "shoes"]
    saved = read_json("outfit_proposals.json")[0]
    assert saved["status"] == "worn-different" and saved["actual_note"] == "grey tee and my usual pants"
    assert not (PROFILE_DIR / "pending_wear_report.json").exists()
    # nothing he wore is catalogued: still recorded, no invented items, no history entry
    write_json("outfit_proposals.json", [_proposal("bbbbbbbbbbb2", status="wear-confirmation-sent")])
    before = len(read_json("outfit_history.json"))
    assert run("outfit_proposals.py", "actual", "--id", "bbbbbbbbbbb2", "--items", "", "--note", "an old gym shirt").returncode == 0
    assert len(read_json("outfit_history.json")) == before and read_json("outfit_proposals.json")[0]["actual_items"] == []
    assert run("outfit_proposals.py", "actual", "--id", "bbbbbbbbbbb2", "--items", "made-up-item", "--note", "x").returncode != 0


@test("rule offer: second consistent correction replaces the reply with a one-tap rule question")
def _():
    write_json("closet.json", _learning_closet())
    write_json("outfit_proposals.json", [_proposal("ccccccccccc1", occasion="smart-casual"), _proposal("ccccccccccc2", occasion="smart-casual")])
    first = run("outfit_callback.py", "--id", "ccccccccccc1", "--action", "too-formal")
    assert first.stdout.strip() == "Recorded as too formal. 🎩"
    again = run("outfit_callback.py", "--id", "ccccccccccc1", "--action", "too-formal")        # double tap on the SAME outfit
    assert again.stdout.strip() == "Recorded as too formal. 🎩" and read_json("rule_proposals.json") == {}
    assert len(read_json("style_signals.json")) == 1
    second = run("outfit_callback.py", "--id", "ccccccccccc2", "--action", "too-formal")
    assert second.stdout.strip() == "NO_REPLY", second.stdout + second.stderr
    (pid, offer), = read_json("rule_proposals.json").items()
    today = date.today()
    stamp = f"{today.strftime('%b')} {today.day}"
    assert offer["text"] == (f"That's twice you've called my dining outfits too formal ({stamp}, {stamp}). "
                             "Make it a rule to dress dining one step more casual: smart-casual → casual?")
    assert offer["status"] == "asked" and offer["activity"] == "dining"


@test("rule offer: exact wording, tier steps, accept keeps the rest of the rule, decline changes nothing")
def _():
    day = date.today().isoformat()
    sig = lambda pid, action="too-formal", activity="golf-round": {"date": day, "source": "outfit-proposal-response", "confidence": 35, "action": action,
                                                                    "proposal_id": pid, "occasion": "smart-casual", "activity": activity, "items": []}
    write_json("style_signals.json", [sig("p1"), sig("p2")])
    run("activity_policy.py", "prefer", "--activity", "golf-round", "--slot-temp", "bottoms:80:pants:shorts", "--guidance", "Polo always", "--evidence", "Original")
    offer = json.loads(run("rule_proposals.py", "check", "--activity", "golf-round", "--action", "too-formal", "--occasion", "smart-casual").stdout)
    today = date.today(); stamp = f"{today.strftime('%b')} {today.day}"
    assert offer["text"] == (f"That's twice you've called my golf round outfits too formal ({stamp}, {stamp}). "
                             "Make it a rule to dress golf round one step more casual: smart-casual → casual?")
    assert [b["text"] for b in offer["reply_markup"]["inline_keyboard"][0]] == ["✅ Make it a rule", "❌ Just those outfits"]
    assert offer["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"confirm:outfitrule-{offer['id']}"
    null = lambda *a: json.loads(run("rule_proposals.py", "check", *a).stdout)
    assert null("--activity", "general", "--action", "too-formal", "--occasion", "casual") is None          # no activity to attach a rule to
    assert null("--activity", "golf-round", "--action", "too-casual", "--occasion", "smart-casual") is None   # opposite direction: no evidence
    write_json("style_signals.json", [sig("p1", activity="dining"), sig("p2", activity="dining")])
    assert null("--activity", "dining", "--action", "too-formal", "--occasion", "casual") is None             # nothing sensible below casual for a dinner
    write_json("style_signals.json", [sig("p1", "too-casual"), sig("p2", "too-casual")])
    up = null("--activity", "golf-round", "--action", "too-casual", "--occasion", "smart-casual")
    assert "too casual" in up["text"] and "one step more formal: smart-casual → business-formal?" in up["text"]

    write_json("style_signals.json", [sig("p1"), sig("p2")])
    write_json("outfit_proposals.json", [_proposal("ddddddddddd1", activity="golf-round", occasion="smart-casual")])
    write_json("style_signals.json", [sig("p1")])
    assert run("outfit_callback.py", "--id", "ddddddddddd1", "--action", "too-formal").stdout.strip() == "NO_REPLY"
    (pid, _), = read_json("rule_proposals.json").items()
    declined = run("rule_proposals.py", "resolve", "--id", pid, "--answer", "decline")
    assert "no rule" in declined.stdout and "occasion" not in read_json("activity_preferences.json")["activities"]["golf-round"]
    assert "Nothing was changed" in run("rule_proposals.py", "resolve", "--id", pid, "--answer", "accept").stdout   # already answered
    assert null("--activity", "golf-round", "--action", "too-formal", "--occasion", "smart-casual") is None           # and not asked again for 60 days

    write_json("rule_proposals.json", {})
    write_json("outfit_proposals.json", [_proposal("ddddddddddd2", activity="golf-round", occasion="smart-casual")])
    write_json("style_signals.json", [sig("p1")])
    run("outfit_callback.py", "--id", "ddddddddddd2", "--action", "too-formal")
    (pid, _), = read_json("rule_proposals.json").items()
    accepted = run("rule_proposals.py", "resolve", "--id", pid, "--answer", "accept")
    assert "Rule saved: golf round now dresses casual (was smart-casual)" in accepted.stdout, accepted.stdout + accepted.stderr
    rule = read_json("activity_preferences.json")["activities"]["golf-round"]
    assert rule["occasion"] == "casual" and rule["guidance"] == "Polo always" and rule["temperature_slot_categories"]["bottoms"]["threshold_f"] == 80
    assert rule["evidence"].startswith("Original | ")


def _laundry(*args):
    return json.loads(run("laundry_day.py", "--location", "home", *args).stdout)


@test("laundry day: washed clothes come back; today's wear, dry-clean pieces and off-days do not")
def _():
    write_json("laundry.json", {"laundry_days": ["sunday", "thursday"], "max_dirty_days": 10})
    write_json("closet.json", [
        item(id="tee-worn-friday", category="t-shirt", lifecycle_state="dirty", dirty_since="2026-09-18"),
        item(id="tee-worn-today", category="t-shirt", lifecycle_state="dirty", dirty_since="2026-09-20"),
        item(id="blazer", category="blazer", lifecycle_state="dirty", dirty_since="2026-09-01"),
        item(id="hand-marked-shirt", category="shirt", lifecycle_state="dirty"),
        item(id="packed-shorts", category="shorts", lifecycle_state="packed-away"),
    ])
    assert _laundry("--today", "2026-09-19")["returned"] == []                    # Saturday: not a laundry day
    preview = _laundry("--today", "2026-09-20", "--dry-run")                      # Sunday
    assert [r["id"] for r in preview["returned"]] == ["tee-worn-friday", "hand-marked-shirt"]
    assert {x["id"]: x["lifecycle_state"] for x in read_json("closet.json")}["tee-worn-friday"] == "dirty"   # dry run wrote nothing
    _laundry("--today", "2026-09-20")
    states = {x["id"]: x for x in read_json("closet.json")}
    assert states["tee-worn-friday"]["lifecycle_state"] == "active" and "dirty_since" not in states["tee-worn-friday"]
    assert states["tee-worn-today"]["lifecycle_state"] == "dirty"                 # not in this load
    assert states["blazer"]["lifecycle_state"] == "dirty"                         # dry clean: he says when it's back
    assert states["packed-shorts"]["lifecycle_state"] == "packed-away"
    assert _laundry("--today", "2026-09-24")["returned"] == [{"id": "tee-worn-today", "why": "laundry day"}]   # Thursday


@test("laundry day: away from home nothing is washed; a skipped week can never empty the closet")
def _():
    write_json("laundry.json", {"laundry_days": ["sunday"], "max_dirty_days": 10})
    write_json("closet.json", [item(id="tee", category="t-shirt", lifecycle_state="dirty", dirty_since="2026-09-05")])
    away = json.loads(run("laundry_day.py", "--location", "away", "--today", "2026-09-20").stdout)
    assert away["away"] is True and away["returned"] == []                        # laundry day AND overdue, but he is in LA
    assert read_json("closet.json")[0]["lifecycle_state"] == "dirty"
    net = _laundry("--today", "2026-09-16")                                       # a Wednesday at home, 11 days dirty
    assert net["laundry_day"] is False and net["returned"] == [{"id": "tee", "why": "dirty 11 days"}]
    write_json("laundry.json", {"laundry_days": ["sunnday"], "max_dirty_days": 10})
    assert run("laundry_day.py", "--location", "home").returncode != 0            # a typo'd day fails loudly


@test("laundry day: a confirmed wear stamps when the piece went dirty")
def _():
    write_json("closet.json", [item(id="tee", category="t-shirt", last_worn="2026-09-18"), item(id="pants", category="pants")])
    run("mark_after_wear.py", "--items", "tee,pants")
    states = {x["id"]: x for x in read_json("closet.json")}
    assert states["tee"]["lifecycle_state"] == "dirty" and states["tee"]["dirty_since"] == "2026-09-18"
    assert states["pants"]["lifecycle_state"] == "active"                         # pants are a 3-wear item


def _event(title, location="", org="me@home.test", org_self=True, attendees=(), **extra):
    base = {"event_id": title[:8], "title": title, "location": location, "description": "", "all_day": False, "day": "2030-01-05",
            "start_fmt": "Sat Jan 5, 9:00 AM", "start_iso": "2030-01-05T09:00:00-06:00", "status": "confirmed", "response_status": "accepted",
            "organizer_email": org, "organizer_self": org_self, "attendees": [{"email": e, "name": n} for e, n in attendees]}
    return {**base, **extra}


def _triage(events):
    path = PROFILE_DIR / "_triage_events.json"
    path.write_text(json.dumps({"events": events}))
    r = run("event_triage.py", "replay", "--events-json", str(path), "--json")
    path.unlink()
    assert r.returncode == 0, r.stdout + r.stderr
    return {row["title"]: row["verdict"] for row in json.loads(r.stdout)}


@test("event triage: outfits only for real occasions; workouts, remote, errands and friends stay silent")
def _():
    write_json("people.json", {"self_emails": ["me@home.test"], "internal_domains": ["mycompany.test"], "people": {
        "investor@fund.test": {"name": "Ann Investor", "category": "business"}, "pal@gmail.test": {"name": "Pal Friend", "category": "friend"},
        "mum@family.test": {"name": "Mum", "category": "family"}}})
    run("activity_policy.py", "venue", "--phrase", "Birdie Barn", "--activity", "golf-simulator", "--evidence", "sim lounge")
    v = _triage([
        _event("Golf <> the user @ Birdie Barn", "100 Example St, Dallas, TX"),
        _event("Push Workout <> the user @ Example Golf Studio", "200 Example Pl, Example City, CA"),
        _event("Brunch <> the user @ Cafe", "12 Main St, Dallas, TX"),                                    # 'run' must not match inside 'Brunch'
        _event("Tee time <> the user @ Example National Golf Club", "100 Example St, Dallas, TX"),
        _event("Breakfast w. Ann - Corner Deli", "100 Example Plaza, Dallas, TX", "investor@fund.test", False, [("investor@fund.test", "Ann Investor")]),
        _event("The user and Pal <> the user @ The Pier", "100 Example St, Example City, CA", "pal@gmail.test", False, [("pal@gmail.test", "Pal")]),
        _event("Lunch <> the user @ Bistro", "9 Elm St, Dallas, TX", attendees=[("pal@gmail.test", "Pal"), ("mum@family.test", "Mum")]),
        _event("Coffee <> the user @ Corner Cafe", "100 Example St, Dallas, TX", attendees=[("new@person.test", "Sam Example")]),
        _event("Strategy <> the user @ Office", "1 Work St, Dallas, TX", attendees=[("cofounder@mycompany.test", "Co Founder")]),
        _event("Acme Co <> Pat @ Zoom", "https://us05web.zoom.us/j/1", attendees=[("investor@fund.test", "Ann Investor")]),
        _event("Dentist <> the user @ Example Clinic", "100 Example St, Example City, CA"),
        _event("WN 1234 DAL → DEN <> the user @ Dallas Love Field", "Dallas Love Field"),
        _event("WN 5678 DEN → DAL <> the user @ DEN", "Denver International Airport"),
        _event("Ticket: Tech Social", "Example Country Club, Fort Worth, TX"),
        _event("Dinner - Trattoria Example <> the user @ Trattoria Example", "100 Example Ave, New York, NY 10001"),
        _event("Board dinner — business casual", "Some Hall, Dallas, TX"),
        _event("Pick up dry cleaning", "400 Example Ave, Dallas, TX"),
        _event("Mystery block", "77 Unknown Rd, Dallas, TX"),
        _event("Cancelled gala", "Hotel, Dallas, TX", status="cancelled"),
    ])
    decided = {t: (x["decision"], x.get("kind")) for t, x in v.items()}
    assert decided["Golf <> the user @ Birdie Barn"] == ("skip", None) and "workout" in v["Golf <> the user @ Birdie Barn"]["reason"]
    assert decided["Push Workout <> the user @ Example Golf Studio"] == ("skip", None)
    assert decided["Brunch <> the user @ Cafe"] == ("venue-check", "dining")
    assert decided["Tee time <> the user @ Example National Golf Club"] == ("propose", "golf-round") and v["Tee time <> the user @ Example National Golf Club"]["outdoor"] is True
    assert decided["Breakfast w. Ann - Corner Deli"] == ("propose", "meeting-business") and v["Breakfast w. Ann - Corner Deli"]["activity"] == "work"
    assert decided["The user and Pal <> the user @ The Pier"] == ("skip", None) and "friend" in v["The user and Pal <> the user @ The Pier"]["reason"]
    assert decided["Lunch <> the user @ Bistro"] == ("propose", "meeting-family")                       # one family member outranks a friend
    assert decided["Coffee <> the user @ Corner Cafe"] == ("ask-person", "meeting-unknown") and v["Coffee <> the user @ Corner Cafe"]["person"]["email"] == "new@person.test"
    assert decided["Strategy <> the user @ Office"] == ("skip", None)                                   # colleagues are not "people" for this
    assert decided["Acme Co <> Pat @ Zoom"] == ("skip", None)                                  # business contact, but remote
    assert decided["Dentist <> the user @ Example Clinic"] == ("skip", None)
    assert decided["WN 1234 DAL → DEN <> the user @ Dallas Love Field"] == ("propose", "flight")
    assert decided["WN 5678 DEN → DAL <> the user @ DEN"] == ("skip", None)                             # same travel day: one board
    assert decided["Ticket: Tech Social"] == ("propose", "hosted-event")
    assert decided["Dinner - Trattoria Example <> the user @ Trattoria Example"] == ("venue-check", "dining")
    assert decided["Board dinner — business casual"] == ("propose", "dress-code") and v["Board dinner — business casual"]["explicit_occasion"] is True
    assert decided["Pick up dry cleaning"] == ("skip", None)
    assert decided["Mystery block"] == ("skip", None) and "silent by default" in v["Mystery block"]["reason"]
    assert decided["Cancelled gala"] == ("skip", None)


@test("event triage: a remembered venue verdict decides a dinner without asking again")
def _():
    event = _event("Dinner - Trattoria Example <> the user @ Trattoria Example", "100 Example Ave, New York, NY 10001")
    key = _triage([event])[event["title"]]["venue_key"]
    write_json("venues.json", {key: {"dressy": True, "evidence": "OpenTable: Casual Elegant"}})
    assert _triage([event])[event["title"]]["decision"] == "propose"
    write_json("venues.json", {key: {"dressy": False, "evidence": "counter service"}})
    assert _triage([event])[event["title"]]["decision"] == "skip"


@test("event forecast: finds the city inside real address shapes and never invents a number")
def _():
    probe = ("import sys, json; sys.path.insert(0, %r); import event_forecast as f\n"
             "print(json.dumps([f.candidates(x)[:1] for x in ['100 Example St, Example City, ST 00000, USA', '100 Example Ave, New York, NY 10001',"
             " 'Dallas Love Field', '200 Example Pl, Austin, TX 78701', '']] + [list(f.forecast('Dallas, TX', '2030-01-05T09:00:00-06:00'))]))") % str(SCRIPTS_DIR)
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env={**os.environ, "WARDROBE_NO_NETWORK": "1"})
    assert r.returncode == 0, r.stderr
    *cities, offline = json.loads(r.stdout)
    assert cities == [["Dallas"], ["New York"], ["Dallas Love Field"], ["Austin"], []]
    assert offline == [None, "network disabled"]


@test("outfit brief: a qualifying event becomes a finished brief with complete outfits; silent events produce nothing")
def _():
    write_json("closet.json", [item(id="oxford", category="shirt", formality=3, sleeve="long", occasion_tags=["business-casual"]),
                               item(id="trousers", category="pants", formality=3, occasion_tags=["business-casual"]),
                               item(id="loafers", category="loafers", formality=3, occasion_tags=["business-casual"])])
    write_json("people.json", {"self_emails": ["me@home.test"], "internal_domains": [], "people": {"investor@fund.test": {"name": "Ann Investor", "category": "business"}}})
    path = PROFILE_DIR / "_brief_events.json"
    path.write_text(json.dumps({"events": [
        _event("Breakfast w. Ann - Corner Deli", "100 Example Plaza, Dallas, TX", "investor@fund.test", False, [("investor@fund.test", "Ann Investor")]),
        _event("Legs Workout <> the user @ Gym", "100 Example St, Dallas, TX"),
        _event("Coffee <> the user @ Cafe", "1 Main St, Dallas, TX", attendees=[("new@person.test", "Sam Example")])]}))
    r = run("outfit_brief.py", "--events-json", str(path))
    path.unlink()
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    (brief,) = out["briefs"]
    assert brief["kind"] == "meeting-business" and brief["forecast"] is None and brief["forecast_missing_because"] == "network disabled"
    assert set(brief["top_outfits"][0]["items"]) == {"oxford", "trousers", "loafers"}
    assert [n["verdict"]["decision"] for n in out["needs_answer"]] == ["ask-person"]


def _caption_probe(code):
    r = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, %r); import proposal_caption as c\n%s" % (str(SCRIPTS_DIR), code)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@test("board caption and 🚫 button: exactly the approved wording")
def _():
    out = _caption_probe("""
p = {'event_title': "Breakfast w. Alex Morgan <> the user", 'event_start': '2026-09-22T08:00:00-05:00', 'reason': 'White shirt, grey trousers, loafers; add the navy blazer.',
     'meta': {'why': 'in person with Alex Morgan (business)', 'kind': 'meeting-business', 'person': {'name': 'Alex Morgan', 'email': 'b@x.test'},
              'forecast': {'feels_like_f': 82, 'wind_mph': 5, 'rain_chance_pct': 10, 'place': 'Dallas, Texas'}}}
old = {'event_title': 'Golf', 'event_start': '2026-09-19T13:30:00-05:00', 'reason': 'r', 'assumption': 'a', 'weather': 'w'}
kinds = [{'kind': 'dining', 'venue_name': 'Trattoria Example'}, {'kind': 'flight'}, {'kind': 'hosted-event'}, {'kind': 'golf-round'}, {'kind': 'dress-code'}]
print(json.dumps({'caption': c.caption(p), 'never': c.never_label(p['meta']), 'ack': c.never_ack(p['meta']), 'old': c.caption(old),
                  'labels': [c.never_label(k) for k in kinds], 'no_forecast': c.forecast_line({})}))""")
    assert out["caption"] == ("Breakfast w. Alex Morgan · Tue Sep 22, 8:00 AM\nWhy: in person with Alex Morgan (business)\n"
                              "White shirt, grey trousers, loafers; add the navy blazer.\nForecast: 82°F feels-like, wind 5 mph, rain 10% (Dallas)")
    assert out["never"] == "🚫 No outfits for Alex" and out["ack"] == "Understood — no more outfits for meetings with Alex. 🎩"
    assert out["labels"] == ["🚫 Not for Trattoria Example", "🚫 No flight-day outfits", "🚫 Not for events like this", "🚫 No golf-round outfits", None]
    assert out["old"].startswith("Golf — 2026-09-19T13:30") and out["no_forecast"] == "Forecast: not available"      # older proposals still render


@test("one-tap questions: exact wording, asked once, the answer is remembered and changes the verdict")
def _():
    write_json("people.json", {"self_emails": ["me@home.test"], "internal_domains": [], "people": {}})
    coffee = _event("Coffee <> the user @ Corner Cafe", "100 Example St, Dallas, TX", attendees=[("sam@fund.test", "Sam Example")])
    dinner = _event("Dinner - Trattoria Example <> the user @ Trattoria Example", "100 Example Ave, New York, NY 10001")
    path = PROFILE_DIR / "_watch_events.json"
    path.write_text(json.dumps({"events": [coffee, dinner]}))
    preview = json.loads(run("calendar_outfit_watch.py", "--dry-run", "--events-json", str(path)).stdout)
    assert preview["would_ask"] == ["First time I've seen Sam Example on your calendar in person (Coffee @ Corner Cafe, Sat Jan 5). Should meetings with Sam Example get an outfit?",
                                    "Dinner at Trattoria Example (Sat Jan 5). Dressy enough for an outfit?"]
    assert read_json("outfit_questions.json") == {}                                         # a dry run asks nothing
    probe = ("import sys, json; sys.path.insert(0, %r); import outfit_questions as q\n"
             "ev = json.load(open(%r))['events']\n"
             "from event_triage import Triage; t = Triage()\n"
             "sent = [q.ask(e, t.verdict(e)) for e in ev] + [q.ask(e, t.verdict(e)) for e in ev]\n"
             "print(json.dumps([s and [b['callback_data'] for row in s['reply_markup']['inline_keyboard'] for b in row] for s in sent]))") % (str(SCRIPTS_DIR), str(path))
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env={**os.environ, "WARDROBE_NO_SEND": "1"})
    assert r.returncode == 0, r.stderr
    first_who, first_venue, again_who, again_venue = json.loads(r.stdout)
    assert again_who is None and again_venue is None                                         # never the same question twice
    assert [c.rsplit("-", 1)[1] for c in first_who] == ["biz", "date", "fam", "friend"] and [c.rsplit("-", 1)[1] for c in first_venue] == ["yes", "no"]
    who_id, venue_id = first_who[0].split("-")[1], first_venue[0].split("-")[1]
    assert run("outfit_questions.py", "answer", "--id", who_id, "--value", "biz").stdout.strip() == "Got it — Sam Example is business. 🎩"
    assert "Nothing was changed" in run("outfit_questions.py", "answer", "--id", who_id, "--value", "friend").stdout          # already answered
    assert run("outfit_questions.py", "answer", "--id", venue_id, "--value", "no").stdout.strip() == "Got it — Trattoria Example is casual, no outfits. 🎩"
    assert read_json("people.json")["people"]["sam@fund.test"]["category"] == "business"
    after = _triage([coffee, dinner])
    path.unlink()
    assert after[coffee["title"]]["kind"] == "meeting-business" and after[dinner["title"]]["decision"] == "skip"


@test("🚫 button: one tap switches off that person, venue or kind, and the triage obeys")
def _():
    write_json("people.json", {"self_emails": ["me@home.test"], "internal_domains": [], "people": {"ann@fund.test": {"name": "Ann Investor", "category": "business"}}})
    meeting = _event("Breakfast w. Ann - Corner Deli", "100 Example Plaza, Dallas, TX", "ann@fund.test", False, [("ann@fund.test", "Ann Investor")])
    flight = _event("WN 1234 DAL → DEN <> the user @ Dallas Love Field", "Dallas Love Field")
    party = _event("Ticket: Tech Social", "Example Country Club, Fort Worth, TX")
    gala = _event("Charity gala", "Example Hotel, Dallas, TX")
    before = _triage([meeting, flight, party, gala])
    assert {v["decision"] for v in before.values()} == {"propose"}
    def board(pid, verdict):
        return {"id": pid, "event_key": pid + "00000000", "event_title": "x", "event_start": "2030-01-05T09:00:00-06:00", "occasion": "casual", "activity": "general",
                "items": [], "status": "proposed", "created_at": "2020-01-01T09:00:00-06:00",
                "meta": {"why": verdict["reason"], "kind": verdict["kind"], **{k: verdict[k] for k in ("person", "matched_phrase") if k in verdict}}}
    write_json("outfit_proposals.json", [board("eeeeeeeeeee1", before[meeting["title"]]), board("eeeeeeeeeee2", before[flight["title"]]), board("eeeeeeeeeee3", before[party["title"]])])
    assert run("outfit_callback.py", "--id", "eeeeeeeeeee1", "--action", "never").stdout.strip() == "Understood — no more outfits for meetings with Ann. 🎩"
    assert run("outfit_callback.py", "--id", "eeeeeeeeeee2", "--action", "never").stdout.strip() == "Understood — no more flight-day outfits. 🎩"
    assert run("outfit_callback.py", "--id", "eeeeeeeeeee3", "--action", "never").stdout.strip() == "Understood — no more outfits for events like this. 🎩"
    after = _triage([meeting, flight, party, gala])
    assert [after[e["title"]]["decision"] for e in (meeting, flight, party)] == ["skip", "skip", "skip"]
    assert after[gala["title"]]["decision"] == "propose"                                   # "ticket" events are off; a gala is a different kind
    assert read_json("outfit_proposals.json")[0]["status"] == "never" and read_json("style_signals.json") == []   # a switch-off is not a style complaint


@test("propose_from_brief: the model supplies only a choice and a sentence; bad input is refused")
def _():
    brief = {"event_key": "f" * 20, "event": {"event_id": "e1", "title": "Breakfast w. Ann <> the user", "start_iso": "2030-01-05T09:00:00-06:00"}, "extra": {},
             "why_an_outfit": "in person with Ann (business)", "kind": "meeting-business", "occasion": "business-casual", "activity": "work", "forecast": None,
             "top_outfits": [{"score": 9, "items": ["oxford", "trousers", "loafers"]}]}
    path = PROFILE_DIR / "_briefs.json"
    path.write_text(json.dumps([brief]))
    args = ["propose_from_brief.py", "--briefs", str(path), "--index", "0"]
    assert run(*args, "--choice", "2", "--reason", "A fine crisp outfit for breakfast.").returncode != 0        # only one outfit on offer
    assert run(*args, "--choice", "1", "--reason", "ok").returncode != 0                                          # not a sentence
    assert run("propose_from_brief.py", "--briefs", str(path), "--index", "4", "--choice", "1", "--reason", "A fine crisp outfit.").returncode != 0
    path.unlink()


def _board_probe(code):
    r = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, %r); %s" % (str(SCRIPTS_DIR), code)], capture_output=True, text=True,
                       env={**os.environ, "WARDROBE_NO_SEND": "1", "WARDROBE_NO_NETWORK": "1"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@test("board buttons: the approved four rows, and the caption shows swaps, taps and the wear")
def _():
    write_json("closet.json", _learning_closet())
    out = _board_probe("""
import proposal_caption as c
meta = {'why': 'in person with Ann (business)', 'kind': 'meeting-business', 'person': {'name': 'Ann Investor'}, 'forecast': None,
        'swaps': [{'old_label': 'Buck Mason grey tee', 'new_label': 'H&B white polo'}], 'notes': ['too warm.']}
p = {'id': 'a' * 12, 'event_title': 'Lunch <> the user', 'event_start': '2030-01-05T12:00:00-06:00', 'reason': 'Keep it light.', 'meta': meta, 'status': 'worn'}
import outfit_board  # importable without rendering
from send_outfit_proposal import board_markup
rows = board_markup(p)['inline_keyboard']
print(json.dumps({'caption': c.caption(p), 'rows': [[b['text'] for b in row] for row in rows], 'data': [b['callback_data'] for row in rows for b in row]}))""")
    assert out["rows"] == [["✅ Wore it", "👕 Wore something else"], ["Swap top", "Swap bottoms", "Swap shoes"],
                           ["Too formal", "Too casual", "Too warm", "Too cold"], ["🚫 No outfits for Ann"]]
    assert "outfit:swap-aaaaaaaaaaaa-top" in out["data"] and "confirm:outfitworn-aaaaaaaaaaaa" in out["data"] and "confirm:outfit-aaaaaaaaaaaa" not in out["data"]
    assert out["caption"].endswith("Forecast: not available\nSwapped: Buck Mason grey tee → H&B white polo\nNoted: too warm.\n✅ Worn, logged.")


@test("board taps: worn logs the wear and marks the hamper; swap replaces one piece from the same pool; notes accumulate")
def _():
    write_json("closet.json", _learning_closet() + [item(id="c-other-shirt", category="shirt", formality=2, occasion_tags=["casual"])])
    write_json("outfit_proposals.json", [{"id": "b" * 12, "event_key": "b" * 20, "event_title": "Lunch", "event_start": "2030-01-05T12:00:00-06:00",
                                          "occasion": "casual", "activity": "dining", "items": ["a-dressy-shirt", "pants", "shoes"], "status": "proposed",
                                          "meta": {"why": "x", "kind": "dining", "scoring": {"occasion": "casual", "activity": "dining"}}, "created_at": "2020-01-01T09:00:00-06:00"}])
    swapped = run("outfit_callback.py", "--id", "b" * 12, "--action", "swap-top")
    assert swapped.returncode == 0 and swapped.stdout.strip().startswith("Swapped "), swapped.stdout + swapped.stderr   # no message id in tests: the reply carries it
    saved = read_json("outfit_proposals.json")[0]
    assert saved["items"][0] in ("b-plain-tee", "c-other-shirt") and saved["items"][1:] == ["pants", "shoes"] and saved["swapped_out"] == ["a-dressy-shirt"]
    assert saved["meta"]["swaps"][0]["old"] == "a-dressy-shirt" and read_json("style_signals.json")[-1]["action"] == "swapped"
    second = run("outfit_callback.py", "--id", "b" * 12, "--action", "swap-top")
    assert second.returncode == 0 and read_json("outfit_proposals.json")[0]["items"][0] not in ("a-dressy-shirt", saved["items"][0])
    third = run("outfit_callback.py", "--id", "b" * 12, "--action", "swap-top")
    assert "that's the only one" in third.stdout                                  # pool exhausted: says so, changes nothing
    assert run("outfit_callback.py", "--id", "b" * 12, "--action", "swap-shoes").stdout.strip() == "No other shoes fits this event — that's the only one. 🎩"
    assert run("outfit_callback.py", "--id", "b" * 12, "--action", "too-warm").stdout.strip() == "Recorded as too warm. 🎩"
    assert read_json("outfit_proposals.json")[0]["meta"]["notes"] == ["too warm."]
    worn = run("outfit_callback.py", "--id", "b" * 12, "--action", "worn")
    assert worn.stdout.strip() == "Outfit and wear history recorded. 🎩", worn.stdout + worn.stderr
    history = read_json("outfit_history.json")[-1]
    assert history["activity"] == "dining" and history["items"][1:] == ["pants", "shoes"]
    states = {x["id"]: x["lifecycle_state"] for x in read_json("closet.json")}
    assert states[history["items"][0]] == "dirty" and states["pants"] == "active"           # a shirt goes to the hamper; pants are a 3-wear item
    assert read_json("outfit_proposals.json")[0]["status"] == "worn"


@test("evening-before delivery: today's boards go now, tomorrow's wait for 7:30pm and get one header")
def _():
    probe = ("import json, sys; sys.argv=['x']; sys.path.insert(0, %r); import calendar_outfit_watch as w; from datetime import datetime\n"
             "day = lambda d: {'event': {'start_iso': d + 'T09:00:00-06:00', 'title': 'Breakfast w. Ann - Corner Deli <> the user'}, 'kind': 'meeting-business'}\n"
             "flight = {'event': {'start_iso': '2030-01-06T06:25:00-06:00', 'title': 'WN 1300 DAL → LGA <> the user @ Dallas Love Field'}, 'kind': 'flight'}\n"
             "morning, evening = datetime(2030, 1, 5, 9, 0), datetime(2030, 1, 5, 19, 30)\n"
             "print(json.dumps({'today_morning': w.due_now(day('2030-01-05'), morning), 'tomorrow_morning': w.due_now(day('2030-01-06'), morning),"
             " 'tomorrow_evening': w.due_now(day('2030-01-06'), evening), 'later_evening': w.due_now(day('2030-01-07'), evening),"
             " 'header': w.tomorrow_header([day('2030-01-06'), flight])}))") % str(SCRIPTS_DIR)
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert (out["today_morning"], out["tomorrow_morning"], out["tomorrow_evening"], out["later_evening"]) == (True, False, True, False)
    assert out["header"] == "👔 Tomorrow: Breakfast w. Ann (9:00 AM) · Flight to LGA (6:25 AM)"


@test("packing list: pairs the outbound with its return, picks real pieces, flags the hamper, sends once")
def _():
    write_json("closet.json", [
        item(id="oxford", category="shirt", formality=3, sleeve="long", occasion_tags=["business-casual"]),
        item(id="tee", category="t-shirt", formality=2, occasion_tags=["casual"], lifecycle_state="dirty"),
        item(id="chinos", category="pants", formality=3, occasion_tags=["business-casual"]),
        item(id="shorts", category="shorts", formality=2, occasion_tags=["casual"]),
        item(id="loafers", category="loafers", formality=3, occasion_tags=["business-casual"]),
        item(id="trainers", category="athletic_shoes", formality=1, occasion_tags=["athletic"]),
        item(id="belt", category="belt", formality=3, occasion_tags=["business-casual"]),
    ])
    write_json("people.json", {"self_emails": ["me@home.test"], "internal_domains": [], "people": {"ann@fund.test": {"name": "Ann", "category": "business"}}})
    events = [_event("WN 1300 DAL → LGA <> the user @ Dallas Love Field", "Dallas Love Field", day="2030-01-07", start_iso="2030-01-07T06:25:00-06:00"),
              _event("Lunch w. Ann", "100 Example Ave, New York, NY", "ann@fund.test", False, [("ann@fund.test", "Ann")], day="2030-01-08", start_iso="2030-01-08T12:30:00-05:00"),
              _event("WN 2397 LGA → DAL <> the user @ LaGuardia Airport", "LaGuardia Airport", day="2030-01-12", start_iso="2030-01-12T08:45:00-05:00"),
              _event("WN 9 DEN → DAL <> the user @ DEN", "DEN", day="2030-01-20", start_iso="2030-01-20T08:45:00-08:00")]      # a return with no outbound: not a trip
    path = PROFILE_DIR / "_pack_events.json"
    path.write_text(json.dumps({"events": events}))
    early = json.loads(run("packing_list.py", "--dry-run", "--events-json", str(path), "--today", "2030-01-03").stdout)
    assert early["packing"] == []                                                      # 4 days out: not yet
    due = json.loads(run("packing_list.py", "--dry-run", "--events-json", str(path), "--today", "2030-01-05").stdout)
    assert [p["trip"] for p in due["packing"]] == ["2030-01-07-LGA"]
    text = due["packing"][0]["text"]
    assert text.startswith("🧳 Packing for New York · Jan 7–12 (5 nights)\nForecast there: not available yet\nOn the plane: ")
    assert "• Tops: " in text and "oxford" not in text and "unbranded" in text.lower() or "Tops:" in text
    assert "In the hamper now:" in text and "Wash before you go." in text            # the dirty tee was picked and flagged
    assert "• Also: " in text                                                          # business lunch: a belt
    sent = json.loads(run("packing_list.py", "--events-json", str(path), "--today", "2030-01-05").stdout)
    assert not sent["dry_run"] and len(sent["packing"]) == 1
    again = json.loads(run("packing_list.py", "--events-json", str(path), "--today", "2030-01-06").stdout)
    assert again["packing"] == []                                                      # already sent for this trip
    path.unlink()


@test("delivered order: the question is asked once with the approved wording; Add / Returning / Not clothing each do the right thing")
def _():
    write_json("closet.json", [item(id="hb-mason-tee-black", category="t-shirt", brand="Holderness & Bourne", color="black", formality=2)])
    common = ["--retailer", "Holderness & Bourne", "--brand", "Holderness & Bourne", "--item", "The Mason T-Shirt", "--category", "t-shirt",
              "--color", "heathered gray", "--size", "L", "--price", "55", "--order-id", "HB-1001"]
    first = run("propose_closet_addition.py", *common)
    assert first.returncode == 0, first.stdout + first.stderr
    out = json.loads(first.stdout)
    assert out["text"] == "📦 Delivered: Holderness & Bourne The Mason T-Shirt, heathered gray, size L (Holderness & Bourne, $55). Add it to your closet?"
    draft = read_json("pending_closet_additions.json")[0]
    assert draft["id"] == out["item_id"] and draft["category"] == "t-shirt" and draft["sleeve"] == "short" and draft["formality"] == 2 and draft["added_via"] == "receipt"
    assert "formality" in draft["flagged_fields"] and "sleeve" in draft["flagged_fields"] and "purchase_price" not in draft["flagged_fields"]
    assert "already_asked" in json.loads(run("propose_closet_addition.py", *common).stdout)          # never asked twice
    assert run("propose_closet_addition.py", *common[:6], "--category", "hoodie", "--color", "x").returncode != 0
    assert run("closet_addition_callback.py", "--id", out["asked"], "--answer", "add").stdout.strip() == \
        "✅ Added Holderness & Bourne The Mason T-Shirt to your closet. No photo yet — send one when you can and I'll attach it. 🎩"
    assert any(x["id"] == out["item_id"] for x in read_json("closet.json")) and read_json("pending_closet_additions.json") == []
    assert "Nothing was changed" in run("closet_addition_callback.py", "--id", out["asked"], "--answer", "add").stdout
    dup = json.loads(run("propose_closet_addition.py", *common[:8], "--color", "black", "--order-id", "HB-1002").stdout)
    assert "Might already be in your closet as hb-mason-tee-black" in dup["text"]
    assert run("closet_addition_callback.py", "--id", dup["asked"], "--answer", "return").stdout.strip() == "↩️ Noted — returning it. 🎩"
    assert read_json("pending_closet_additions.json") == []
    other = json.loads(run("propose_closet_addition.py", "--retailer", "Amazon", "--brand", "Anker", "--item", "cable", "--category", "bag", "--color", "black", "--order-id", "A1").stdout)
    assert run("closet_addition_callback.py", "--id", other["asked"], "--answer", "notclothing").stdout.strip() == "OK — not clothing, ignored. 🎩"


@test("photo wear: asks with the approved wording, logs only on the tap, takes a correction")
def _():
    write_json("closet.json", [item(id="tee", category="t-shirt", brand="Buck Mason", color="washed black", formality=2, occasion_tags=["casual"]),
                               item(id="jeans", category="jeans", brand="Wrangler", color="light wash blue", formality=2, occasion_tags=["casual"]),
                               item(id="polo", category="polo", brand="H&B", color="white", formality=3, occasion_tags=["smart-casual"])])
    assert run("propose_wear_from_photo.py", "--items", "tee,made-up").returncode != 0
    asked = json.loads(run("propose_wear_from_photo.py", "--items", "tee,jeans").stdout)
    assert asked["text"] == "Looks like: Buck Mason washed black t-shirt, Wrangler light wash blue jeans. Log as worn today?"
    assert read_json("outfit_history.json") == []                                      # nothing logged until he taps
    assert run("wear_photo_callback.py", "--id", asked["asked"], "--answer", "notquite").stdout.strip() == "OK — tell me what I got wrong and I'll log it right. 🎩"
    pending_file = lambda: next((PROFILE_DIR / "_state").glob("pending_wear_*.json"), None)     # the scratch copy of the skill's state file
    assert json.loads(pending_file().read_text())["status"] == "correcting"
    fixed = run("wear_photo_callback.py", "--id", asked["asked"], "--answer", "correct", "--items", "polo,jeans")
    assert fixed.stdout.strip() == "✅ Logged as worn today: H&B white polo, Wrangler light wash blue jeans. 🎩", fixed.stdout + fixed.stderr
    assert read_json("outfit_history.json")[-1]["items"] == ["polo", "jeans"] and pending_file() is None
    assert "No open photo question" in run("wear_photo_callback.py", "--id", asked["asked"], "--answer", "log").stdout
    asked2 = json.loads(run("propose_wear_from_photo.py", "--items", "tee").stdout)
    assert run("wear_photo_callback.py", "--id", asked2["asked"], "--answer", "log").stdout.strip() == "✅ Logged as worn today: Buck Mason washed black t-shirt. 🎩"
    assert {x["id"]: x["lifecycle_state"] for x in read_json("closet.json")}["tee"] == "dirty"


@test("weekly summary: one line only in a week where something happened")
def _():
    assert run("weekly_summary.py", "--brief", "--today", "2030-01-12").stdout.strip() == ""
    write_json("closet.json", [item(id="polo1", category="polo", brand="H&B", color="white"), item(id="polo2", category="polo", brand="H&B", color="navy"),
                               item(id="grey-trousers", category="pants", brand="S&L", color="grey")])
    write_json("outfit_history.json", [{"id": "o1", "date": "2030-01-08", "activity": "work", "items": ["polo1"], "favorite": False, "notes": "", "feedback": ""},
                                       {"id": "o2", "date": "2030-01-10", "activity": "dining", "items": ["polo2"], "favorite": False, "notes": "", "feedback": ""},
                                       {"id": "o3", "date": "2030-01-11", "activity": "work", "items": ["polo1"], "favorite": False, "notes": "", "feedback": ""},
                                       {"id": "old", "date": "2029-12-01", "activity": "work", "items": ["polo1"], "favorite": False, "notes": "", "feedback": ""}])
    write_json("style_signals.json", [{"date": "2030-01-09", "source": "outfit-proposal-response", "action": "swapped", "items": ["grey-trousers"], "activity": "work"}])
    write_json("people.json", {"self_emails": [], "internal_domains": [], "people": {"d@x.test": {"name": "Jordan Example", "category": "friend", "evidence": "The user 2030-01-10: friend"},
                                                                                     "o@x.test": {"name": "Old Pal", "category": "friend", "evidence": "The user 2029-06-01: friend"}}})
    line = run("weekly_summary.py", "--brief", "--today", "2030-01-12").stdout.strip()
    assert line == "👔 Wardrobe this week: 3 outfits worn (H&B polos 3 times) · swapped out the S&L grey pants · no more outfits for Jordan.", line


@test("activity policy: explicit exclusive override is a hard filter")
def _():
    write_json("closet.json", [item(id="reserved-polo", category="polo")])
    set_result = run("activity_policy.py", "set", "--item", "reserved-polo", "--allowed", "golf-round", "--preferred", "golf-round", "--excluded", "general", "--exclusive", "true", "--evidence", "User reserved this for golf")
    assert set_result.returncode == 0
    general = json.loads(run("outfit_candidates.py", "--occasion", "smart-casual", "--activity", "general").stdout)
    golf = json.loads(run("outfit_candidates.py", "--occasion", "smart-casual", "--activity", "golf-round").stdout)
    assert not general.get("candidates", {}).get("base_top")
    assert [x["id"] for x in golf["candidates"]["base_top"]] == ["reserved-polo"]


def main():
    backup_dir = Path(tempfile.mkdtemp()) / "profile_backup"
    shutil.copytree(PROFILE_DIR, backup_dir)

    passed, failed = 0, 0
    failures = []
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None      # run the tests whose name contains this
    selected = [(n, f) for n, f in TESTS if not only or only.lower() in n.lower()]
    try:
        for name, fn in selected:
            shutil.rmtree(PROFILE_DIR)
            shutil.copytree(backup_dir, PROFILE_DIR)
            reset_profile()
            try:
                fn()
                passed += 1
                print(f"PASS: {name}")
            except AssertionError as e:
                failed += 1
                failures.append(name)
                print(f"FAIL: {name}\n    {e}")
                if only:
                    import traceback; traceback.print_exc()
            except Exception as e:
                failed += 1
                failures.append(name)
                print(f"ERROR: {name}\n    {type(e).__name__}: {e}")
                if only:
                    import traceback; traceback.print_exc()
    finally:
        shutil.rmtree(PROFILE_DIR)
        shutil.copytree(backup_dir, PROFILE_DIR)
        shutil.rmtree(backup_dir.parent)

    print(f"\n{passed} passed, {failed} failed out of {len(selected)} total")
    if failures:
        print("Failed: " + ", ".join(failures))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
