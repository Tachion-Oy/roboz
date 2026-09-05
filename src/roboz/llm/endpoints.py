"""Language-model endpoint contracts and deterministic test endpoint."""

import json
from collections.abc import Callable, Mapping
from typing import Any, Final, Literal, cast

from pydantic import BaseModel, Field, field_validator

from roboz.models import Message, Role
from roboz.tooling.dependencies import LazyExternalDependency, ModelEndpointDependency

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


class LLMEndpoint(BaseModel, ModelEndpointDependency):
    """Specification for an LLM endpoint, containing all necessary details for a validated API call."""

    model_config = {"arbitrary_types_allowed": True}

    client: Any
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

    def redacted_metadata(self) -> Mapping[str, str]:
        """Return safe model endpoint metadata for inspection."""
        return {
            "api_name": self.api_name,
            "model_name": self.model_name,
            "endpoint_type": "llm",
        }


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


class MockProviderError(Exception):
    """Scripted provider error for mock endpoints used in tests."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Initialize a scripted provider error with an optional status code."""
        super().__init__(message)
        self.status_code = status_code


EndpointLike = LLMEndpoint | MockLLMEndpoint | LazyExternalDependency[LLMEndpoint]


class TranscriptionEndpoint(BaseModel, ModelEndpointDependency):
    """Specification for an audio transcription endpoint."""

    model_config = {"arbitrary_types_allowed": True}

    client: Any
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


TranscriptionEndpointLike = (
    TranscriptionEndpoint
    | MockTranscriptionEndpoint
    | LazyExternalDependency[TranscriptionEndpoint]
)
