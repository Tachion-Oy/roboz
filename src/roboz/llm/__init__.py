"""Provider-neutral structured language-model operations."""

from roboz.llm._truncation import (
    estimate_conversation_tokens,
    get_truncated_messages_for_context,
)
from roboz.llm.binding import LLMTelemetryDict, resolve_endpoint, with_request_options
from roboz.llm.calls import call_llm_api, call_transcription_api
from roboz.llm.completion import get_completion
from roboz.llm.endpoints import (
    EndpointLike,
    JSONValue,
    LLMEndpoint,
    LLMPricing,
    MockLLMEndpoint,
    MockProviderError,
    MockTranscriptionEndpoint,
    ModelSelector,
    RequestOptions,
    TranscriptionEndpoint,
    TranscriptionEndpointLike,
    role_to_user_mapper,
)
from roboz.llm.openrouter import (
    OpenRouterReasoningEffort,
    openrouter_request_options,
    with_openrouter_policy,
)
from roboz.llm.prompts import get_basic_system_prompt

__all__ = [
    "EndpointLike",
    "JSONValue",
    "LLMEndpoint",
    "LLMPricing",
    "LLMTelemetryDict",
    "MockLLMEndpoint",
    "MockProviderError",
    "MockTranscriptionEndpoint",
    "ModelSelector",
    "OpenRouterReasoningEffort",
    "RequestOptions",
    "TranscriptionEndpoint",
    "TranscriptionEndpointLike",
    "call_llm_api",
    "call_transcription_api",
    "estimate_conversation_tokens",
    "get_basic_system_prompt",
    "get_completion",
    "openrouter_request_options",
    "get_truncated_messages_for_context",
    "resolve_endpoint",
    "role_to_user_mapper",
    "with_request_options",
    "with_openrouter_policy",
]
