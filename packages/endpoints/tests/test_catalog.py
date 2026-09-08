from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import pickle

import pytest

from roboz_endpoints import cerebras, groq, openrouter
from roboz_endpoints.catalog import Catalog
from roboz_endpoints.adapters.openai_compatible import OpenAICompatibleAdapter
from roboz_endpoints.inventory import (
    CEREBRAS_MODELS,
    GROQ_MODELS,
    OPENROUTER_MODELS,
    ChatModelSpec,
    TranscriptionModelSpec,
)


@pytest.mark.parametrize(
    "provider,inventory",
    [(openrouter, OPENROUTER_MODELS), (cerebras, CEREBRAS_MODELS), (groq, GROQ_MODELS)],
)
def test_inventory_constants_reference_specs_and_discoverable_attributes(
    provider, inventory
):
    assert inventory is provider.models
    assert tuple(provider.models_by_attribute.values()) == inventory
    assert set(provider.models_by_attribute) <= set(dir(provider))


@pytest.mark.parametrize(
    "spec", [ChatModelSpec("test/chat", 123), TranscriptionModelSpec("test/audio")]
)
def test_spec_serialization_keeps_public_module_identity(spec):
    assert type(spec).__module__ == "roboz_endpoints.inventory"
    assert pickle.loads(pickle.dumps(spec)) == spec
    with pytest.raises(FrozenInstanceError):
        spec.model_id = "changed"


def test_inventory_and_discovery():
    expected = [
        (openrouter, "z_ai__glm_5_3", "z-ai/glm-5.3", 1_310_720),
        (openrouter, "z_ai__glm_5_3_flash", "z-ai/glm-5.3-flash", 1_310_720),
        (cerebras, "gpt_oss_120b", "gpt-oss-120b", 131_072),
        (groq, "whisper_large_v3_turbo", "whisper-large-v3-turbo", None),
    ]
    assert sum(len(provider.models) for provider in (openrouter, cerebras, groq)) == 4
    for provider, attribute, model_id, context in expected:
        assert attribute in dir(provider)
        spec = provider.models_by_attribute[attribute]
        assert spec.model_id == model_id
        assert getattr(spec, "max_context_tokens", None) == context
        assert spec.endpoint_type == ("llm" if context else "transcription")
        endpoint = getattr(provider, attribute)
        assert endpoint is getattr(provider, attribute)
        assert endpoint.dependency_id == f"model:{provider.api_name}:{model_id}"
        assert endpoint.redacted_metadata() == {
            "api_name": provider.api_name,
            "model_name": model_id,
            "endpoint_type": spec.endpoint_type,
        }
        assert "materialized" not in endpoint.__dict__
    with pytest.raises(TypeError):
        openrouter.models_by_attribute["new"] = ChatModelSpec("new", 1)
    with pytest.raises(FrozenInstanceError):
        openrouter.models[0].model_id = "changed"


@pytest.mark.parametrize(
    "provider_class,attribute",
    [
        (openrouter.configured, "z_ai__glm_5_3"),
        (openrouter.configured, "z_ai__glm_5_3_flash"),
        (cerebras.configured, "gpt_oss_120b"),
        (groq.configured, "whisper_large_v3_turbo"),
    ],
)
def test_catalogue_caches_concurrent_access(provider_class, attribute):
    from threading import Barrier

    catalogue = provider_class()
    start = Barrier(8)

    def select(_):
        start.wait(timeout=5)
        return getattr(catalogue, attribute)

    with ThreadPoolExecutor(max_workers=8) as executor:
        endpoints = list(executor.map(select, range(8)))
    assert all(endpoint is endpoints[0] for endpoint in endpoints)
    assert "materialized" not in endpoints[0].__dict__
    assert getattr(provider_class(), attribute) is not endpoints[0]
    with pytest.raises(AttributeError):
        catalogue.missing


@pytest.mark.parametrize(
    "provider_class", [openrouter.configured, cerebras.configured, groq.configured]
)
def test_catalogue_validates_settings_only_on_model_access(provider_class):
    catalogue = provider_class(timeout_s=0)
    assert catalogue.models
    assert set(catalogue.models_by_attribute) <= set(dir(catalogue))
    for attribute in catalogue.models_by_attribute:
        # Discovery stays usable, and failed access is never cached as a value.
        for _ in range(2):
            with pytest.raises(
                ValueError, match="timeout_s must be finite and positive"
            ):
                getattr(catalogue, attribute)


def test_discovery_does_not_import_sdk_or_read_credentials():
    import subprocess
    import sys
    import textwrap

    subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""\
            import builtins
            import os

            original_import = builtins.__import__
            original_get = os.environ.get

            def import_module(name, *args, **kwargs):
                if name == 'openai' or name.startswith('openai.'):
                    raise AssertionError('SDK imported during discovery')
                return original_import(name, *args, **kwargs)

            def get_environment(key, *args):
                if key in {'OPENROUTER_API_KEY', 'CEREBRAS_API_KEY', 'GROQ_API_KEY'}:
                    raise AssertionError('Credentials read during discovery')
                return original_get(key, *args)

            builtins.__import__ = import_module
            os.environ.get = get_environment
            from roboz_endpoints import openrouter, cerebras, groq
            for provider in (openrouter, cerebras, groq):
                assert provider.models
                assert set(provider.models_by_attribute) <= set(dir(provider))
                for name in provider.models_by_attribute:
                    endpoint = getattr(provider, name)
                    assert endpoint.dependency_id
                    assert endpoint.redacted_metadata()
                    assert 'materialized' not in endpoint.__dict__
        """),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "factory,args",
    [
        (ChatModelSpec, (" ", 1)),
        (ChatModelSpec, ("model", 0)),
        (TranscriptionModelSpec, ("",)),
    ],
)
def test_invalid_model_specs(factory, args):
    with pytest.raises(ValueError):
        factory(*args)


def test_collections_share_one_catalogue_class_and_imports():
    from roboz_endpoints import catalog, inventory

    for name, collection in inventory.CATALOGS.items():
        assert type(collection) is Catalog
        assert getattr(inventory, name) is collection
        assert getattr(catalog, name) is collection
    assert catalog.__all__ == ["Catalog", *inventory.CATALOGS]
    with pytest.raises(AttributeError):
        catalog.unknown_provider


@pytest.mark.parametrize("name", ["bad-name", "_private", "class", "models", "configured"])
def test_model_attributes_cannot_hide_catalogue_api(name):
    with pytest.raises(ValueError, match="Invalid model attribute"):
        Catalog(adapter=OpenAICompatibleAdapter(), models={name: ChatModelSpec("m", 1)})


def test_any_provider_can_mix_chat_and_transcription():
    models = {
        "audio": TranscriptionModelSpec("test/audio"),
        "chat": ChatModelSpec("test/chat", 123),
    }
    collection = Catalog(
        adapter=OpenAICompatibleAdapter(api_name="another_provider"), models=models
    )
    models.clear()
    assert list(collection.models_by_attribute) == ["audio", "chat"]
    assert collection.audio.redacted_metadata()["endpoint_type"] == "transcription"
    assert collection.chat.redacted_metadata()["endpoint_type"] == "llm"
    assert collection.audio.dependency_id == "model:another_provider:test/audio"
    assert collection.chat.dependency_id == "model:another_provider:test/chat"
    assert collection.audio is collection.audio
    assert collection.chat is collection.chat


@pytest.mark.parametrize(
    "serialized,expected",
    [
        (
            b'\x80\x04\x95E\x00\x00\x00\x00\x00\x00\x00\x8c\x19roboz_endpoints.inventory\x94\x8c\rChatModelSpec\x94\x93\x94)\x81\x94]\x94(\x8c\ttest/chat\x94K{eb.',
            ChatModelSpec("test/chat", 123),
        ),
        (
            b'\x80\x04\x95L\x00\x00\x00\x00\x00\x00\x00\x8c\x19roboz_endpoints.inventory\x94\x8c\x16TranscriptionModelSpec\x94\x93\x94)\x81\x94]\x94\x8c\ntest/audio\x94ab.',
            TranscriptionModelSpec("test/audio"),
        ),
    ],
)
def test_specs_serialized_before_module_move_still_load(serialized, expected):
    assert pickle.loads(serialized) == expected
