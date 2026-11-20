"""Deduplication must survive changes to the stored PCA representation."""

from unittest.mock import patch

from hanno.config import FingerprintConfig
from hanno.fingerprint.engine import FingerprintEngine
from hanno.ledger.repository import Ledger


def test_duplicate_survives_initial_calibration_and_recalibration(tmp_path):
    ledger = Ledger.connect(tmp_path / "fingerprints.db")
    ledger.init_schema()
    config = FingerprintConfig(
        compressed_dimensions=2, calibration_minimum_fingerprints=3, recalibration_interval=2
    )
    engine = FingerprintEngine(ledger, config)
    vectors = [[1.0, 2.0, 3.0, 4.0], [4.0, 1.0, 2.0, 3.0], [-2.0, 3.0, 1.0, 4.0]]
    try:
        with patch("hanno.fingerprint.engine.embedder.embed", side_effect=vectors):
            for index in range(3):
                engine.process("seed", str(index), f"seed {index}")
        with patch("hanno.fingerprint.engine.embedder.embed", return_value=vectors[0]):
            for index in range(3):
                result = engine.process("seed", f"repeat-{index}", "same original seed")
                assert result.classification == "EXACT_DUPLICATE"
                assert result.matched_entity_id == "0"
        active = ledger.get_active_reducer_version()
        assert active is not None
        assert active["version"] == 2
    finally:
        ledger.close()
