"""Request policy and deferred resolution for language-model endpoints."""

from collections.abc import Mapping
from typing import Final, TypedDict, overload

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
    ExternalDependencyReference,
    LazyExternalDependency,
)

_EXTRA_BODY_FIELD: Final[str] = "extra_body"


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


@overload
def with_request_options(
    endpoint: ExternalDependencyReference[LLMEndpoint],
    *,
    extra_body: Mapping[str, object],
) -> ExternalDependencyReference[LLMEndpoint]: ...


def with_request_options(
    endpoint: LLMEndpoint | ExternalDependencyReference[LLMEndpoint],
    *,
    extra_body: Mapping[str, object],
) -> LLMEndpoint | ExternalDependencyReference[LLMEndpoint]:
    """Copy an endpoint resource with per-use provider request options.

    Lazy resources remain lazy and retain their canonical dependency identity.
    References select their current target on every materialization and inspection.
    The supplied mapping is validated and copied before it is captured.
    """
    options = copy_request_options(extra_body)
    if isinstance(endpoint, LLMEndpoint):
        return endpoint.model_copy(update={_EXTRA_BODY_FIELD: options})
    if not isinstance(endpoint, ExternalDependencyReference):
        raise TypeError("endpoint must be an LLMEndpoint or LLM dependency reference")
    if not isinstance(endpoint, LazyExternalDependency):
        return _ConfiguredEndpointReference(endpoint, options)

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


class _ConfiguredEndpointReference(ExternalDependencyReference[LLMEndpoint]):
    """Apply captured options to the current target without caching selection."""

    __slots__ = ("_endpoint", "_options")

    def __init__(
        self,
        endpoint: ExternalDependencyReference[LLMEndpoint],
        options: Mapping[str, object],
    ) -> None:
        self._endpoint = endpoint
        self._options = options

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Inspect the reference's current resources without materializing them."""
        return self._endpoint.external_dependencies()

    def materialize(self) -> LLMEndpoint:
        """Configure the currently selected endpoint, retaining its client."""
        materialized = self._endpoint.materialize()
        if not isinstance(materialized, LLMEndpoint):
            raise TypeError("LLM dependency did not materialize an LLMEndpoint")
        return materialized.model_copy(
            update={_EXTRA_BODY_FIELD: copy_request_options(self._options)}
        )


def _validate_endpoint(endpoint: EndpointLike) -> None:
    """Reject unsupported endpoint values without materializing a resource."""
    if not isinstance(endpoint, (ExternalDependency, ExternalDependencyReference, MockLLMEndpoint)):
        raise TypeError("endpoint must be an external dependency or MockLLMEndpoint")


class LLMTelemetryDict(TypedDict, total=False):
    """Wire format for LLMTelemetry model dumps with omitted None values."""

    endpoint: str
    model: str
    token_input: int
    token_output: int


def resolve_endpoint(endpoint: EndpointLike) -> LLMEndpoint | MockLLMEndpoint:
    """Materialize and validate a language-model endpoint dependency."""
    if isinstance(endpoint, (ExternalDependency, ExternalDependencyReference)):
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
    """Materialize and validate a transcription endpoint dependency."""
    if isinstance(endpoint, (ExternalDependency, ExternalDependencyReference)):
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
