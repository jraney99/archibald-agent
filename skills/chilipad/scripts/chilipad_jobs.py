#!/usr/bin/env python3
"""
What the cron store actually says about this skill's three jobs.

WHY this exists at all (2026-10-06, borrowed from shared/sync_bedroom_sunrise.py's
`disabled_jobs`): `openclaw cron edit` succeeds perfectly well on a *disabled*
job. The user's bedroom sunrise missed two mornings while the nightly sync printed
{"status": "ok"} three nights running, because the edit's exit code says nothing
about whether tomorrow's run will happen. So the enable state is checked
separately, by name, every night.

It deliberately does NOT re-enable anything. A job switched off by hand was
switched off on purpose; turning the bed back on by itself is the proactive
behaviour the user has ruled out. Being loud about it is the job.

A local copy rather than an import of sync_bedroom_sunrise.disabled_jobs: that
module reads the Hatch at import time and pulls in the whole MQTT stack
(hatch_rest_api, awscrt), which is far too much to drag in behind a `status.py`
that only wants to know whether a cron is on.
"""

from __future__ import annotations

import json
import subprocess


def job_states(job_ids) -> dict | None:
    """{job_id: {"name", "enabled", "schedule"}} for the ids that exist.

    Returns None — never {} — when the cron list cannot be read or parsed. The two
    have to be told apart: an empty dict legitimately means "every one of these
    jobs has been deleted", and reporting that as "could not read the list" would
    hide a deleted job behind a transport excuse.
    """
    wanted = {jid for jid in job_ids if jid}
    if not wanted:
        return {}
    probe = subprocess.run(["openclaw", "cron", "list", "--all", "--json"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        return None
    try:
        jobs = json.loads(probe.stdout).get("jobs") or []
    except (ValueError, AttributeError):
        return None
    out = {}
    for job in jobs:
        jid = job.get("id")
        if jid in wanted:
            out[jid] = {
                "name": job.get("name") or jid,
                "enabled": bool(job.get("enabled")),
                "schedule": job.get("schedule") or job.get("cron") or "?",
            }
    return out


def will_not_run(job_ids) -> list[str]:
    """Plain-English reasons tonight might not happen. [] when every job is live.

    A job missing from `--all` is reported by name, so a deleted job is never
    read as healthy.
    """
    wanted = [jid for jid in job_ids if jid]
    if not wanted:
        return ["no cron jobs are configured yet — see SKILL.md's Setup section"]
    states = job_states(wanted)
    if states is None:
        return ["could not read the cron list to confirm the jobs are enabled"]
    reasons = []
    for jid in wanted:
        state = states.get(jid)
        if state is None:
            reasons.append(f"{jid} is not in the cron list at all")
        elif not state["enabled"]:
            reasons.append(f"{state['name']} is disabled")
    return reasons
