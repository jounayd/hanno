"""build_client: the single place that maps config.excavator.provider to a
concrete ModelClient. Hermetic -- constructs real client objects (their
__init__ methods don't make network calls) without ever calling generate()."""
import pytest

from hanno.config import HannoConfig
from hanno.excavator.client import AnthropicClient
from hanno.excavator.client_factory import build_client, build_prover_client
from hanno.excavator.openai_client import OpenAICompatibleClient


def test_anthropic_provider_builds_anthropic_client():
    config = HannoConfig()
    config.excavator.provider = "anthropic"
    config.excavator.model = "claude-sonnet-4-5-20250929"

    client = build_client(config, api_key="test-key")

    assert isinstance(client, AnthropicClient)


def test_openai_compatible_provider_builds_openai_client():
    config = HannoConfig()
    config.excavator.provider = "openai_compatible"
    config.excavator.base_url = "http://localhost:11434/v1"
    config.excavator.model = "deepseek-r1-14b-hanno"

    client = build_client(config, api_key=None)

    assert isinstance(client, OpenAICompatibleClient)


def test_openai_compatible_without_base_url_raises_clear_error():
    config = HannoConfig()
    config.excavator.provider = "openai_compatible"
    config.excavator.base_url = None

    with pytest.raises(ValueError, match="base_url"):
        build_client(config, api_key=None)


def test_unknown_provider_raises_clear_error():
    config = HannoConfig()
    config.excavator.provider = "made_up_provider"

    with pytest.raises(ValueError, match="made_up_provider"):
        build_client(config, api_key=None)


def test_max_tokens_override_takes_precedence_over_config_default():
    config = HannoConfig()
    config.excavator.provider = "anthropic"
    config.excavator.max_tokens_per_candidate = 8000

    client = build_client(config, api_key="test-key", max_tokens=1500)

    assert client._max_tokens == 1500


def test_max_tokens_defaults_to_config_when_not_overridden():
    config = HannoConfig()
    config.excavator.provider = "anthropic"
    config.excavator.max_tokens_per_candidate = 8000

    client = build_client(config, api_key="test-key")

    assert client._max_tokens == 8000


def test_request_timeout_flows_to_anthropic_client():
    config = HannoConfig()
    config.excavator.provider = "anthropic"
    config.excavator.request_timeout_seconds = 1800.0

    client = build_client(config, api_key="test-key")

    assert client._client.timeout == 1800.0


def test_request_timeout_flows_to_openai_compatible_client():
    config = HannoConfig()
    config.excavator.provider = "openai_compatible"
    config.excavator.base_url = "http://localhost:11434/v1"
    config.excavator.request_timeout_seconds = 1800.0

    client = build_client(config, api_key=None)

    assert client._client.timeout == 1800.0


def test_prover_client_is_none_when_prover_model_not_set():
    # The default, fully backward-compatible case: no separate prover role
    # configured, every caller keeps reusing the single main client.
    config = HannoConfig()
    config.excavator.provider = "anthropic"

    assert build_prover_client(config, api_key="test-key") is None


def test_prover_client_uses_prover_model_with_same_provider_by_default():
    config = HannoConfig()
    config.excavator.provider = "anthropic"
    config.excavator.model = "claude-haiku-4-5-20251001"
    config.excavator.prover_model = "claude-sonnet-4-5-20250929"

    client = build_prover_client(config, api_key="test-key")

    assert isinstance(client, AnthropicClient)
    assert client._model == "claude-sonnet-4-5-20250929"


def test_prover_client_can_use_a_fully_different_provider():
    # Cheap/fast local model proposes and formalizes; a real hosted model
    # does the actually-hard proving -- the Migration Plan Phase 6 idea.
    config = HannoConfig()
    config.excavator.provider = "openai_compatible"
    config.excavator.base_url = "http://localhost:11434/v1"
    config.excavator.model = "deepseek-r1-14b-hanno"
    config.excavator.prover_provider = "anthropic"
    config.excavator.prover_model = "claude-sonnet-4-5-20250929"

    client = build_prover_client(config, api_key="test-key")

    assert isinstance(client, AnthropicClient)
    assert client._model == "claude-sonnet-4-5-20250929"


def test_prover_client_falls_back_to_main_base_url_when_unset():
    config = HannoConfig()
    config.excavator.provider = "openai_compatible"
    config.excavator.base_url = "http://localhost:11434/v1"
    config.excavator.prover_model = "a-stronger-local-model"

    client = build_prover_client(config, api_key=None)

    assert isinstance(client, OpenAICompatibleClient)


def test_prover_client_without_base_url_on_openai_compatible_raises_clear_error():
    config = HannoConfig()
    config.excavator.provider = "anthropic"
    config.excavator.prover_provider = "openai_compatible"
    config.excavator.prover_model = "some-model"
    config.excavator.prover_base_url = None

    with pytest.raises(ValueError, match="prover_base_url"):
        build_prover_client(config, api_key=None)
