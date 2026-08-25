"""Rule-based seed validation (Step 2 simplification).

Concept-level duplicate detection is no longer part of this module — it now
lives in the Fingerprint Engine (see tests/test_fingerprint_engine.py and
tests/test_seed_manager.py).
"""
from hanno.seeds.validation import validate_seed_text


def test_vague_seed_rejected():
    result = validate_seed_text("interesting mathematics", "mathematics")
    assert not result.ok
    assert "too short" in result.feedback or "too broad" in result.feedback


def test_too_short_seed_rejected():
    result = validate_seed_text("short seed text here", "mathematics")
    assert not result.ok
    assert "too short" in result.feedback


def test_well_formed_math_seed_passes():
    text = (
        "Explore possible generalizations of the classification theorem for "
        "finite simple groups to infinite discrete groups with a topology, "
        "focusing on connections to algebra and category theory structures."
    )
    result = validate_seed_text(text, "mathematics")
    assert result.ok


def test_no_domain_keyword_rejected():
    text = (
        "A very long seed text about nothing specific at all just generic "
        "words strung together without any recognizable technical vocabulary "
        "from any particular area of inquiry whatsoever right now here."
    )
    result = validate_seed_text(text, "mathematics")
    assert not result.ok
    assert "too broad" in result.feedback


def test_unknown_domain_rejected():
    text = "A perfectly reasonable length seed about number theory and algebra structures."
    result = validate_seed_text(text, "astrology")
    assert not result.ok
    assert "unknown domain" in result.feedback


