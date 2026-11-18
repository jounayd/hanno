"""Step 1 (preprocessing) + Step 2 (semantic embedding) of the Fingerprint
Engine pipeline (General-spec.txt:508-522).

The embedding model is fixed for the lifetime of the MVP — changing it
invalidates all stored fingerprints and requires a deliberate re-fingerprint
migration of the whole ledger. Loading the model is expensive (seconds), so
it is cached per-process as a module-level singleton.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

import re

_MODEL_CACHE: dict[str, SentenceTransformer] = {}  # noqa: F821 (forward ref)

_MARKDOWN_RE = re.compile(r"[*_`#>\[\]()]")
_WHITESPACE_RE = re.compile(r"\s+")


def preprocess(text: str) -> str:
    """Strip markdown formatting, normalize whitespace. Symbols are
    preserved deliberately (General-spec.txt:511) since mathematical
    notation carries semantic content."""
    stripped = _MARKDOWN_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def _get_model(model_name: str) -> SentenceTransformer:
    if model_name not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer

        _MODEL_CACHE[model_name] = SentenceTransformer(model_name)
    return _MODEL_CACHE[model_name]


def embed(text: str, model_name: str) -> list[float]:
    """Returns the full-dimensional semantic embedding vector for `text`."""
    model = _get_model(model_name)
    cleaned = preprocess(text)
    vector = model.encode(cleaned, normalize_embeddings=True)
    return vector.tolist()
