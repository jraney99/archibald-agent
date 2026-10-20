#!/usr/bin/env python3
"""Wardrobe analytics (DESIGN.md Module 7) — treats the closet as a system,
not a list: usage patterns, possible duplicates, redundancy clusters (3+
items serving the same purpose), coverage gaps, and role coverage by
occasion_tags, all computed from existing closet data (wear_count, category,
formality, season, lifecycle_state, occasion_tags). No new fields required.
Ties back to the skill's stated success metrics: are items actually getting
worn, is there redundant buying, are there real gaps worth an intentional
purchase (vs. an impulse one)? This is the grounding data for Shopping
Advisor's "wardrobe fit" reasoning (SKILL.md) — the script surfaces facts
(counts, clusters, gaps), the agent narrates what they mean, never the
reverse.

Usage: python3 closet_report.py
"""
import json
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

try:
    import yaml
except ImportError:
    import sys
    sys.exit("PyYAML is required: pip install pyyaml")

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from output_mode import Report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLOSET_PATH = ROOT / "profile" / "closet.json"
VOCAB_PATH = ROOT / "vocab.yaml"
COLOR_ANALYSIS_PATH = ROOT / "profile" / "color_analysis.yaml"

FORMALITY_TOLERANCE = 1
COLOR_SIMILARITY_THRESHOLD = 0.8


def is_valid_formality(f):
    """A closet item's formality should be an int 1-5 per vocab.yaml, but
    this file reads raw JSON that may not have gone through validate_closet.py
    since it was last written — bool is explicitly excluded since Python's
    bool is an int subclass and would otherwise silently compare as 0/1."""
    return f is not None and not isinstance(f, bool) and isinstance(f, (int, float))


def basics_categories(vocab):
    """The `basics` category group (socks, underwear, ...) — foundation
    garments that are legitimately owned in multiples. Excluded from
    duplicate/redundancy detection: fifteen identical black socks is a
    stocked drawer, not a purchase mistake the way three navy sweaters is."""
    return set(vocab.get("category_groups", {}).get("basics", []))


def colors_match(a, b):
    """Substring containment catches naming variants (navy / navy blue,
    brown / dark brown) that plain sequence similarity scores too low."""
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False, 0.0
    a_l, b_l = a.strip().lower(), b.strip().lower()
    if a_l in b_l or b_l in a_l:
        return True, 1.0
    sim = SequenceMatcher(None, a_l, b_l).ratio()
    return sim > COLOR_SIMILARITY_THRESHOLD, sim


def report_usage(r, closet):
    active = [i for i in closet if i.get("lifecycle_state") == "active"]
    tracked = sum(i.get("wear_count", 0) for i in active)
    section = r.section("📊", "Usage", headline=f"{tracked} tracked wears")

    most_worn = sorted(active, key=lambda i: i.get("wear_count", 0), reverse=True)[:5]
    if most_worn:
        section.line("Most worn")
        for i in most_worn:
            section.bullet(f"{i.get('id', '?')} · {i.get('wear_count', 0)} wears")

    # A wear_count of 0 conflates two very different situations: a genuinely
    # new item, and a pre-existing item catalogued in bulk (e.g. a photo
    # pass over an established wardrobe) whose tracked wear count only
    # starts now. estimated_frequency is the self-reported baseline
    # captured once at intake for exactly that case — split it out so
    # "never worn" is never asserted about something the user already
    # said they wear regularly, just untracked so far.
    zero_wear = [i for i in active if i.get("wear_count", 0) == 0]
    untracked_with_baseline = [i for i in zero_wear if i.get("estimated_frequency")]
    never_worn = [i for i in zero_wear if not i.get("estimated_frequency")]

    if never_worn:
        worn = r.section("🕸", "Never worn", headline=f"{len(never_worn)} item(s)",
                         subtitle="no tracked wears, no self-reported baseline")
        for i in never_worn[:10]:
            worn.bullet(f"{i.get('id', '?')} · {i.get('category')} · added {i.get('added_date', '?')}")
        if len(never_worn) > 10:
            worn.bullet(f"and {len(never_worn) - 10} more")

    if untracked_with_baseline:
        r.section("📋", "Untracked", headline=f"{len(untracked_with_baseline)} item(s)",
                  subtitle="self-reported usage on file",
                  note="Recorded at intake; tracked wear counts only start now.")
        r.handling(f"Tracking wears for {len(untracked_with_baseline)} catalogued item(s)",
                   key="tracking catalogued wears")


def report_duplicates(r, closet, vocab):
    section = r.section("🔁", "Possible duplicates")
    basics = basics_categories(vocab)
    active = [i for i in closet if i.get("lifecycle_state") == "active" and i.get("category") not in basics]
    found = False
    pairs = set()

    for a_idx, a in enumerate(active):
        for b in active[a_idx + 1:]:
            if a.get("category") != b.get("category"):
                continue
            # Missing formality on either side shouldn't hide a real possible
            # duplicate — under-reporting here is the worse failure (a missed
            # "you already own one" is exactly what this report is for).
            a_formality, b_formality = a.get("formality"), b.get("formality")
            formality_unknown = not is_valid_formality(a_formality) or not is_valid_formality(b_formality)
            if not formality_unknown and abs(a_formality - b_formality) > FORMALITY_TOLERANCE:
                continue
            color_matched, sim = colors_match(a.get("color"), b.get("color"))
            if color_matched:
                found = True
                pairs.add(a.get("id"))
                pairs.add(b.get("id"))
                note = " · formality unknown on one side, unverified" if formality_unknown else ""
                section.bullet(f"{a.get('id', '?')} <-> {b.get('id', '?')} · "
                               f"{a.get('category')}, similar color ({sim:.2f}){note}")

    section.headline = f"{len(section.lines)} pair(s)" if found else "none found"
    return pairs


def report_redundancy_clusters(r, closet, matched_pairs, vocab):
    """Groups of 3+ active items in the same category serving the same
    purpose (PART 2's "three navy sweaters" case) — a different signal than
    a pairwise duplicate: not "these two might be the same," but "this many
    of these is more than the wardrobe needs," which is what should actually
    inform a skip/consolidate recommendation, not just a duplicate flag."""
    section = r.section("🧺", "Redundancy clusters", subtitle="3+ serving the same purpose")
    basics = basics_categories(vocab)
    active = [i for i in closet if i.get("lifecycle_state") == "active" and i.get("category") not in basics]
    seen_clusters = []
    any_cluster = False

    for i, item in enumerate(active):
        if item.get("id") not in matched_pairs:
            continue
        if any(item.get("id") in cluster for cluster in seen_clusters):
            continue
        cluster = [item]
        for other in active[i + 1:]:
            if other.get("category") != item.get("category"):
                continue
            # Same formality-tolerance rule as report_duplicates — otherwise
            # this would flag two items sharing a category/color as
            # "redundant" even when they serve genuinely different formality
            # tiers (a loungewear crewneck and a business-casual v-neck,
            # both navy, are not the same purchase mistake).
            item_formality, other_formality = item.get("formality"), other.get("formality")
            formality_unknown = not is_valid_formality(item_formality) or not is_valid_formality(other_formality)
            if not formality_unknown and abs(item_formality - other_formality) > FORMALITY_TOLERANCE:
                continue
            matched, _ = colors_match(item.get("color"), other.get("color"))
            if matched:
                cluster.append(other)
        if len(cluster) >= 3:
            any_cluster = True
            ids = [c.get("id", "?") for c in cluster]
            seen_clusters.append(set(ids))
            section.bullet(f"{len(cluster)}x {item.get('category')} in {item.get('color')} · "
                           f"{', '.join(ids)} · likely more than one purpose needs")

    section.headline = f"{len(seen_clusters)} cluster(s)" if any_cluster else "none found"


def report_role_coverage(r, closet, vocab):
    """Coverage by occasion_tags — the per-item "roles" PART 2 asks about
    (a navy blazer tagged business-casual/dinner/travel). Complements
    report_gaps (which derives coverage from formality) with the roles the
    user actually assigned, since occasion_tags is optional per item and the
    two views can disagree in informative ways (e.g. formality says a tier
    is covered, but no item is actually tagged for it — worth surfacing,
    not treated as a hard gap since occasion_tags is never required)."""
    active = [i for i in closet if i.get("lifecycle_state") == "active"]
    tagged = [i for i in active if isinstance(i.get("occasion_tags"), list) and i["occasion_tags"]]

    if not tagged:
        r.section("🎯", "Role coverage", subtitle="by occasion tags", headline="not tagged",
                  note="Occasion tags are optional; nothing to report until items carry them.")
        return
    section = r.section("🎯", "Role coverage", subtitle="by occasion tags",
                        headline=f"{len(tagged)} tagged item(s)")

    counts = {tier: 0 for tier in vocab["occasion_tiers"]}
    for item in tagged:
        for tag in item["occasion_tags"]:
            if tag in counts:
                counts[tag] += 1

    thin = [tier for tier, n in counts.items() if n == 0]
    for tier, n in counts.items():
        section.row(tier, f"{n} item(s)")
    if thin:
        section.bullet(f"Nothing tagged for: {', '.join(thin)} — only meaningful if you tag "
                       "items for those occasions regularly.")


def report_gaps(r, closet, vocab):
    section = r.section("🕳", "Coverage gaps")
    active = [i for i in closet if i.get("lifecycle_state") == "active"]

    no_formality_count = sum(1 for i in active if not is_valid_formality(i.get("formality")))
    if no_formality_count:
        section.bullet(f"{no_formality_count} active item(s) have no usable formality and were "
                       "excluded — what they cover cannot be verified.")
        r.needs_you(f"Set formality on {no_formality_count} active item(s) so coverage can be checked",
                    key="set formality")
    active = [i for i in active if is_valid_formality(i.get("formality"))]

    category_to_slot = {}
    for slot, categories in vocab["outfit_slots"].items():
        for cat in categories:
            category_to_slot[cat] = slot

    any_gap = False
    for occasion, target_formality in vocab["occasion_formality"].items():
        matches = [i for i in active if abs(i["formality"] - target_formality) <= FORMALITY_TOLERANCE]
        slots_present = {category_to_slot.get(i.get("category")) for i in matches}

        # full_body (a suit) and athletic_wear (a tracksuit/one-piece) both
        # cover top+bottoms on their own — same substitution rule for either.
        has_top = bool({"base_top", "layering_top", "full_body", "athletic_wear"} & slots_present)
        has_bottom = bool({"bottoms", "full_body", "athletic_wear"} & slots_present)
        has_footwear = "footwear" in slots_present

        missing = []
        if not has_top:
            missing.append("top")
        if not has_bottom:
            missing.append("bottoms")
        if not has_footwear:
            missing.append("footwear")

        if missing:
            any_gap = True
            section.row(occasion, "no active " + "/".join(missing))

    section.headline = "gaps found" if any_gap else "none"
    if not any_gap:
        section.note = "Every occasion tier has at least one complete outfit available."


def report_palette_coverage(r, closet):
    """How much of the active closet actually sits in the user's colour
    palette (DESIGN.md Module 11). Reads each item's `palette_match` snapshot
    (set at intake by color_match.py) — never recomputes here, and skips
    cleanly if there's no colour analysis on file or nothing's been matched
    yet. Off-palette items are surfaced as data; SKILL.md's rule is to only
    raise them when asked or directly relevant, never as an unprompted nag."""
    if not COLOR_ANALYSIS_PATH.exists():
        return
    active = [i for i in closet if i.get("lifecycle_state") == "active"]
    matched = [i for i in active if isinstance(i.get("palette_match"), dict)
               and i["palette_match"].get("verdict")]

    if not matched:
        r.section("🎨", "Palette coverage", headline="not matched yet",
                  note="A palette match is recorded at intake once a colour analysis is on file.")
        return

    buckets = {"in-palette": [], "borderline": [], "reject": []}
    for i in matched:
        v = i["palette_match"]["verdict"]
        buckets.setdefault(v, []).append(i)

    total = len(matched)
    in_pct = round(100 * len(buckets["in-palette"]) / total)
    section = r.section("🎨", "Palette coverage", headline=f"{in_pct}% in palette")
    section.row("In palette", f"{len(buckets['in-palette'])} of {total}")
    section.row("Borderline", str(len(buckets["borderline"])))
    section.row("Off palette", str(len(buckets["reject"])))
    if total < len(active):
        section.bullet(f"{len(active) - total} active item(s) have no palette match and are "
                       "excluded from the percentage.")
    for i in buckets["reject"]:
        pm = i["palette_match"]
        section.bullet(f"off palette: {i.get('id', '?')} ({i.get('color')}) · nearest was "
                       f"{pm.get('name')} at ΔE {pm.get('delta_e')}")


def main():
    if not CLOSET_PATH.exists():
        print("No closet.json found.")
        return
    with open(CLOSET_PATH) as f:
        closet = json.load(f)
    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    if not closet:
        print("Closet is empty — nothing to report on.")
        return

    active = [i for i in closet if i.get("lifecycle_state") == "active"]
    r = Report("👔", "Wardrobe report", headline=f"{len(active)} active items",
               tldr="The closet read as a system: what gets worn, what repeats, what is missing.",
               coverage=f"As of {date.today().isoformat()} · {len(closet)} catalogued items")
    report_usage(r, closet)
    matched_pairs = report_duplicates(r, closet, vocab)
    report_redundancy_clusters(r, closet, matched_pairs, vocab)
    report_gaps(r, closet, vocab)
    report_role_coverage(r, closet, vocab)
    report_palette_coverage(r, closet)
    print(r.render())


if __name__ == "__main__":
    main()
