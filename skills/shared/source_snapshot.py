"""Scoped, cross-process read reuse. No source access until the caller's fetch runs."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = Path(os.environ.get('OPENCLAW_HOME', str(Path.home() / '.openclaw'))) / 'workspace/state/source_snapshots.sqlite'


class SourceUnavailable(RuntimeError):
    pass


def require_complete(result):
    if result.get('status') != 'complete':
        raise SourceUnavailable('Source is partial or unavailable; cannot conclude empty or zero')
    return result['value']


class SnapshotStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)

    def read(self, resource, scope, fetch, *, max_age=60, force=False, version=1):
        try:
            return self._read(resource, scope, fetch, max_age=max_age, force=force, version=version)
        except (OSError, sqlite3.Error) as exc:
            # Cache health is not source health: retain a fresh read if storage fails.
            try:
                result = fetch()
                if result.get('status') not in ('complete','partial','unavailable'):
                    raise ValueError('Missing source health')
            except Exception as source_exc:
                result = {'status':'unavailable','value':None,'error':type(source_exc).__name__}
            now=time.time()
            return {**result,'resource':resource,'scope':scope,'source_version':version,
                    'observed_at':now,'expires_at':now,'cached':False,'cache_error':type(exc).__name__}

    def _read(self, resource, scope, fetch, *, max_age=60, force=False, version=1):
        if not 0 <= max_age <= 3600:
            raise ValueError('max_age must be between zero and one hour')
        key = hashlib.sha256(json.dumps([resource, scope, version], sort_keys=True).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        locks = self.path.parent / (self.path.name + '.locks')
        locks.mkdir(exist_ok=True)
        # Per-key process lock: different source reads can proceed concurrently.
        with (locks / key).open('a') as lock:
            os.chmod(locks / key, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            db = sqlite3.connect(str(self.path), timeout=20)
            try:
                os.chmod(self.path, 0o600)
                db.execute('CREATE TABLE IF NOT EXISTS snapshots (key TEXT PRIMARY KEY, observed REAL NOT NULL, expires REAL NOT NULL, result TEXT NOT NULL)')
                old = db.execute('SELECT observed,expires,result FROM snapshots WHERE key=?', (key,)).fetchone()
                now = time.time()
                if old and not force and max_age > 0 and 0 <= now-old[0] < max_age and now < old[1]:
                    return {**json.loads(old[2]), 'cached': True}
                try:
                    result = fetch()
                    if result.get('status') not in ('complete', 'partial', 'unavailable'):
                        raise ValueError('Source health is required')
                except Exception as exc:
                    result = {'status': 'unavailable', 'value': None, 'error': type(exc).__name__}
                observed = time.time()
                result = {**result, 'resource': resource, 'scope': scope, 'source_version': version,
                          'observed_at': observed, 'expires_at': observed + max_age, 'cached': False}
                # A failed refresh invalidates the prior success instead of hiding the outage.
                if result['status'] == 'complete':
                    db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)',
                               (key, observed, observed + max_age, json.dumps(result)))
                else:
                    db.execute('DELETE FROM snapshots WHERE key=?', (key,))
                # Bound historical windows without ever touching canonical source records.
                db.execute('DELETE FROM snapshots WHERE expires<?', (observed - 86400,))
                db.commit()
                return result
            finally:
                db.close()
