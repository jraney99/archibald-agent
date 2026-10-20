#!/usr/bin/env python3
"""The "where data lives" rule, enforced (workspace/Documentation/rules/32-where-data-lives.md).

    python3 test_data_layout.py                  check
    python3 test_data_layout.py --write-baseline  re-snapshot (only ever to REMOVE entries)

Run daily by test-fixtures/test-all.mjs (guard-suite-daily-selfcheck), so a new loose
file or a new typed path is reported within a day instead of found in an audit.

The 2026-09-20 snapshot of what already broke the rule is test-fixtures/
data-layout-baseline.json. It is a ratchet: nothing may be added to it, and an entry
that no longer applies must be taken out.
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_paths as dp  # noqa: E402

OPENCLAW = dp.OPENCLAW
BASELINE = OPENCLAW / "test-fixtures" / "data-layout-baseline.json"

# OpenClaw's own files and credentials: the only things that belong at the root.
RUNTIME = re.compile(r"^(openclaw(\.redacted)?\.json|schema\.json|exec-approvals\.json|update-check\.json|"
                     r"gateway-supervisor-.*\.json|client_secret_.*\.json|.*token.*\.json|.*credential.*\.json)$")
ABSOLUTE = re.compile(r"""["'`](?:~/\.openclaw|~/\.openclaw)/[\w.\-/ ]*?([\w.\-]+\.jsonl?)["'`]""")
LITERAL = re.compile(r"""["'`]([\w.\-]+\.jsonl?)["'`]""")


def scripts():
    for base, pattern in ((dp.SKILLS, "**/*.py"), (OPENCLAW / "plugins", "*/*.mjs")):
        for f in sorted(base.glob(pattern)):
            name = f.name
            if "stress" in f.parts or "__pycache__" in f.parts or "node_modules" in f.parts or name.startswith("test_") \
                    or name.startswith("test") and name.endswith(".mjs") or ".test." in name or name == "data_paths.py":
                continue
            yield f


def typed_paths(text: str, suffix: str = ".py") -> set:
    """Data files a script names for itself: any absolute ~/.openclaw *.json path, plus any
    registered file named as a bare literal ("food_log.json") — unless the script uses the
    helper, where naming the file IS how it asks: path("food_log.json").

    A bare registered NAME is only a violation where the helper could have been used, so it
    is not counted in a plugin's .mjs: data_paths.py is Python and there is no JS equivalent,
    so a JS plugin has no way to ask. The absolute-path rule still applies to both — that is
    the one that actually pins a file in place."""
    found = {m.group(1) for m in ABSOLUTE.finditer(text) if not RUNTIME.match(m.group(1))}
    uses_helper = re.search(r"^\s*(?:from data_paths import|import data_paths)\b", text, re.M)
    if suffix == ".py" and not uses_helper:
        found |= {m.group(1) for m in LITERAL.finditer(text) if m.group(1) in dp.REGISTRY}
    return found


def snapshot() -> dict:
    typed, named = {}, set()
    for f in scripts():
        text = f.read_text(errors="ignore")
        named |= {m.group(1) for m in LITERAL.finditer(text)}
        hits = typed_paths(text, f.suffix)
        if hits:
            typed[str(f.relative_to(OPENCLAW))] = sorted(hits)
    return {
        "typed_paths": typed,
        # every *.json name any script mentioned at snapshot time, however the path was built
        # (OPENCLAW / "x.json", os.path.join, an f-string): a file with one of these names
        # turning up at the root later is old behaviour, not a new loose file.
        "names_in_code": sorted(named),
        "shared_data": sorted(p.name for p in (dp.SKILLS / "shared").glob("*.json*")),
        "note": "2026-09-20 snapshot of what already broke the rule. Remove entries as they are fixed; never add.",
    }


class Layout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = json.loads(BASELINE.read_text())
        cls.now = snapshot()

    def test_no_script_types_a_new_data_path(self):
        new = {f: sorted(set(paths) - set(self.base["typed_paths"].get(f, [])))
               for f, paths in self.now["typed_paths"].items()}
        new = {f: p for f, p in new.items() if p}
        self.assertEqual(new, {}, "\nA script names a data file itself. Ask the helper instead:\n"
                                  "    from data_paths import path, new_file\n" + json.dumps(new, indent=2))

    def test_baseline_only_shrinks(self):
        gone = {f: sorted(set(paths) - set(self.now["typed_paths"].get(f, [])))
                for f, paths in self.base["typed_paths"].items()}
        gone = {f: p for f, p in gone.items() if p}
        self.assertEqual(gone, {}, "\nFixed — now take these out of test-fixtures/data-layout-baseline.json "
                                   "(python3 test_data_layout.py --write-baseline):\n" + json.dumps(gone, indent=2))

    def test_nothing_new_at_the_root(self):
        legacy = {today for _, _, today in dp.REGISTRY.values()} | set(self.base["names_in_code"])
        loose = sorted(n for n in os.listdir(OPENCLAW)
                       if re.search(r"\.jsonl?$", n) and n not in legacy and not RUNTIME.match(n))
        self.assertEqual(loose, [], "\nNew loose file(s) at the root of ~/.openclaw. A skill's file belongs in "
                                    "Skills/<skill>/data|state|config/ — create it with data_paths.new_file().")

    def test_shared_holds_code_only(self):
        extra = sorted(set(self.now["shared_data"]) - set(self.base["shared_data"]))
        self.assertEqual(extra, [], "\nshared/ holds code only. Give this data an owner skill: data_paths.new_file().")

    def test_registry_is_sound(self):
        places = [today for _, _, today in dp.REGISTRY.values()]
        self.assertEqual(len(places), len(set(places)), "two registry names point at one file")
        for name, (owner, kind, today) in dp.REGISTRY.items():
            self.assertIn(kind, dp.KINDS, name)
            self.assertTrue((dp.SKILLS / owner).is_dir(), f"{name}: owner {owner!r} is not a skill folder")
            self.assertEqual(Path(today).name, name, f"{name}: registry key must be the file's own name")
        self.assertTrue(dp.MOVED <= set(dp.REGISTRY), "MOVED names a file that is not registered")

    def test_an_unmoved_name_always_resolves_to_its_old_place(self):
        # The safety property: a script on the helper and a script still typing the old path
        # must agree, even for a file that does not exist yet.
        for name, (_, _, today) in dp.REGISTRY.items():
            if name not in dp.MOVED:
                self.assertEqual(dp.path(name), OPENCLAW / today, name)
        for name in dp.MOVED:
            owner, kind, today = dp.REGISTRY[name]
            self.assertEqual(dp.path(name), dp.SKILLS / owner / kind / name)
            self.assertFalse((OPENCLAW / today).exists(), f"{name} is marked moved but the old file is still there")

    def test_new_file_refuses_a_bad_home(self):
        self.assertEqual(dp.new_file("hydration", "state", "x.json"), dp.SKILLS / "hydration" / "state" / "x.json")
        for skill, kind in (("shared", "data"), ("hydration", "logs"), ("no-such-skill", "data")):
            with self.assertRaises(ValueError):
                dp.new_file(skill, kind, "x.json")
        with self.assertRaises(KeyError):
            dp.path("never_registered.json")

    def test_ignored_stress_restores_are_not_scanned_as_live_code(self):
        self.assertFalse(any("stress" in f.parts for f in scripts()))


if __name__ == "__main__":
    if "--write-baseline" in sys.argv:
        BASELINE.write_text(json.dumps(snapshot(), indent=1) + "\n")
        print(f"baseline written: {BASELINE}")
        sys.exit(0)
    unittest.main()
