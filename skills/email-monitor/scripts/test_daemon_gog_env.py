#!/usr/bin/env python3
"""Every gog call the daemon makes must carry the keyring password.

2026-10-02 21:17 gog moved to a file keyring and started requiring
GOG_KEYRING_PASSWORD. The gateway gets it from openclaw.json env.vars; the email
daemon does NOT -- its launchd plist has no EnvironmentVariables block at all, so
`ps eww` on the running process shows no GOG_* variable of any kind. Only one of
the ten gog invocations in daemon.py passed `env=gog_env()`, so for four days the
other nine failed with "no TTY available for keyring file backend password prompt".

The quiet one was `_proposal_already_on_calendar`, the duplicate-event guard: a
non-zero exit is `continue`d without logging, so a failed lookup is indistinguishable
from "no duplicate found" and every proposal looked new. On 2026-10-06 that put a
second "Reservation detected" card in front of the user for a table the agent had just
booked itself.

This is a source check, not a behaviour test: the failure mode is a missing keyword
argument, and it is invisible at runtime precisely because the error is swallowed.

    python3 test_daemon_gog_env.py
"""
import re
import unittest
from pathlib import Path

DAEMON = Path(__file__).resolve().parent / "daemon.py"
GOG = "/opt/homebrew/bin/gog"
# A bare "gog" relies on PATH. The daemon's launchd PATH is /usr/bin:/bin:/usr/sbin:/sbin,
# which does not contain /opt/homebrew/bin, so it can never resolve.
BARE_GOG = re.compile(r'"gog"')


def gog_subprocess_calls():
    """(line number, owning function, passes_env) for every subprocess.run that ends
    up running gog -- whether the binary is named inline or built into a `cmd` list
    just above the call."""
    lines = DAEMON.read_text().splitlines()
    funcs = [(i, l.split("(")[0][4:].strip()) for i, l in enumerate(lines)
             if re.match(r"^def \w", l)]

    def owner(n):
        name = "<module>"
        for i, f in funcs:
            if i >= n:
                break
            name = f
        return name

    found = []
    for i, line in enumerate(lines):
        if "subprocess.run(" not in line:
            continue
        depth, j = 0, i
        while j < len(lines):
            depth += lines[j].count("(") - lines[j].count(")")
            if depth <= 0 and j > i:
                break
            j += 1
        span = "\n".join(lines[i:j + 1])
        preceding = "\n".join(lines[max(0, i - 30):i])
        inline = GOG in span or BARE_GOG.search(span)
        via_cmd = re.search(r"subprocess\.run\(\s*cmd\b", line) and (
            GOG in preceding or BARE_GOG.search(preceding))
        if inline or via_cmd:
            found.append((i + 1, owner(i), "env=gog_env()" in span,
                          GOG in span or GOG in preceding))
    return found


class GogEnvironment(unittest.TestCase):
    def test_every_gog_call_passes_the_keyring_env(self):
        calls = gog_subprocess_calls()
        self.assertGreaterEqual(len(calls), 10, "the scanner stopped finding gog calls")
        missing = [f"line {ln} in {fn}()" for ln, fn, ok, _ in calls if not ok]
        self.assertEqual(missing, [], "gog calls without env=gog_env() will fail under "
                                      "launchd, silently: " + "; ".join(missing))

    def test_every_gog_call_uses_the_absolute_path(self):
        """The daemon's launchd PATH has no /opt/homebrew/bin, so a bare "gog" raises
        FileNotFoundError however good the environment is (2026-10-06: the UV sun-block
        confirm did exactly this)."""
        calls = gog_subprocess_calls()
        relative = [f"line {ln} in {fn}()" for ln, fn, _, absolute in calls if not absolute]
        self.assertEqual(relative, [], "bare \"gog\" is unresolvable under launchd: "
                                       + "; ".join(relative))

    def test_the_duplicate_guard_is_covered(self):
        """Named explicitly because its failure is silent: a non-zero gog exit is
        treated as 'no duplicate on the calendar'."""
        guard = [ok for ln, fn, ok, _ in gog_subprocess_calls()
                 if fn == "_proposal_already_on_calendar"]
        self.assertTrue(guard and all(guard),
                        "_proposal_already_on_calendar must be able to read the calendar")

    def test_gog_env_actually_supplies_the_password(self):
        import sys
        sys.path.insert(0, str(DAEMON.parent.parent.parent / "shared"))
        from fetch_calendar import gog_env
        self.assertIn("GOG_KEYRING_PASSWORD", gog_env(),
                      "gog_env() is the only thing standing between the daemon and a "
                      "keyring prompt it can never answer")


if __name__ == "__main__":
    unittest.main(verbosity=2)
