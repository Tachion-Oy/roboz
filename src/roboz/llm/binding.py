from collections.abc import Mapping
from typing import Final, TypedDict, cast, overload

from roboz.llm.endpoints import (
    EndpointLike,
    LLMEndpoint,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    TranscriptionEndpoint,
    TranscriptionEndpointLike,
    copy_request_options,
)
from roboz.tooling.dependencies import (
    ExternalDependency,
    LazyExternalDependency,
    ToolDependency,
)

_EXTRA_BODY_FIELD: Final[str] = "extra_body"

EndpointBinding = ToolDependency[ExternalDependency] | MockLLMEndpoint


@overload
def with_request_options(
    endpoint: LLMEndpoint, *, extra_body: Mapping[str, object]
) -> LLMEndpoint: ...


@overload
def with_request_options(
    endpoint: LazyExternalDependency[LLMEndpoint],
    *,
    extra_body: Mapping[str, object],
) -> LazyExternalDependency[LLMEndpoint]: ...


def with_request_options(
    endpoint: LLMEndpoint | LazyExternalDependency[LLMEndpoint],
    *,
    extra_body: Mapping[str, object],
) -> LLMEndpoint | LazyExternalDependency[LLMEndpoint]:
    """Copy an endpoint resource with per-use provider request options.

    Lazy resources remain lazy and retain their canonical dependency identity.
    The supplied mapping is validated and copied before it is captured.
    """
    options = copy_request_options(extra_body)
    if isinstance(endpoint, LLMEndpoint):
        return endpoint.model_copy(update={_EXTRA_BODY_FIELD: options})
    if not isinstance(endpoint, LazyExternalDependency):
        raise TypeError("endpoint must be an LLMEndpoint or lazy LLM dependency")

    def configured_endpoint() -> LLMEndpoint:
        materialized = endpoint.materialize()
        if not isinstance(materialized, LLMEndpoint):
            raise TypeError("LLM dependency did not materialize an LLMEndpoint")
        return materialized.model_copy(
            update={_EXTRA_BODY_FIELD: copy_request_options(options)}
        )

    return LazyExternalDependency(
        dependency_id_value=endpoint.dependency_id,
        dependency_kind=endpoint.kind,
        metadata=endpoint.redacted_metadata(),
        resolver=configured_endpoint,
    )


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
