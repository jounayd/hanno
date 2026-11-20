"""Step 5 (implicit) + Step 6: collision classification (General-spec.txt:543-560)."""

from __future__ import annotations

import numpy as np


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def classify(
    best_similarity: float, near_duplicate_threshold: float, exact_duplicate_threshold: float
) -> str:
    if best_similarity >= exact_duplicate_threshold:
        return "EXACT_DUPLICATE"
    if best_similarity >= near_duplicate_threshold:
        return "NEAR_DUPLICATE"
    return "NOVEL"
