from collections.abc import Mapping
from typing import assert_type

from roboz import Agent, LazyExternalDependency
from roboz.llm import LLMEndpoint, TranscriptionEndpoint, with_openrouter_policy
from roboz_endpoints import cerebras, groq, openrouter
from roboz_endpoints.adapters.openai_compatible import (
    OpenAICompatibleAdapter,
    chat_endpoint,
    transcription_endpoint,
)
from roboz_endpoints.catalog import openrouter as catalogue_openrouter
from roboz_endpoints.inventory import ChatModelSpec, TranscriptionModelSpec

# All public import paths carry the same generated model types.
from roboz_endpoints.inventory import (
    CEREBRAS_MODELS,
    GROQ_MODELS,
    OPENROUTER_MODELS,
    openrouter as inventory_openrouter,
)


assert_type(openrouter.z_ai__glm_5_3, LazyExternalDependency[LLMEndpoint])
assert_type(openrouter.z_ai__glm_5_3_flash, LazyExternalDependency[LLMEndpoint])
assert_type(cerebras.gpt_oss_120b, LazyExternalDependency[LLMEndpoint])
assert_type(groq.whisper_large_v3_turbo, LazyExternalDependency[TranscriptionEndpoint])
assert_type(catalogue_openrouter.z_ai__glm_5_3, LazyExternalDependency[LLMEndpoint])
assert_type(openrouter.models, tuple[ChatModelSpec, ...])
assert_type(cerebras.models, tuple[ChatModelSpec, ...])
assert_type(groq.models, tuple[TranscriptionModelSpec, ...])
assert_type(openrouter.models_by_attribute, Mapping[str, ChatModelSpec])
assert_type(cerebras.models_by_attribute, Mapping[str, ChatModelSpec])
assert_type(groq.models_by_attribute, Mapping[str, TranscriptionModelSpec])
assert_type(
    with_openrouter_policy(openrouter.z_ai__glm_5_3),
    LazyExternalDependency[LLMEndpoint],
)
assert_type(
    chat_endpoint(model="custom", max_context_tokens=123),
    LazyExternalDependency[LLMEndpoint],
)
assert_type(
    transcription_endpoint(model="custom"),
    LazyExternalDependency[TranscriptionEndpoint],
)
adapter = OpenAICompatibleAdapter(api_name="custom", api_key="test", timeout_s=12)
assert_type(
    adapter.chat_endpoint(model="chat", max_context_tokens=123, stream=False),
    LazyExternalDependency[LLMEndpoint],
)
assert_type(
    adapter.transcription_endpoint(model="audio"),
    LazyExternalDependency[TranscriptionEndpoint],
)
assert_type(
    openrouter.configured(api_key="test", timeout_s=12, stream=False).z_ai__glm_5_3_flash,
    LazyExternalDependency[LLMEndpoint],
)
assert_type(
    cerebras.configured(api_key="test", timeout_s=12, stream=False).gpt_oss_120b,
    LazyExternalDependency[LLMEndpoint],
)
assert_type(
    groq.configured(api_key="test", timeout_s=12).whisper_large_v3_turbo,
    LazyExternalDependency[TranscriptionEndpoint],
)
Agent(name="catalogue", system_prompt="Stop.", agent_endpoint=cerebras.gpt_oss_120b)

assert_type(inventory_openrouter.z_ai__glm_5_3, LazyExternalDependency[LLMEndpoint])
assert_type(OPENROUTER_MODELS, tuple[ChatModelSpec, ...])
assert_type(CEREBRAS_MODELS, tuple[ChatModelSpec, ...])
assert_type(GROQ_MODELS, tuple[TranscriptionModelSpec, ...])
assert_type(adapter.api_name, str)
assert_type(adapter.configured(api_key="another"), OpenAICompatibleAdapter)
