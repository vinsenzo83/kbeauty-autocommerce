from __future__ import annotations

import hashlib


def compute_event_hash(raw_body: bytes, source: str, event_type: str) -> str:
    """Return a deterministic SHA-256 hex digest for dedup."""
    h = hashlib.sha256()
    h.update(source.encode())
    h.update(b":")
    h.update(event_type.encode())
    h.update(b":")
    h.update(raw_body)
    return h.hexdigest()
