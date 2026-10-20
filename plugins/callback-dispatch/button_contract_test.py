#!/usr/bin/env python3
"""Every button any script can send must reach a handler. Fails the suite otherwise.

WHY (2026-09-19): all 8 buttons under the wardrobe outfit board were dead from
launch. The dispatch table had entries for them, the scripts existed, every unit
test passed — but the plugin's parser only accepted `confirm:`/`skip:` and choked
on the `<media:image>` line that precedes a tap on a button under a photo. Nobody
noticed until a tap fell through to the agent and derailed the next conversation.
Nothing compared what senders SEND with what the plugin ACCEPTS. This does.

It scans every sender under workspace/Skills for `callback_data` templates, expands
them into real tokens, and pushes each through the plugin's production parser and
table (token-cli.mjs) — wrapped exactly as Telegram delivers it, including the
media line for scripts that attach buttons to a photo or document.

Adding a button? If this fails, add its dispatch-table entry (and the mirror row in
workspace/Documentation/rules/05-…). If the values are an enumeration the generic
sampler cannot guess, add it to ENUMERATIONS.
"""
import ast
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# OPENCLAW_DIR lets a scratch copy of the plugin (mutation checks) still scan the real senders.
OPENCLAW = Path(os.environ.get("OPENCLAW_DIR") or HERE.parent.parent)
SKILLS = OPENCLAW / "workspace" / "Skills"
TABLE = json.loads((HERE / "dispatch-table.json").read_text())

SKIP_PARTS = ("node_modules", "/tests/", "/test_", ".test.", "run_eval", "__pycache__", "/references/")
LITERAL = re.compile(r"""callback_data["']?\s*[:=]\s*f?(["'])((?:(?!\1).)*)\1""")
PLACEHOLDER = re.compile(r"\{[^}]*\}")
MEDIA_SENDERS = re.compile(r"sendPhoto|sendDocument|sendMediaGroup|sendVideo")

# Shapes real ids take. A template passes if SOME combination reaches a handler.
SAMPLES = ["4b7e3309d908", "0123456789abcdef01234567", "0123456789abcdef0123456789abcdef", "0000000000000001", "2026-09-19", "2026-09-19-2",
           "32", "3", "1", "abc", "morning", "evening", "oth", "done", "other", "skip"]

# Templates whose placeholder is an ENUMERATION: every value must reach a handler,
# not just one. (file, constant, how to turn the constant into the enumerated values)
flat = lambda rows: [s for row in rows for s in row]
ENUMERATIONS = {
    "confirm:meal-{today}-{slot}-{choice}": ("nutrition/scripts/send_meal_reminder.py", "SLOT_CHOICE_BUTTONS",
                                             lambda c: [f"confirm:meal-2026-09-19-{slot}-{choice}" for slot, row in c.items() for _, choice in row]),
    "confirm:supp-{due_at.date().isoformat()}-between-{slug(s)}": (
        "nutrition/scripts/send_supplement_nudge.py", "BETWEEN_SLUGS",
        lambda c: [f"confirm:supp-2026-10-12-between-{name}" for name in c]),
    "confirm:txnctx-{txn_id}-{s}": ("shared/txn_context.py", "CONTEXTS",
                                    lambda c: [f"confirm:txnctx-0000000000000001-{s}" for s in c]),
    "confirm:txncat-{entry['id']}-{s}": ("afrexai-personal-finance/scripts/finance_txn_watch.py", "BUTTON_ROWS",
                                         lambda c: [f"confirm:txncat-0000000000000001-{s}" for s in flat(c)]),
    "confirm:inflow-{row_id}-{letter}{n}": ("afrexai-personal-finance/scripts/finance_deposit_question.py", "STAGE_LETTER",
                                            lambda c: [f"confirm:inflow-0000000000000001-{letter}0" for letter in c.values()]),
    "confirm:p2pcat-{entry['id']}-{s}": ("afrexai-personal-finance/scripts/finance_p2p_watch.py", ("IN_BUTTON_ROWS", "OUT_BUTTON_ROWS"),
                                         lambda c: [f"confirm:p2pcat-0000000000000001-{s}" for s in flat(c)]),
    "confirm:transfercat-{entry['id']}-{s}": ("afrexai-personal-finance/scripts/finance_transfer_watch.py", ("IN_BUTTON_ROWS", "OUT_BUTTON_ROWS"),
                                              lambda c: [f"confirm:transfercat-0000000000000001-{s}" for s in flat(c)]),
}

# `confirm:{action_type}` is filled at call sites; each real action type must be handled.
DYNAMIC = {"confirm:{action_type}", "skip:{action_type}"}
ACTION_TYPE_CALL = re.compile(r"""(?:--action-type["',\s=]+|_tg_send_with_buttons\([^)]*?,\s*["'])([a-z][a-z0-9_-]*)""")
# Dormant on purpose — say why, or delete the code.
DORMANT_ACTION_TYPES = {
    "uv": "email daemon still has the UV-window proposal path, but nothing in the repo writes pending_uv_event.json any more, so it can never be sent",
}
# An approval id is appended to these before sending (daemon._tg_send_with_buttons).
APPROVAL_SUFFIXED = {"email": "0123456789abcdef0123456789abcdef", "cal": "0123456789abcdef0123456789abcdef"}


def constant(path, names):
    names = (names,) if isinstance(names, str) else names
    found = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(t, "id", None) in names for t in targets):
                value = ast.literal_eval(node.value)
                found = value if isinstance(value, dict) else found + list(value)
    return found


def senders():
    for path in sorted(SKILLS.rglob("*.py")):
        if any(part in str(path) for part in SKIP_PARTS):
            continue
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if "callback_data" in text or "action-type" in text or "_tg_send_with_buttons" in text:
            yield path, text


def parse(raws):
    done = subprocess.run(["node", str(HERE / "token-cli.mjs")], input=json.dumps(raws), capture_output=True, text=True, timeout=60)
    if done.returncode:
        sys.exit(f"token-cli failed: {done.stderr[:400]}")
    return json.loads(done.stdout)


def main():
    checks = []          # (description, [raw texts]); passes if ANY raw is handled ...
    must_all = []        # ... or, for enumerations, if EVERY raw is handled
    action_types = set()
    seen_templates = set()
    for path, text in senders():
        rel = str(path.relative_to(SKILLS))
        wrap = (lambda t: f"<media:image> [file_id:AgACAgEAAxkDAAIw22qua26B]\ncallback_data: {t}") if MEDIA_SENDERS.search(text) \
            else (lambda t: f"callback_data: {t}")
        action_types.update(ACTION_TYPE_CALL.findall(text))
        for n, line in enumerate(text.splitlines(), 1):
            for m in LITERAL.finditer(line):
                template = m.group(2)
                if ":" not in template or template in DYNAMIC:
                    continue
                seen_templates.add(template)
                where = f"{rel}:{n}  {template}"
                if template in ENUMERATIONS:
                    file, names, expand = ENUMERATIONS[template]
                    must_all.append((where, [wrap(t) for t in expand(constant(SKILLS / file, names))]))
                    continue
                slots = len(PLACEHOLDER.findall(template))
                tokens = [template] if not slots else [
                    _fill(template, combo) for combo in itertools.product(SAMPLES, repeat=min(slots, 3))]
                checks.append((where, [wrap(t) for t in tokens]))
    for kind in sorted(action_types - set(DORMANT_ACTION_TYPES) - {"required"}):
        suffix = f"-{APPROVAL_SUFFIXED[kind]}" if kind in APPROVAL_SUFFIXED else ""
        for verb in ("confirm", "skip"):
            must_all.append((f"action type '{kind}'  {verb}:{kind}{suffix}", [f"callback_data: {verb}:{kind}{suffix}"]))

    failures = []
    flat_raws = [raw for _, raws in checks + must_all for raw in raws]
    results = iter(parse(flat_raws))
    for where, raws in checks:
        got = [next(results) for _ in raws]
        if not any(r["handled"] for r in got):
            failures.append(f"DEAD BUTTON  {where}\n             no sampled value reaches a handler"
                            + ("" if any(r["token"] for r in got) else " — the parser does not even recognise it as a tap"))
    for where, raws in must_all:
        got = [next(results) for _ in raws]
        dead = [raw.splitlines()[-1].replace("callback_data: ", "") for raw, r in zip(raws, got) if not r["handled"]]
        if dead:
            failures.append(f"DEAD BUTTON  {where}\n             unhandled value(s): {', '.join(dead[:8])}")

    failures.extend(state_links())

    stale = [t for t in ENUMERATIONS if t not in seen_templates]
    if stale:
        failures.append("ENUMERATIONS lists template(s) no script sends any more: " + "; ".join(stale))
    entries = list(TABLE["exact"].values()) + TABLE["patterns"]
    missing = sorted({e["argv"][0] for e in entries if e.get("argv") and not (SKILLS / e["argv"][0]).exists()})
    if missing:
        failures.append("dispatch-table.json points at script(s) that do not exist: " + ", ".join(missing))

    if failures:
        print("\n".join(failures))
        sys.exit(f"\nbutton contract: {len(failures)} problem(s). See the docstring for how to fix.")
    print(f"button contract: {len(checks) + len(must_all)} button templates from {len(list(senders()))} sender files "
          f"all reach a handler ({len(flat_raws)} tokens through the real parser; {len(entries)} table entries, no missing scripts)")


# 2026-10-03: a button that reaches a handler can still edit the WRONG message. A
# daily check-in button moved onto the meal prompt on 2026-10-01, but its handler kept
# editing the retired standalone reminder's message id from that reminder's own state
# file — the entry was logged, the tapped message never changed, the user tapped again.
# So: a handler that finds its message through a state file must share that file with
# the sender that emits the button, unless it looks the message up by an id carried in
# the token itself (proposal ids, transaction ids), which cannot go stale that way.
STATE_FILE_RE = re.compile(
    r"""(?:data_path|path|new_file)\(\s*(?:["'][a-z-]+["']\s*,\s*["'][a-z]+["']\s*,\s*)?["']([A-Za-z0-9_.\-]+\.jsonl?)["']"""
    r"""|OPENCLAW\s*/\s*["']([A-Za-z0-9_.\-]+\.jsonl?)["']|~/\.openclaw/([A-Za-z0-9_.\-/]+\.jsonl?)""")
BY_ID_RE = re.compile(r"--id\b|proposal_id|find_proposal|--token\b|--reply-id|args\.id\b|--entry\b|item_id|--qid\b|--pid\b")


def _state_files(text):
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return {Path(next(g for g in m.groups() if g)).name for m in STATE_FILE_RE.finditer(code)}, code


def _resolve(token):
    entry = TABLE["exact"].get(token)
    if entry:
        return entry
    return next((p for p in TABLE["patterns"] if re.match(p["re"], token)), None)


def state_links():
    """Every (sender, button) -> handler pair where the handler edits through a state
    file the sender never writes. Empty when the contract holds."""
    problems = []
    handlers = {}
    for path, text in senders():
        rel = str(path.relative_to(SKILLS))
        written, _ = _state_files(text)
        for template in dict.fromkeys(m.group(2) for m in LITERAL.finditer(text)):
            if ":" not in template or template in DYNAMIC:
                continue
            if template in ENUMERATIONS:
                file, names, expand = ENUMERATIONS[template]
                tokens = expand(constant(SKILLS / file, names))
            else:
                slots = len(PLACEHOLDER.findall(template))
                tokens = [template] if not slots else [
                    _fill(template, combo) for combo in itertools.product(SAMPLES, repeat=min(slots, 3))]
            entry = next((e for e in map(_resolve, tokens) if e), None)
            if not entry or not entry.get("argv"):
                continue
            script = entry["argv"][0]
            if script not in handlers:
                target = SKILLS / script
                read, code = _state_files(target.read_text()) if target.exists() else (set(), "")
                handlers[script] = (read, bool(BY_ID_RE.search(code)))
            read, by_id = handlers[script]
            if read and not by_id and not (read & written):
                problems.append(f"STALE STATE  {rel}  {template}\n             handler {script} finds its message in "
                                f"{', '.join(sorted(read))}, which this sender never writes — it will edit the wrong "
                                f"message or fall back to a second one")
    return problems


def _fill(template, combo):
    for value in combo:
        template = PLACEHOLDER.sub(value, template, count=1)
    return template


if __name__ == "__main__":
    main()
