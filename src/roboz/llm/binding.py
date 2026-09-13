"""Request policy and validation for concrete language-model endpoints."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Final, TypedDict, overload

from roboz.llm.endpoints import (
    EndpointLike,
    LLMEndpoint,
    LLMEndpointRoute,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    TranscriptionEndpoint,
    TranscriptionEndpointLike,
    copy_request_options,
)

_EXTRA_BODY_FIELD: Final[str] = "extra_body"


@overload
def with_request_options(
    endpoint: LLMEndpoint, *, extra_body: Mapping[str, object]
) -> LLMEndpoint: ...


@overload
def with_request_options(
    endpoint: LLMEndpointRoute[LLMEndpoint], *, extra_body: Mapping[str, object]
) -> LLMEndpointRoute[LLMEndpoint]: ...


def with_request_options(
    endpoint: LLMEndpoint | LLMEndpointRoute[LLMEndpoint],
    *,
    extra_body: Mapping[str, object],
) -> LLMEndpoint | LLMEndpointRoute[LLMEndpoint]:
    """Detach request policy while retaining resource identity and live selection.

    Configuring a route does not call its getter. Each resolution receives fresh
    options; inspection continues to report the original selected resource.
    """
    options = copy_request_options(extra_body)
    if isinstance(endpoint, LLMEndpointRoute):
        return replace(endpoint, _extra_body=options)
    if not isinstance(endpoint, LLMEndpoint):
        raise TypeError("endpoint must be an LLMEndpoint or LLMEndpointRoute")
    return endpoint.model_copy(update={_EXTRA_BODY_FIELD: options})


def _validate_endpoint(endpoint: EndpointLike) -> None:
    """Reject unsupported endpoint values without calling a client."""
    if not isinstance(endpoint, (LLMEndpoint, MockLLMEndpoint, LLMEndpointRoute)):
        raise TypeError(
            "endpoint must be an LLMEndpoint, MockLLMEndpoint or LLMEndpointRoute"
        )


class LLMTelemetryDict(TypedDict, total=False):
    """Wire format for LLMTelemetry model dumps with omitted None values."""

    endpoint: str
    model: str
    token_input: int
    token_output: int


def resolve_endpoint(endpoint: EndpointLike) -> LLMEndpoint | MockLLMEndpoint:
    """Resolve the current chat endpoint without initializing its client.

    A route calls its getter and applies any detached request policy. Direct
    endpoints are returned unchanged. Getters must perform no external work.
    """
    _validate_endpoint(endpoint)
    return endpoint.resolve() if isinstance(endpoint, LLMEndpointRoute) else endpoint


def resolve_transcription_endpoint(
    endpoint: TranscriptionEndpointLike,
) -> TranscriptionEndpoint | MockTranscriptionEndpoint:
    """Validate and return the supplied transcription endpoint unchanged."""
    if isinstance(endpoint, (TranscriptionEndpoint, MockTranscriptionEndpoint)):
        return endpoint
    raise TypeError(
        "endpoint must be a TranscriptionEndpoint or MockTranscriptionEndpoint"
    )
