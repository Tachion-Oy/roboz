from typing import TypedDict, cast

from roboz.llm.endpoints import (
    EndpointLike,
    LLMEndpoint,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    TranscriptionEndpoint,
    TranscriptionEndpointLike,
)
from roboz.tooling.dependencies import ExternalDependency, ToolDependency

EndpointBinding = ToolDependency[ExternalDependency] | MockLLMEndpoint


def bind_endpoint(endpoint: EndpointLike) -> EndpointBinding:
    """Bind real endpoint resources while leaving test mocks ordinary."""
    if isinstance(endpoint, MockLLMEndpoint):
        return endpoint
    if not isinstance(endpoint, ExternalDependency):
        raise TypeError("endpoint must be an external dependency or MockLLMEndpoint")
    return ToolDependency(endpoint)


def endpoint_resource(endpoint: EndpointBinding) -> EndpointLike:
    """Return the callable endpoint represented by a factory context binding."""
    if isinstance(endpoint, ToolDependency):
        return cast(EndpointLike, endpoint.resource)
    return endpoint


class LLMTelemetryDict(TypedDict, total=False):
    """Wire format for LLMTelemetry model dumps with omitted None values."""

    endpoint: str
    model: str
    token_input: int
    token_output: int


def resolve_endpoint(endpoint: EndpointLike) -> LLMEndpoint | MockLLMEndpoint:
    if isinstance(endpoint, ExternalDependency):
        materialized = endpoint.materialize()
        if not isinstance(materialized, LLMEndpoint):
            raise TypeError("LLM dependency did not materialize an LLMEndpoint")
        return materialized
    if isinstance(endpoint, MockLLMEndpoint):
        return endpoint
    raise TypeError("endpoint must be an endpoint dependency or MockLLMEndpoint")


def resolve_transcription_endpoint(
    endpoint: TranscriptionEndpointLike,
) -> TranscriptionEndpoint | MockTranscriptionEndpoint:
    if isinstance(endpoint, ExternalDependency):
        materialized = endpoint.materialize()
        if not isinstance(materialized, TranscriptionEndpoint):
            raise TypeError(
                "transcription dependency did not materialize a TranscriptionEndpoint"
            )
        return materialized
    if isinstance(endpoint, MockTranscriptionEndpoint):
        return endpoint
    raise TypeError(
        "endpoint must be a transcription dependency or MockTranscriptionEndpoint"
    )
