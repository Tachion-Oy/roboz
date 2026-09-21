from collections.abc import Mapping
from typing import assert_type

from roboz.llm import LLMEndpoint, TranscriptionEndpoint
from roboz.endpoints.specs import ModelSpec
from tests.type_tests.fixtures.inventory_models import custom, groq

assert_type(custom.chat, LLMEndpoint)
assert_type(custom.audio, TranscriptionEndpoint)
assert_type(groq.new_chat, LLMEndpoint)
assert_type(groq.whisper_large_v3_turbo, TranscriptionEndpoint)
assert_type(custom.models, tuple[ModelSpec, ...])
assert_type(custom.models_by_attribute, Mapping[str, ModelSpec])
assert_type(custom.configured(stream=False).chat, LLMEndpoint)
