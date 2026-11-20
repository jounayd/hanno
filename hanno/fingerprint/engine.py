"""FingerprintEngine: orchestrates the full Module 3 pipeline
(General-spec.txt:505-560) for both seeds and candidates.
"""

from __future__ import annotations

from dataclasses import dataclass

from hanno.config import FingerprintConfig
from hanno.fingerprint import embedder, hashing, reducer, similarity
from hanno.ledger.repository import Ledger


@dataclass
class FingerprintResult:
    hash: str
    classification: str  # NOVEL | NEAR_DUPLICATE | EXACT_DUPLICATE
    matched_hash: str | None
    matched_entity_id: str | None
    similarity_score: float | None


class FingerprintEngine:
    def __init__(self, ledger: Ledger, config: FingerprintConfig) -> None:
        self._ledger = ledger
        self._config = config
        self._reducer = reducer.Reducer(
            target_dims=config.compressed_dimensions,
            calibration_minimum=config.calibration_minimum_fingerprints,
            recalibration_interval=config.recalibration_interval,
        )

    def _maybe_refit_reducer(self) -> None:
        total = self._ledger.count_fingerprints()
        active = self._ledger.get_active_reducer_version()
        last_fitted_on = active["fitted_on_count"] if active else None
        if not self._reducer.should_refit(total, last_fitted_on):
            return

        full_vectors = self._ledger.list_all_full_vectors()
        if len(full_vectors) < 2:
            return  # PCA needs at least 2 samples

        pca = reducer.fit_pca(full_vectors, self._reducer.target_dims)
        mean_json, components_json = reducer.serialize_pca(pca)
        next_version = (active["version"] + 1) if active else 1
        self._ledger.insert_reducer_version(
            version=next_version,
            input_dimensions=len(full_vectors[0]),
            output_dimensions=pca.n_components_,
            mean_json=mean_json,
            components_json=components_json,
            fitted_on_count=len(full_vectors),
        )

    def process(self, entity_type: str, entity_id: str, text: str) -> FingerprintResult:
        """Runs the full pipeline for one seed or candidate and persists
        the fingerprint + collision event. Does not mutate the entity —
        caller decides what to do with the classification."""
        self._maybe_refit_reducer()

        full_vector = embedder.embed(text, self._config.embedding_model)
        active_reducer = self._ledger.get_active_reducer_version()
        compressed_vector, compression_version = self._reducer.reduce(full_vector, active_reducer)
        fingerprint_hash = hashing.hash_vector(compressed_vector)

        existing = self._ledger.list_fingerprints_by_entity_type(entity_type)

        best_similarity = 0.0
        best_match_hash = None
        best_match_entity_id = None
        for row in existing:
            import json

            # PCA versions have different bases. The original embeddings share
            # one stable coordinate system, including across recalibration.
            other_vector = json.loads(row["full_vector"])
            score = similarity.cosine_similarity(full_vector, other_vector)
            if score > best_similarity:
                best_similarity = score
                best_match_hash = row["hash"]
                best_match_entity_id = row["entity_id"]

        classification = (
            similarity.classify(
                best_similarity,
                self._config.similarity_threshold_near_duplicate,
                self._config.similarity_threshold_exact_duplicate,
            )
            if existing
            else "NOVEL"
        )

        self._ledger.insert_fingerprint(
            hash=fingerprint_hash,
            entity_type=entity_type,
            entity_id=entity_id,
            compressed_vector=compressed_vector,
            full_vector=full_vector,
            embedding_model=self._config.embedding_model,
            compression_model_version=compression_version,
        )
        self._ledger.insert_collision_event(
            query_fingerprint_hash=fingerprint_hash,
            matched_fingerprint_hash=best_match_hash,
            similarity_score=best_similarity if existing else None,
            classification=classification,
        )

        return FingerprintResult(
            hash=fingerprint_hash,
            classification=classification,
            matched_hash=best_match_hash,
            matched_entity_id=best_match_entity_id,
            similarity_score=best_similarity if existing else None,
        )
