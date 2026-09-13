"""Request policy and validation for concrete language-model endpoints."""

from collections.abc import Mapping
from typing import Final, TypedDict

from roboz.llm.endpoints import (
    EndpointLike,
    LLMEndpoint,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    TranscriptionEndpoint,
    TranscriptionEndpointLike,
    copy_request_options,
)

_EXTRA_BODY_FIELD: Final[str] = "extra_body"


def with_request_options(
    endpoint: LLMEndpoint, *, extra_body: Mapping[str, object]
) -> LLMEndpoint:
    """Copy an endpoint with validated, detached provider request options.

    Retain the configured client and canonical dependency identity. No client
    is created or called; resource construction belongs to the caller.
    """
    options = copy_request_options(extra_body)
    if not isinstance(endpoint, LLMEndpoint):
        raise TypeError("endpoint must be an LLMEndpoint")
    return endpoint.model_copy(update={_EXTRA_BODY_FIELD: options})


def _validate_endpoint(endpoint: EndpointLike) -> None:
    """Reject unsupported endpoint values without calling a client."""
    if not isinstance(endpoint, (LLMEndpoint, MockLLMEndpoint)):
        raise TypeError("endpoint must be an LLMEndpoint or MockLLMEndpoint")


class LLMTelemetryDict(TypedDict, total=False):
    """Wire format for LLMTelemetry model dumps with omitted None values."""

    endpoint: str
    model: str
    token_input: int
    token_output: int


def resolve_endpoint(endpoint: EndpointLike) -> LLMEndpoint | MockLLMEndpoint:
    """Validate and return the supplied chat endpoint without copying it.

    This operation performs no construction, materialization, or external work.
    """
    _validate_endpoint(endpoint)
    return endpoint


def resolve_transcription_endpoint(
    endpoint: TranscriptionEndpointLike,
) -> TranscriptionEndpoint | MockTranscriptionEndpoint:
    """Validate and return the supplied transcription endpoint unchanged."""
    if isinstance(endpoint, (TranscriptionEndpoint, MockTranscriptionEndpoint)):
        return endpoint
    raise TypeError(
        "endpoint must be a TranscriptionEndpoint or MockTranscriptionEndpoint"
    )
