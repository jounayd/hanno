"""Step 3: dimensionality reduction (General-spec.txt:524-534).

Below `calibration_minimum_fingerprints` (default 500), use raw truncation
of the embedding vector. Once that threshold is crossed, fit a PCA model on
the distribution of all fingerprints seen so far and use it. Refit every
`recalibration_interval` (default 1000) new fingerprints thereafter.
"""
from __future__ import annotations

import json

import numpy as np

RAW_TRUNCATION_VERSION = "raw_truncation_v0"


def truncate(full_vector: list[float], target_dims: int) -> list[float]:
    if len(full_vector) >= target_dims:
        return full_vector[:target_dims]
    return full_vector + [0.0] * (target_dims - len(full_vector))


def fit_pca(full_vectors: list[list[float]], target_dims: int):
    from sklearn.decomposition import PCA
    matrix = np.array(full_vectors)
    n_components = min(target_dims, matrix.shape[0], matrix.shape[1])
    pca = PCA(n_components=n_components)
    pca.fit(matrix)
    return pca


def apply_pca(pca_mean: list[float], pca_components: list[list[float]], full_vector: list[float]) -> list[float]:
    vector = np.array(full_vector) - np.array(pca_mean)
    components = np.array(pca_components)
    return (components @ vector).tolist()


def serialize_pca(pca) -> tuple[str, str]:
    return json.dumps(pca.mean_.tolist()), json.dumps(pca.components_.tolist())


class Reducer:
    """Wraps the currently-active reduction strategy (raw truncation or a
    fitted PCA version), and decides when a refit is due."""

    def __init__(self, target_dims: int, calibration_minimum: int, recalibration_interval: int):
        self.target_dims = target_dims
        self.calibration_minimum = calibration_minimum
        self.recalibration_interval = recalibration_interval

    def should_refit(self, total_fingerprints: int, last_fitted_on_count: int | None) -> bool:
        if last_fitted_on_count is None:
            return total_fingerprints >= self.calibration_minimum
        return total_fingerprints - last_fitted_on_count >= self.recalibration_interval

    def reduce(self, full_vector: list[float], active_reducer_row: dict | None) -> tuple[list[float], str]:
        """Returns (compressed_vector, compression_model_version)."""
        if active_reducer_row is None:
            return truncate(full_vector, self.target_dims), RAW_TRUNCATION_VERSION
        mean = json.loads(active_reducer_row["mean_json"])
        components = json.loads(active_reducer_row["components_json"])
        compressed = apply_pca(mean, components, full_vector)
        return compressed, f"pca_v{active_reducer_row['version']}"
