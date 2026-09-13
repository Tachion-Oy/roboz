"""Tests for explicit per-use OpenRouter request policy."""

from typing import Final
from types import SimpleNamespace

from roboz.llm import LLMEndpoint, with_openrouter_policy


_API_NAME: Final[str] = "openrouter"
_MODEL_NAME: Final[str] = "z-ai/glm-5.3"
_DEPENDENCY_ID: Final[str] = f"model:{_API_NAME}:{_MODEL_NAME}"


def _endpoint() -> LLMEndpoint:
    return LLMEndpoint(
        client=SimpleNamespace(chat=object(), models=object(), close=lambda: None),
        model_name=_MODEL_NAME,
        api_name=_API_NAME,
    )


def test_openrouter_policy_replaces_nitro_with_throughput_routing() -> None:
    configured = with_openrouter_policy(_endpoint())

    assert configured.extra_body == {
        "provider": {"sort": "throughput", "require_parameters": True}
    }
    assert configured.model_name == _MODEL_NAME
    assert configured.dependency_id == _DEPENDENCY_ID
    assert ":nitro" not in configured.model_name


def test_openrouter_policy_attaches_reasoning_per_use() -> None:
    canonical = _endpoint()
    orchestrator = with_openrouter_policy(canonical, reasoning_effort="low")
    memory = with_openrouter_policy(canonical, reasoning_effort="high")
    planner = with_openrouter_policy(canonical)
    expected_provider = {"sort": "throughput", "require_parameters": True}

    assert orchestrator.extra_body == {
        "provider": expected_provider,
        "reasoning": {"effort": "low"},
    }
    assert memory.extra_body == {
        "provider": expected_provider,
        "reasoning": {"effort": "high"},
    }
    assert planner.extra_body == {"provider": expected_provider}
    assert canonical.extra_body is None


def test_openrouter_policy_adds_optional_provider_ignore_list() -> None:
    configured = with_openrouter_policy(
        _endpoint(),
        reasoning_effort="max",
        ignored_providers=("provider-a", "provider-b"),
    )

    assert configured.extra_body == {
        "provider": {
            "sort": "throughput",
            "require_parameters": True,
            "ignore": ["provider-a", "provider-b"],
        },
        "reasoning": {"effort": "max"},
    }


def test_openrouter_policy_keeps_client_deferred_and_identity_stable():
    constructions = []
    canonical = _endpoint()
    canonical.client.materialize = lambda: constructions.append("initialized")
    configured = with_openrouter_policy(canonical, reasoning_effort="high")
    assert constructions == []
    assert configured.dependency_id == canonical.dependency_id
    assert configured.client is canonical.client
    assert configured.materialize() is configured
    assert constructions == ["initialized"]
    assert configured.extra_body == {
        "provider": {"sort": "throughput", "require_parameters": True},
        "reasoning": {"effort": "high"},
    }
