"""Bounded classification reuse; never routes approval or execution decisions."""
from __future__ import annotations
import hashlib
import json
from source_snapshot import SnapshotStore, SourceUnavailable, require_complete


def classify_cached(operation, model, system, inputs, classify, validate, *, store=None, max_chars=60000):
    """Oversized or invalid inputs require review, never silent truncation/skip.

    Only validated classifications are reusable. The full prompt and model are
    part of the identity; raw prompt text is not stored in the cache key.
    """
    encoded = json.dumps([operation, model, system, inputs], sort_keys=True)
    if len(encoded) > max_chars:
        raise SourceUnavailable('Classification needs review: input exceeds bounded context')
    scope = {'operation': operation, 'model': model,
             'prompt_sha256': hashlib.sha256(encoded.encode()).hexdigest()}
    def fetch():
        value = classify()
        if not validate(value):
            raise ValueError('Classification requires review: invalid model output')
        return {'status': 'complete', 'value': value}
    result = (store or SnapshotStore()).read('bounded-classification', scope, fetch, max_age=600)
    return require_complete(result)
