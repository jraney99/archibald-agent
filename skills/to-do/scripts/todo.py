"""Local commitments with provenance, completion evidence, and an audit trail."""
import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from data_paths import OPENCLAW, path
ROOT = OPENCLAW
DB_PATH = path("commitments.sqlite")

from output_mode import CONCERN, Report  # noqa: E402


@contextmanager
def db_open():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS commitments (id TEXT PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL, project TEXT NOT NULL, status TEXT NOT NULL, due TEXT, waiting_on TEXT, next_step TEXT, source TEXT NOT NULL, source_key TEXT UNIQUE, evidence TEXT, updated_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, commitment_id TEXT, at TEXT, change_json TEXT)")
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    columns = {r[1] for r in db.execute("PRAGMA table_info(commitments)")}
    if "notification_route" not in columns:
        db.execute("ALTER TABLE commitments ADD COLUMN notification_route TEXT NOT NULL DEFAULT 'existing'")
    if "details_json" not in columns:
        db.execute("ALTER TABLE commitments ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}'")
    db.commit()
    try:
        yield db
    finally:
        db.close()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def memory_base(title: str) -> str:
    """Stable MEMORY.md text with changing confirmation markers removed."""
    return title.split("_(confirmed", 1)[0].strip()


def memory_source_key(title: str) -> str:
    return "memory:" + hashlib.sha256(memory_base(title).encode()).hexdigest()[:16]


def add(title, source, project="personal", owner="The user", due=None, waiting_on=None, next_step=None, source_key=None, notification_route="existing", details=None):
    if not title.strip() or not source.strip():
        raise ValueError("A title and source are required")
    if due:
        date.fromisoformat(due)
    with db_open() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM commitments WHERE source_key=?", (source_key,)).fetchone() if source_key else None
        if existing:
            return existing["id"]
        cid = uuid.uuid4().hex[:12]
        db.execute("INSERT INTO commitments (id,title,owner,project,status,due,waiting_on,next_step,source,source_key,updated_at,notification_route,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (cid, title, owner, project,
                   "waiting" if waiting_on else "open", due, waiting_on, next_step, source, source_key, stamp(),
                   "manual" if project == "side-project" else notification_route, json.dumps(details or {}, sort_keys=True)))
        db.execute("INSERT INTO history(commitment_id,at,change_json) VALUES (?,?,?)", (cid, stamp(), json.dumps({"created": title, "source": source})))
        db.commit()
        return cid


def update(cid, status=None, evidence=None, due=None, next_step=None):
    if status in ("completed", "cancelled") and not evidence:
        raise ValueError("Completion/cancellation requires evidence or the user's explicit confirmation")
    if status and status not in ("open", "waiting", "completed", "cancelled"):
        raise ValueError("Unknown status")
    if due:
        date.fromisoformat(due)
    values = {k: v for k, v in {"status": status, "evidence": evidence, "due": due, "next_step": next_step}.items() if v is not None}
    with db_open() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT id FROM commitments WHERE id=?", (cid,)).fetchone():
            raise ValueError("Unknown commitment ID")
        values["updated_at"] = stamp()
        db.execute("UPDATE commitments SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", [*values.values(), cid])
        db.execute("INSERT INTO history(commitment_id,at,change_json) VALUES (?,?,?)", (cid, stamp(), json.dumps(values)))
        db.commit()


def get_by_source_key(source_key: str):
    with db_open() as db:
        row = db.execute(
            "SELECT * FROM commitments WHERE source_key=?", (source_key,)
        ).fetchone()
    return dict(row) if row else None


def ensure_memory_commitment(title: str) -> str:
    base = memory_base(title)
    return add(
        base,
        "MEMORY.md: Pending Actions",
        source_key=memory_source_key(base),
        next_step="Confirm the current status and the next action; do not infer completion.",
    )


def update_by_source_key(source_key: str, *, status: str, evidence: str) -> str:
    item = get_by_source_key(source_key)
    if not item:
        raise ValueError("Unknown commitment source key")
    update(item["id"], status=status, evidence=evidence)
    return item["id"]


def listing(project=None, include_closed=False, include_manual=False):
    with db_open() as db:
        clauses, params = [], []
        if not include_manual:
            clauses.append("notification_route != 'manual'")
        if not include_closed:
            clauses.append("status IN ('open','waiting')")
        if project:
            clauses.append("project=?")
            params.append(project)
        query = "SELECT * FROM commitments" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        return [dict(row) for row in db.execute(query + " ORDER BY due IS NULL,due,updated_at", params)]


def sync(root=ROOT):
    imported = []
    runtime_db = root / "state/openclaw.sqlite"
    with sqlite3.connect(f"file:{runtime_db}?mode=ro", uri=True) as db:
        jobs = db.execute("SELECT job_id,name,at FROM cron_jobs WHERE enabled=1 AND name LIKE 'reminder-%' AND schedule_kind='at'").fetchall()
    for job_id, name, due in jobs:
        title = name.removeprefix("reminder-")
        parts = title.rsplit("-", 1)
        title = (parts[0] if len(parts) == 2 and parts[1].isdigit() else title).replace("-", " ")
        imported.append(add(title, "Existing one-shot reminder: " + job_id, due=due[:10] if due else None,
                            source_key="cron:" + job_id, next_step="Complete the task, then record its outcome."))
    memory = (root / "workspace/MEMORY.md").read_text()
    section = memory.split("## Pending Actions", 1)[-1].split("\n## ", 1)[0] if "## Pending Actions" in memory else ""
    for line in section.splitlines():
        if not line.startswith("- "):
            continue
        title = line[2:].strip()
        if "STALE" in title:
            continue
        imported.append(ensure_memory_commitment(title))
    return imported


def status_report(project=None, today=None):
    """Output Mode — workspace/Documentation/rules/33-output-mode.md.

    Splits the tracker the way the rule asks: `waiting` is Archibald carrying it,
    `open` is the user's and appears only under NEEDS YOU. Nothing is inferred
    complete — a due date passing is reported as overdue, never as done."""
    today = today or date.today()
    items = listing(project)
    closed = [i for i in listing(project, include_closed=True)
              if i["status"] in ("completed", "cancelled")]
    open_items = [i for i in items if i["status"] == "open"]
    waiting = [i for i in items if i["status"] == "waiting"]
    overdue = {i["id"] for i in open_items if i["due"] and i["due"] < today.isoformat()}

    r = Report(
        "📌", "To-do",
        headline=f"{len(items)} open",
        tldr=("Nothing is being tracked right now." if not items else
              f"{len(open_items)} yours, {len(waiting)} with me"
              + (f", {len(overdue)} overdue." if overdue else ".")),
        coverage=f"As of {today.isoformat()}" + (f" · project {project}" if project else ""),
    )
    tracked = r.section("📋", "Tracked", headline=f"{len(items)} live")
    tracked.row("Yours to move", str(len(open_items)))
    tracked.row("Waiting on someone", str(len(waiting)))
    if overdue:
        tracked.row("Past due", str(len(overdue)))

    for item in waiting:
        r.handling(f"{item['title']} · waiting on {item['waiting_on'] or 'a reply'}", key=item["id"])
    for item in closed[-3:]:
        r.complete(f"{item['title']} · {item['status']}", key=item["id"])
    for item in open_items:
        when = f"due {item['due']}" if item["due"] else "no due date"
        flag = f"{CONCERN} " if item["id"] in overdue else ""
        step = f" · {item['next_step']}" if item["next_step"] else ""
        r.needs_you(f"{flag}{item['title']} · {when}{step}", key=item["id"])
    return r.render()


def brief(project=None):
    items = listing(project)
    if not items:
        return ""
    actionable = [item for item in items if item["status"] == "open"][:3]
    return "\n".join(f"- {item['title']}" + (f" (due {item['due']})" if item["due"] else "") for item in actionable)


def apply_side_project_request(source_key, request):
    """Idempotent owner endpoint for the side-project transactional outbox."""
    if not request.get('title'):
        raise ValueError('promise needs an action')
    if request.get('due'): date.fromisoformat(request['due'])
    status=request.get('status','open')
    if status not in ('open','waiting','completed','cancelled'): raise ValueError('invalid promise status')
    evidence=request.get('completion_evidence')
    if status in ('completed','cancelled') and not evidence:
        raise ValueError('completed/cancelled promise needs evidence')
    cid=add(request['title'],request.get('source','side-project CLI'),project='side-project',owner=request.get('owner','The user'),due=request.get('due'),waiting_on=request.get('waiting_on'),next_step=request.get('next_step'),source_key=source_key,notification_route='manual',details=request)
    values={'title':request['title'],'owner':request.get('owner','The user'),'due':request.get('due'),'waiting_on':request.get('waiting_on'),'next_step':request.get('next_step'),'status':status,'details_json':json.dumps(request,sort_keys=True),'evidence':evidence}
    with db_open() as db:
        db.execute('BEGIN IMMEDIATE')
        prior=dict(db.execute('SELECT * FROM commitments WHERE id=?',(cid,)).fetchone())
        changed={k:v for k,v in values.items() if prior.get(k)!=v}
        if changed:
            changed['updated_at']=stamp()
            db.execute('UPDATE commitments SET '+','.join(k+'=?' for k in changed)+' WHERE id=?', [*changed.values(),cid])
            db.execute('INSERT INTO history(commitment_id,at,change_json) VALUES(?,?,?)',(cid,stamp(),json.dumps({'before':prior,'after':changed},sort_keys=True)))
        db.commit()
    return cid


def move_side_project_to_review(source_key, evidence):
    """Retain audit history, exclude an owner-rejected inference from action views."""
    with db_open() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT * FROM commitments WHERE source_key=? AND project='side-project'",(source_key,)).fetchone()
        if not row: return False
        detail=json.loads(row['details_json'])
        if detail.get('review_only'): return False
        detail['review_only']=True
        db.execute("UPDATE commitments SET details_json=?,status='cancelled',evidence=?,updated_at=? WHERE id=?",(json.dumps(detail,sort_keys=True),evidence,stamp(),row['id']))
        db.execute('INSERT INTO history(commitment_id,at,change_json) VALUES(?,?,?)',(row['id'],stamp(),json.dumps({'moved_to_data_quality':dict(row),'reason':evidence},sort_keys=True)))
        db.commit()
        return True


def export_project(project="side-project"):
    with db_open() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM commitments WHERE project=? ORDER BY id", (project,))]
        ids = {r["id"] for r in rows}
        history = [dict(r) for r in db.execute("SELECT * FROM history ORDER BY id") if r["commitment_id"] in ids]
    return {"commitments": rows, "history": history}


def restore_project(payload):
    with db_open() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in payload["commitments"]:
            if row["project"] != "side-project" or row["notification_route"] != "manual":
                raise ValueError("restore accepts manual side-project commitments only")
            old = db.execute("SELECT * FROM commitments WHERE id=?", (row["id"],)).fetchone()
            if old and dict(old) != row:
                raise ValueError("commitment restore conflict")
            if not old:
                db.execute(f"INSERT INTO commitments ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        for row in payload["history"]:
            # History IDs belong to the shared owner, so preserve evidence while
            # allowing unrelated commitments already in that store.
            exists = db.execute("SELECT 1 FROM history WHERE commitment_id=? AND at=? AND change_json=?",
                                (row["commitment_id"], row["at"], row["change_json"])).fetchone()
            if not exists:
                if not db.execute("SELECT 1 FROM history WHERE id=?", (row["id"],)).fetchone():
                    db.execute("INSERT INTO history(id,commitment_id,at,change_json) VALUES(?,?,?,?)",
                               (row["id"], row["commitment_id"], row["at"], row["change_json"]))
                else:
                    db.execute("INSERT INTO history(commitment_id,at,change_json) VALUES(?,?,?)",
                               (row["commitment_id"], row["at"], row["change_json"]))
        db.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("add")
    p.add_argument("--title", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--project", default="personal")
    p.add_argument("--owner", default="The user")
    p.add_argument("--due")
    p.add_argument("--waiting-on")
    p.add_argument("--next-step")
    p = sub.add_parser("list")
    p.add_argument("--project")
    p.add_argument("--brief", action="store_true")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("--project")
    p = sub.add_parser("update")
    p.add_argument("id")
    p.add_argument("--status", choices=["open", "waiting", "completed", "cancelled"])
    p.add_argument("--evidence")
    p.add_argument("--due")
    p.add_argument("--next-step")
    sub.add_parser("sync")
    args = parser.parse_args()
    if args.command == "add":
        print(json.dumps({"id": add(args.title, args.source, args.project, args.owner, args.due, args.waiting_on, args.next_step)}))
    elif args.command == "update":
        update(args.id, args.status, args.evidence, args.due, args.next_step)
        print(json.dumps({"id": args.id, "updated": True}))
    elif args.command == "sync":
        print(json.dumps({"tracked_ids": sync()}))
    elif args.command == "status":
        print(status_report(args.project))
    elif args.brief:
        print(brief(args.project))
    else:
        print(json.dumps(listing(args.project, args.all), indent=2))
    if args.command in ("add", "update", "sync") and path("side_project.sqlite").exists():
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "side-project/scripts"))
        from trail import refresh
        print(json.dumps({"side_project_artifacts": refresh(path("side_project.sqlite"))}), file=sys.stderr)


if __name__ == "__main__":
    main()
