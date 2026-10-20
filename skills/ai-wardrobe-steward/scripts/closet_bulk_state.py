#!/usr/bin/env python3
"""Bulk lifecycle_state changes — for "pack away all my shorts for winter"
style requests that would otherwise mean editing items one at a time.

Usage:
  python3 closet_bulk_state.py --category shorts --set-state packed-away [--dry-run|--apply]
  python3 closet_bulk_state.py --season summer --current-state active --set-state packed-away --apply
  python3 closet_bulk_state.py --ids id1,id2,id3 --set-state active --apply
  python3 closet_bulk_state.py --ids id1 --set-state retired --disposition donated --apply
  python3 closet_bulk_state.py --ids id1 --set-state retired --disposition sold --disposition-value 40 --apply

At least one of --category / --season / --ids / --current-state is required
— an unscoped call would silently match the whole closet. Defaults to a
dry run (lists what WOULD change, writes nothing) unless --apply is passed —
same "show before you commit" spirit as the rest of this skill, just a flag
instead of a full pending-file for something this low-risk and reversible.

--disposition/--disposition-value/--disposition-date are only meaningful
alongside --set-state retired (see vocab.yaml disposition_types) — retiring
an item without a disposition is still fine (matches historical retirements
where the outcome was never recorded); a disposition without --set-state
retired would be a stale record nothing else expects, so it's rejected.
--disposition-date defaults to today if a disposition is given but no date.
"""
import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
CLOSET_PATH = ROOT / "profile" / "closet.json"
VOCAB_PATH = ROOT / "vocab.yaml"
VALIDATE_SCRIPT = Path(__file__).resolve().parent / "validate_closet.py"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", default=None)
    parser.add_argument("--season", default=None)
    parser.add_argument("--ids", default=None, help="comma-separated item ids")
    parser.add_argument("--current-state", default=None, help="only affect items currently in this lifecycle_state")
    parser.add_argument("--set-state", required=True)
    parser.add_argument("--disposition", default=None, help="only valid with --set-state retired")
    parser.add_argument("--reason", default=None,
                        help="REQUIRED with --set-state retired: why it left (vocab.yaml retirement_reasons). "
                             "Half the old notes said only 'didn't fit' or nothing, which taught nothing.")
    parser.add_argument("--reason-note", default=None, help="optional: his own words, kept verbatim")
    parser.add_argument("--disposition-value", type=float, default=None)
    parser.add_argument("--disposition-date", default=None)
    parser.add_argument("--apply", action="store_true", help="without this, only previews what would change")
    args = parser.parse_args()

    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    if args.set_state not in vocab["lifecycle_states"]:
        sys.exit(f"'{args.set_state}' is not a valid lifecycle_state. Valid: {vocab['lifecycle_states']}")
    if args.current_state and args.current_state not in vocab["lifecycle_states"]:
        sys.exit(f"'{args.current_state}' is not a valid lifecycle_state. Valid: {vocab['lifecycle_states']}")

    reasons = vocab.get("retirement_reasons", [])
    if args.set_state == "retired" and not args.reason:
        sys.exit("--reason is required when retiring: " + ", ".join(reasons))
    if args.reason and args.set_state != "retired":
        sys.exit("--reason only applies when retiring an item.")
    if args.reason and args.reason not in reasons:
        sys.exit(f"'{args.reason}' is not a retirement reason. Valid: {', '.join(reasons)}")
    if args.disposition and args.set_state != "retired":
        sys.exit("--disposition is only valid together with --set-state retired.")
    if args.disposition and args.disposition not in vocab.get("disposition_types", []):
        sys.exit(f"'{args.disposition}' is not a valid disposition. Valid: {vocab.get('disposition_types')}")
    if (args.disposition_value is not None or args.disposition_date) and not args.disposition:
        sys.exit("--disposition-value/--disposition-date require --disposition.")
    if args.disposition and not args.disposition_date:
        args.disposition_date = date.today().isoformat()

    if not any([args.category, args.season, args.ids, args.current_state]):
        sys.exit("At least one of --category, --season, --ids, --current-state is required — an unscoped call would match the whole closet.")

    with open(CLOSET_PATH) as f:
        closet = json.load(f)

    id_filter = None
    missing_ids = []
    if args.ids:
        id_filter = {i.strip() for i in args.ids.split(",") if i.strip()}
        found_ids = {item.get("id") for item in closet}
        missing_ids = sorted(id_filter - found_ids)

    matches = []
    for item in closet:
        if id_filter is not None and item.get("id") not in id_filter:
            continue
        if args.category and item.get("category") != args.category:
            continue
        if args.season and item.get("season") not in (args.season, "all-season"):
            continue
        if args.current_state and item.get("lifecycle_state") != args.current_state:
            continue
        matches.append(item)

    if missing_ids:
        print(f"Note: {len(missing_ids)} id(s) not found in closet, skipped: {', '.join(missing_ids)}")

    if not matches:
        print("No items match these filters — nothing to do.")
        return

    print(f"{len(matches)} item(s) {'will be' if args.apply else 'would be'} set to '{args.set_state}':")
    for item in matches:
        print(f"  - {item.get('id')}: {item.get('category')}, currently '{item.get('lifecycle_state')}'")
    if args.disposition:
        note = f" for ${args.disposition_value:,.2f}" if args.disposition_value is not None else ""
        print(f"  disposition: {args.disposition}{note} on {args.disposition_date}")

    if not args.apply:
        print("\nDry run — pass --apply to actually make this change.")
        return

    for item in matches:
        item["lifecycle_state"] = args.set_state
        if args.reason:
            item["retirement"] = {"reason": args.reason, "date": date.today().isoformat(),
                                  **({"note": args.reason_note} if args.reason_note else {})}
        if args.disposition:
            item["disposition"] = args.disposition
            item["disposition_date"] = args.disposition_date
            if args.disposition_value is not None:
                item["disposition_value"] = args.disposition_value
    with open(CLOSET_PATH, "w") as f:
        json.dump(closet, f, indent=2)

    print(f"\nUpdated {len(matches)} item(s).")

    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print("Validation failed after write — review closet.json.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
