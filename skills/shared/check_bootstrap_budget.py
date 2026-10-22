#!/usr/bin/env python3
"""
Weekly guardrail for the 2026-09-13 bootstrap-truncation incident: SOUL.md
grew in one commit and silently pushed several always-injected files
(IDENTITY.md included - the "Master Wayne" rule) past
agents.defaults.bootstrapTotalMaxChars, and nothing surfaced it for two
weeks. `openclaw doctor --lint` already detects this
(checkId "core/doctor/bootstrap-size") - this script just runs it on a
schedule and Telegrams the user only when it finds something, instead of relying
on someone remembering to run doctor by hand.
"""
import json
import subprocess
import sys
from pathlib import Path

OPENCLAW = "/opt/homebrew/bin/openclaw"
SHARED = Path(__file__).parent
TELEGRAM_SCRIPT = SHARED / "send_telegram.py"


def notify(text: str) -> None:
    subprocess.run(["python3", str(TELEGRAM_SCRIPT), "--text", text],
                   capture_output=True, text=True, timeout=20)


def main():
    r = subprocess.run([OPENCLAW, "doctor", "--lint", "--json"],
                        capture_output=True, text=True, timeout=60)
    # doctor interleaves "[plugins] ..."/"[openclaw] ..." log lines with the
    # JSON array on stdout - strip those before parsing.
    json_text = "\n".join(
        line for line in r.stdout.splitlines()
        if not line.startswith("[plugins]") and not line.startswith("[openclaw]")
    ).strip()
    report = json.loads(json_text) if json_text else {}
    findings = report.get("findings", []) if isinstance(report, dict) else report

    # Includes "info" (near limit - the early warning this check exists for)
    # as well as "warning" (already being truncated).
    bootstrap_findings = [f for f in findings if f.get("checkId") == "core/doctor/bootstrap-size"]
    if not bootstrap_findings:
        print(json.dumps({"status": "clean"}))
        return

    lines = [f.get("message", "") for f in bootstrap_findings]
    # SEVERITY DECIDES THE WORDING (2026-10-11). Both severities were reported with
    # one sentence - "found files being truncated out of every turn's context again"
    # - so an `info` finding, which means NOTHING is truncated yet, announced that
    # the 2026-09-13 incident had recurred. The user got that alert while the real state
    # was 65 characters of headroom and no loss at all. An alert that overstates is
    # an alert that gets ignored, which is the one thing this check cannot afford.
    # Only an explicit "info" is the mild case. Anything else - "warning", or a
    # finding with no severity at all - gets the loud wording: understating a real
    # loss is far worse than overstating, and a runtime-contract test has
    # asserted since before this split that a severity-less finding whose message
    # says "truncated" must still read as truncation.
    truncating = any(f.get("severity") != "info" for f in bootstrap_findings)
    if truncating:
        headline = ("⚠️ Master Wayne — files are being truncated out of every turn's "
                    "context (same class of bug as 2026-09-13, which is why "
                    "\"Master Wayne\" silently stopped once):")
    else:
        headline = ("Master Wayne — the bootstrap context is close to its limit. "
                    "Nothing is being dropped yet; this is the early warning:")
    text = (
        headline + "\n"
        + "\n".join(f"- {line}" for line in lines)
        + "\n\nEither trim the flagged file(s) or raise "
        "agents.defaults.bootstrapMaxChars/bootstrapTotalMaxChars, then "
        "restart the gateway."
    )
    notify(text)
    # "truncation_found" is the pre-existing machine-readable value and stays as it
    # was; only the new mild case needed a name of its own.
    print(json.dumps({"status": "truncation_found" if truncating else "near_limit",
                      "count": len(bootstrap_findings),
                      "severities": sorted({f.get("severity") for f in bootstrap_findings})}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        notify(f"⚠️ Master Wayne — the weekly bootstrap-size check itself failed "
               f"({type(e).__name__}: {str(e)[:150]}). Run `openclaw doctor --lint` "
               "manually to check.")
        sys.exit(1)
