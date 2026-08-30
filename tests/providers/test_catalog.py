from __future__ import annotations

from collections.abc import Callable

import pytest
from roboz.llm import LLMEndpoint
from roboz.tooling import LazyExternalDependency

from roboz.llm.providers.catalog import ProviderCatalog
from roboz.llm.providers.model_types import ChatModelSpec


def _fake_endpoint(model: ChatModelSpec, model_name: str) -> LLMEndpoint:
    return LLMEndpoint(
        client={"model_id": model.model_id},
        model_name=model_name,
        api_name="test",
        max_context_tokens=model.max_context_tokens,
    )


def _catalog(
    *models: ChatModelSpec,
    endpoint_factory: Callable[
        [ChatModelSpec, str], LLMEndpoint
    ] = _fake_endpoint,
) -> ProviderCatalog:
    return ProviderCatalog(
        api_name="test",
        endpoint_factory=endpoint_factory,
        models=models,
    )


def test_catalog_inspection_does_not_materialize_endpoint() -> None:
    calls: list[str] = []

    def endpoint_factory(model: ChatModelSpec, model_name: str) -> LLMEndpoint:
        calls.append(model_name)
        return _fake_endpoint(model, model_name)

    provider = _catalog(
        ChatModelSpec("chat-model", 128_000),
        endpoint_factory=endpoint_factory,
    )

    dependency = provider.chat_model

    assert dependency.dependency_id == "model:test:chat-model"
    assert dependency.redacted_metadata() == {
        "api_name": "test",
        "model_name": "chat-model",
        "endpoint_type": "llm",
    }
    assert "chat_model" in dir(provider)
    assert calls == []


def test_catalog_materializes_chat_endpoint_once() -> None:
    materializations: list[str] = []

    def endpoint_factory(model: ChatModelSpec, model_name: str) -> LLMEndpoint:
        materializations.append(model_name)
        return _fake_endpoint(model, model_name)

    provider = _catalog(
        ChatModelSpec("chat-model", 128_000),
        endpoint_factory=endpoint_factory,
    )

    dependency = provider.chat_model
    endpoint = dependency.materialize()

    assert isinstance(dependency, LazyExternalDependency)
    assert isinstance(endpoint, LLMEndpoint)
    assert endpoint.client == {"model_id": "chat-model"}
    assert endpoint.model_name == "chat-model"
    assert endpoint.max_context_tokens == 128_000
    assert dependency.materialize() is endpoint
    assert materializations == ["chat-model"]


def test_catalog_accepts_provider_specific_model_name_factory() -> None:
    provider = ProviderCatalog(
        api_name="test",
        endpoint_factory=_fake_endpoint,
        models=(ChatModelSpec("chat-model", 128_000),),
        model_name_factory=lambda model: f"{model.model_id}:fast",
    )

    dependency = provider.chat_model

    assert dependency.redacted_metadata()["model_name"] == "chat-model:fast"
    assert dependency.materialize().model_name == "chat-model:fast"


def test_catalog_rejects_model_ids_with_colliding_attribute_names() -> None:
    with pytest.raises(ValueError, match="colliding model attribute names"):
        _catalog(
            ChatModelSpec("some-model", 128_000),
            ChatModelSpec("some.model", 128_000),
        )


def test_catalog_reports_unknown_model_attributes() -> None:
    provider = _catalog(ChatModelSpec("chat-model", 128_000))

    with pytest.raises(AttributeError, match="chat_model"):
        getattr(provider, "missing")
