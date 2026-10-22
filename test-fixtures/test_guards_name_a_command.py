#!/usr/bin/env python3
"""A guard that blocks an action must hand back a command to RUN.

2026-10-13: The user asked his agent to tell a venue he'd be 15 minutes late.
email-send-guard blocked the raw send — correctly — and then told it to "build
the gmail_pending entry (draft + to_email + thread_id + rfc_msg_id ...), show the
draft, wait for a literal ✅". That is an assembly job, not a command. The agent
spent 23 turns reverse-engineering it, re-reading ~75k of context each turn, and
one short note cost $1.01 — a third of that day's entire model spend.

Every other guard already did the right thing:
    calendar-write  "re-run this same command with --propose"
    trading         "propose_trade.py"
    memory-health   "confirm_meal.py / log_food.py / log_supplement.py"

So the rule is not "name an alternative" — all of them did. It is "name one
thing to run". The baseline may only shrink.

    python3 test_guards_name_a_command.py
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
BASELINE = Path(__file__).resolve().parent / "guard-recipe-baseline.json"

# A guard "blocks" when it hands the agent a refusal string to act on.
BLOCKY = re.compile(r"\b(BANNED|must be proposed|is blocked|never use|do not hand-build)\b", re.I)
# A runnable thing: a script with its flags, or an explicit "re-run with --flag".
COMMAND = re.compile(r"[\w/]+\.py\s|--\w[\w-]*\b")
# NOT phrase-detection. A first cut tried to spot "assemble this yourself"
# wording and flagged calendar-write-guard for saying "do NOT hand-build" and
# flagged this file's own rationale for the word "assemble". Negation and
# commentary are indistinguishable from instruction at that level, and a flaky
# contract is worse than none. What IS checkable: the refusal must name a script
# that actually exists, so the agent has somewhere real to go.
SCRIPT = re.compile(r"([\w./-]+\.py)")


def guard_texts() -> dict:
    """{plugin: [long strings it can hand back]}"""
    out = {}
    for mjs in sorted(PLUGINS.glob("*/index.mjs")):
        src = mjs.read_text(errors="ignore")
        # JS builds these refusals by concatenating fragments across lines, so a
        # per-literal view sees only "is BANNED (" and misses the guidance that
        # follows. Take every literal in a file that blocks anything.
        strings = [m.group(1) for m in
                   re.finditer(r"[\"'`]([^\"'`]{20,900})[\"'`]", src)]
        if any(BLOCKY.search(x) for x in strings):
            out[mjs.parent.name] = strings
    return out


class GuardsHandBackACommand(unittest.TestCase):
    def setUp(self):
        self.guards = guard_texts()
        try:
            raw = json.loads(BASELINE.read_text())
        except (OSError, ValueError):
            raw = {}
        self.base = {k: v for k, v in raw.items() if not k.startswith("_")}

    def offenders(self) -> dict:
        """{guard: why} for guards whose refusal points nowhere real."""
        bad = {}
        for name, strings in self.guards.items():
            joined = " ".join(strings)
            named = [m for m in SCRIPT.findall(joined)]
            if not named:
                bad[name] = "names no script to run"
                continue
            missing = [n for n in named
                       if not list(ROOT.rglob(Path(n).name)) ]
            if missing and not COMMAND.search(joined):
                bad[name] = f"names only scripts that do not exist: {missing[:3]}"
        return bad

    def test_some_guards_were_found(self):
        self.assertTrue(self.guards, "no blocking guards detected — the check proves nothing")

    def test_the_email_guard_names_propose_email(self):
        """The exact regression: this is the one that cost $1.01."""
        joined = " ".join(self.guards.get("email-send-guard", []))
        self.assertIn("propose_email.py", joined,
                      "email-send-guard no longer tells the agent what to run")
        self.assertNotIn("build the gmail_pending", joined,
                         "the hand-assembly recipe is back")

    def test_every_blocking_guard_points_somewhere_real(self):
        new = sorted(set(self.offenders()) - set(self.base))
        self.assertEqual(new, [], "\n".join([
            "",
            "These guards block an action without naming a script the agent can",
            "actually run. That is what turns one blocked action into dozens of",
            "exploratory turns (2026-10-13, $1.01 for one email):",
            *(f"  {n}: {self.offenders()[n]}" for n in new),
        ]))

    def test_the_baseline_only_shrinks(self):
        stale = sorted(set(self.base) - set(self.offenders()))
        self.assertEqual(stale, [], f"baselined guards that are now fine — remove them: {stale}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
