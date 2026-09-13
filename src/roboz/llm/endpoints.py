"""Language-model endpoint contracts, selection, and deterministic test endpoints."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Lock
from typing import Annotated, Any, Final, Literal, Self, cast

from pydantic import BaseModel, Field, WithJsonSchema, field_validator

from roboz.models import Message, Role
from roboz.tooling.context import HasExternalDependencies, Materializable
from roboz.dependencies import ExternalDependency, ExternalDependencyKind
from roboz.llm.openai_compatible import (
    OpenAICompatibleChatClient,
    OpenAICompatibleModelsClient,
    OpenAICompatibleTranscriptionClient,
)

type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)
type RequestOptions = dict[str, JSONValue]

_EXTRA_BODY_FIELD: Final[str] = "extra_body"
_FRAMEWORK_OWNED_REQUEST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "messages",
        "model",
        "response_format",
        "stream",
        "stream_options",
        "temperature",
    }
)


def copy_request_options(extra_body: Mapping[str, object]) -> RequestOptions:
    """Validate and defensively copy provider-specific request options."""
    protected = _FRAMEWORK_OWNED_REQUEST_KEYS.intersection(extra_body)
    if protected:
        keys = ", ".join(sorted(protected))
        raise ValueError(f"extra_body cannot override framework-owned keys: {keys}")
    try:
        return cast(
            RequestOptions,
            json.loads(json.dumps(dict(extra_body), allow_nan=False)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("extra_body must be JSON-compatible") from exc


def _check_openai_compatible_model(
    client: OpenAICompatibleModelsClient, model_name: str
) -> bool:
    """Query an OpenAI-compatible client's model listing without generating output.

    Match the configured model or its canonical name before a route suffix.
    Preserve the existing model-discovery probe's ten-second request timeout.
    Provider errors propagate; malformed model listings raise ``TypeError``.
    """
    response = client.models.list(timeout=10.0)
    data = (
        response.get("data")
        if isinstance(response, dict)
        else getattr(response, "data", None)
    )
    if not isinstance(data, (list, tuple)):
        raise TypeError("model discovery response must contain a data list")
    model_ids: set[str] = set()
    for item in data:
        model_id = (
            item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        )
        if not isinstance(model_id, str):
            raise TypeError("model discovery entries must have string IDs")
        model_ids.add(model_id)
    return model_name in model_ids or model_name.split(":", 1)[0] in model_ids


class LLMPricing(BaseModel):
    """Represents the pricing of an LLM per million tokens."""

    input_price_per_million_tokens: float | None = Field(
        default=None, ge=0, description="Cost for 1M input tokens in USD."
    )
    output_price_per_million_tokens: float | None = Field(
        default=None, ge=0, description="Cost for 1M output tokens in USD."
    )
    cached_input_price_per_million_tokens: float | None = Field(
        default=None,
        ge=0,
        description="Cost for 1M cached input tokens in USD, if applicable.",
    )
    transcription_price_per_hour: float | None = Field(
        default=None,
        ge=0,
        description="Cost for 1M cached input tokens in USD, if applicable.",
    )


def role_to_user_mapper(
    messages: list[Message], mapped_roles: set[Role] | None = None
) -> list[Message]:
    """Convert all designated roles to user messages."""
    if mapped_roles is None:
        mapped_roles = {Role.ERROR}
    renamed_messages: list[Message] = []
    for m in messages:
        if m.role in mapped_roles:
            renamed_messages.append(Message(role=Role.USER, content=m.content))
            continue
        renamed_messages.append(Message(role=m.role, content=m.content))
    return renamed_messages


class LLMEndpoint(BaseModel, ExternalDependency):
    """Configured model served by a synchronous OpenAI-compatible chat client."""

    model_config = {"arbitrary_types_allowed": True}

    client: Annotated[OpenAICompatibleChatClient, WithJsonSchema({})]
    model_name: str = Field(
        ..., description="The specific model identifier, e.g., 'gpt-4-turbo'."
    )
    api_name: str = Field(..., description="A unique name for this API configuration.")
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Controls randomness. Lower is more deterministic.",
    )
    message_mapper: Callable[[list[Message]], list[Message]] = Field(
        default=role_to_user_mapper,
        description="Allows modification of all messages before they are passed to the llm API.",
    )
    language: str | None = Field(default=None)

    max_context_tokens: int = Field(
        default=128_000,
        gt=0,
        description=(
            "Context window size for this model or route. "
            "Used for approximate usage vs. limit (e.g. token wellness)."
        ),
    )
    prices: LLMPricing | None = Field(default=None)
    output_format: Literal["text", "json"] = Field(
        default="text", description="Specify 'json' for JSON mode."
    )
    stream: bool = Field(
        default=True,
        description="Whether this endpoint supports streamed chat completions.",
    )
    extra_body: RequestOptions | None = Field(
        default=None,
        exclude=True,
        description="Provider-specific request options forwarded to the LLM SDK.",
    )

    rate_limit_error: type[Exception] = Field(
        default=Exception, description="Client's rate limit error."
    )
    context_length_error: type[Exception] = Field(
        default=Exception, description="Client's error for context length issues."
    )

    @field_validator(_EXTRA_BODY_FIELD, mode="before")
    @classmethod
    def _validate_extra_body(cls, value: object) -> RequestOptions | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TypeError("extra_body must be a mapping")
        return copy_request_options(value)

    @property
    def dependency_id(self) -> str:
        """Return the endpoint's namespace-qualified model identity."""
        return f"model:{self.api_name}:{self.model_name}"

    @property
    def kind(self) -> ExternalDependencyKind:
        """Return the model-endpoint resource category."""
        return ExternalDependencyKind.MODEL_ENDPOINT

    def materialize(self) -> Self:
        """Initialize a deferred client now and return this same endpoint.

        Factory invocation calls this automatically for a direct endpoint
        context. Explicit calls allow early credential loading and SDK client
        construction without checking availability or making a model request.
        Already constructed clients are preserved; initialization errors propagate.
        """
        if isinstance(self.client, Materializable):
            self.client.materialize()
        return self

    def check(self) -> bool:
        """Confirm this model appears in an OpenAI-compatible model listing.

        Make a model-discovery request with a ten-second timeout; generate no
        completion or transcription. Return ``False`` for an absent model.
        Authentication, transport, and malformed-response errors propagate.
        """
        return _check_openai_compatible_model(self.client, self.model_name)

    def redacted_metadata(self) -> Mapping[str, str]:
        """Return safe model endpoint metadata for inspection."""
        return {
            "api_name": self.api_name,
            "model_name": self.model_name,
            "endpoint_type": "llm",
        }


class ModelSelector:
    """Select configured model endpoints by stable identity."""

    def __init__(
        self,
        models: Mapping[str, LLMEndpoint],
        *,
        default: LLMEndpoint,
    ) -> None:
        """Copy the catalog and validate identities and the initial selection."""
        self.models = dict(models)
        self._models_by_id = {
            endpoint.dependency_id: endpoint for endpoint in self.models.values()
        }
        if len(self._models_by_id) != len(models):
            raise ValueError("selectable model ids must be unique")
        if default.dependency_id not in self._models_by_id:
            raise ValueError("default model must be selectable")
        self._selected_model_id = default.dependency_id
        self._lock = Lock()

    @property
    def selected_model_id(self) -> str:
        """Return the current selection under the selector lock."""
        with self._lock:
            return self._selected_model_id

    @property
    def selected_endpoint(self) -> LLMEndpoint:
        """Return the selected endpoint without copying it or calling its client."""
        with self._lock:
            return self._models_by_id[self._selected_model_id]

    def endpoint(self, model_id: str) -> LLMEndpoint:
        """Look up a configured endpoint without changing the selection."""
        try:
            return self._models_by_id[model_id]
        except KeyError:
            raise KeyError(model_id) from None

    def select(self, model_id: str) -> None:
        """Change the selection atomically; reject unknown identities."""
        with self._lock:
            if model_id not in self._models_by_id:
                raise KeyError(model_id)
            self._selected_model_id = model_id


class MockLLMEndpoint:
    """Returns pre-scripted JSON responses instead of calling the API. Used for tests."""

    def __init__(
        self,
        responses: list[dict[str, Any] | Exception],
        *,
        max_context_tokens: int = 128_000,
        api_name: str = "mock",
        model_name: str = "mock",
    ):
        """Initialize a deterministic endpoint from scripted responses."""
        self.mock_responses: list[str | Exception] = [
            r if isinstance(r, Exception) else json.dumps(r) for r in responses
        ]
        self.max_context_tokens = max_context_tokens
        self.api_name = api_name
        self.model_name = model_name
        self.rate_limit_error: type[Exception] = Exception
        self.context_length_error: type[Exception] = Exception

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report no external resources for this scripted context."""
        return ()


class MockProviderError(Exception):
    """Scripted provider error for mock endpoints used in tests."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Initialize a scripted provider error with an optional status code."""
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class LLMEndpointRoute[TEndpoint: LLMEndpoint | MockLLMEndpoint](
    HasExternalDependencies
):
    """Follow a caller-owned model selection without caching or constructing clients.

    The getter must return an existing endpoint without external work. Inspection
    reports that endpoint; each operation resolves the current selection once.
    An in-flight provider call retains its resolved endpoint when selection changes.
    """

    get_endpoint: Callable[[], TEndpoint]
    _extra_body: RequestOptions | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate the getter and detach request policy without selecting a model."""
        if not callable(self.get_endpoint):
            raise TypeError("get_endpoint must be callable")
        if self._extra_body is not None:
            object.__setattr__(
                self, "_extra_body", copy_request_options(self._extra_body)
            )

    def _selected_endpoint(self) -> TEndpoint:
        """Validate the getter's current result without initializing its client."""
        endpoint = self.get_endpoint()
        if not isinstance(endpoint, (LLMEndpoint, MockLLMEndpoint)):
            raise TypeError(
                "get_endpoint must return an LLMEndpoint or MockLLMEndpoint"
            )
        return endpoint

    def resolve(self) -> TEndpoint:
        """Return the selected endpoint with a fresh copy of any per-route policy."""
        endpoint = self._selected_endpoint()
        if self._extra_body is None:
            return endpoint
        if not isinstance(endpoint, LLMEndpoint):
            raise TypeError("request options require an LLMEndpoint")
        return endpoint.model_copy(
            update={"extra_body": copy_request_options(self._extra_body)}
        )

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report the selected resource without copying it or initializing clients."""
        return self._selected_endpoint().external_dependencies()

    def materialize(self) -> Self:
        """Initialize the currently selected client and retain this live route."""
        endpoint = self._selected_endpoint()
        if isinstance(endpoint, Materializable):
            endpoint.materialize()
        return self


EndpointLike = (
    LLMEndpoint
    | MockLLMEndpoint
    | LLMEndpointRoute[LLMEndpoint]
    | LLMEndpointRoute[MockLLMEndpoint]
    | LLMEndpointRoute[LLMEndpoint | MockLLMEndpoint]
)


class TranscriptionEndpoint(BaseModel, ExternalDependency):
    """Configured model served by a synchronous OpenAI-compatible audio client."""

    model_config = {"arbitrary_types_allowed": True}

    client: Annotated[OpenAICompatibleTranscriptionClient, WithJsonSchema({})]
    model_name: str = Field(
        ..., description="The provider's speech-to-text model identifier."
    )
    api_name: str = Field(..., description="A unique name for this API configuration.")
    language: str | None = Field(default=None)
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Controls transcription randomness. Lower is more deterministic.",
    )
    prompt: str | None = Field(default=None)
    prices: LLMPricing | None = Field(default=None)

    @property
    def dependency_id(self) -> str:
        """Return the endpoint's namespace-qualified model identity."""
        return f"model:{self.api_name}:{self.model_name}"

    @property
    def kind(self) -> ExternalDependencyKind:
        """Return the model-endpoint resource category."""
        return ExternalDependencyKind.MODEL_ENDPOINT

    def materialize(self) -> Self:
        """Initialize a deferred client now and return this same endpoint.

        Factory invocation calls this automatically for a direct endpoint
        context. Explicit calls allow early credential loading and SDK client
        construction without checking availability or making a model request.
        Already constructed clients are preserved; initialization errors propagate.
        """
        if isinstance(self.client, Materializable):
            self.client.materialize()
        return self

    def check(self) -> bool:
        """Confirm this model appears in an OpenAI-compatible model listing.

        Make a model-discovery request with a ten-second timeout; generate no
        completion or transcription. Return ``False`` for an absent model.
        Authentication, transport, and malformed-response errors propagate.
        """
        return _check_openai_compatible_model(self.client, self.model_name)

    def redacted_metadata(self) -> Mapping[str, str]:
        """Return safe transcription endpoint metadata for inspection."""
        return {
            "api_name": self.api_name,
            "model_name": self.model_name,
            "endpoint_type": "transcription",
        }


class MockTranscriptionEndpoint:
    """Returns pre-scripted transcripts instead of calling a provider API."""

    def __init__(
        self,
        responses: list[str],
        *,
        api_name: str = "mock",
        model_name: str = "mock",
    ):
        """Initialize a deterministic endpoint from scripted transcripts."""
        self.mock_responses = responses
        self.api_name = api_name
        self.model_name = model_name

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report no external resources for this scripted context."""
        return ()


TranscriptionEndpointLike = TranscriptionEndpoint | MockTranscriptionEndpoint
