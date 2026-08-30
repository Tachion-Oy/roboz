from dataclasses import FrozenInstanceError
from importlib import import_module

import pytest

from roboz.llm.providers.model_types import (
    ChatModelSpec,
    EndpointType,
    TranscriptionModelSpec,
)
from roboz.standard.providers import example_openrouter
from roboz.standard.providers.openrouter import (
    ExampleOpenRouterCatalog,
    OpenRouterApiKey,
)

openrouter_module = import_module("roboz.standard.providers.openrouter")


def test_package_exports_configured_catalogs() -> None:
    assert example_openrouter is openrouter_module.example_openrouter


def test_generic_catalog_module_has_no_openrouter_configuration() -> None:
    catalog_module = import_module("roboz.llm.providers.catalog")

    assert not hasattr(catalog_module, "OpenRouterCatalog")
    assert not hasattr(catalog_module, "OPENROUTER_MODELS")
    assert not hasattr(catalog_module, "OPENROUTER_BASE_URL")


def test_openrouter_loads_its_credentials_only_when_materialized(monkeypatch) -> None:
    key_loads: list[OpenRouterApiKey] = []

    def load_api_key(key: OpenRouterApiKey) -> str:
        key_loads.append(key)
        return "secret"

    monkeypatch.setattr(openrouter_module, "load_key", load_api_key)
    monkeypatch.setattr(
        openrouter_module,
        "openrouter_client",
        lambda api_key: {"api_key": api_key},
    )
    provider = ExampleOpenRouterCatalog()

    dependency = provider.example__mock_chat_model
    assert key_loads == []

    endpoint = dependency.materialize()

    assert endpoint.client == {"api_key": "secret"}
    assert endpoint.api_name == "openrouter"
    assert endpoint.model_name == "example/mock-chat-model:nitro"
    assert key_loads == [OpenRouterApiKey.API_KEY]


def test_model_specs_are_immutable_and_validate_their_values() -> None:
    model = ChatModelSpec("chat-model", 128_000)

    with pytest.raises(FrozenInstanceError):
        setattr(model, "model_id", "changed")
    with pytest.raises(ValueError, match="model_id"):
        ChatModelSpec("", 128_000)
    with pytest.raises(ValueError, match="max_context_tokens"):
        ChatModelSpec("chat-model", 0)
    with pytest.raises(ValueError, match="model_id"):
        TranscriptionModelSpec("")


def test_model_specs_declare_endpoint_types() -> None:
    assert ChatModelSpec("chat-model", 128_000).endpoint_type is EndpointType.LLM
    assert (
        TranscriptionModelSpec("speech-model").endpoint_type
        is EndpointType.TRANSCRIPTION
    )


def test_openrouter_runtime_names_use_nitro_suffix() -> None:
    for attribute, model in example_openrouter.models_by_attribute.items():
        dependency = getattr(example_openrouter, attribute)
        assert dependency.redacted_metadata()["model_name"] == f"{model.model_id}:nitro"


def test_openrouter_catalog_attributes_remain_unsuffixed() -> None:
    assert tuple(example_openrouter.models_by_attribute) == (
        "example__mock_chat_model",
    )
    model = example_openrouter.models_by_attribute["example__mock_chat_model"]
    assert model.max_context_tokens == 128_000
    assert "example__mock_chat_model_nitro" not in example_openrouter.models_by_attribute
