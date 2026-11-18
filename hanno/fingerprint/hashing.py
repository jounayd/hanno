"""Step 4: cryptographic hashing (General-spec.txt:536-539)."""

from __future__ import annotations

import hashlib
import json


def hash_vector(compressed_vector: list[float]) -> str:
    """Deterministic SHA-256 hex digest of the compressed vector."""
    rounded = [round(v, 8) for v in compressed_vector]
    serialized = json.dumps(rounded, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
