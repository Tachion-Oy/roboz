"""Typed per-use OpenRouter routing and reasoning policy."""

from collections.abc import Sequence
from typing import Final, Literal, overload

from roboz.llm.binding import with_request_options
from roboz.llm.endpoints import JSONValue, LLMEndpoint, LLMEndpointRoute, RequestOptions

type OpenRouterReasoningEffort = Literal["low", "high", "max"]
type OpenRouterSort = Literal["throughput"]

OPENROUTER_PROVIDER_FIELD: Final[str] = "provider"
OPENROUTER_PROVIDER_SORT_FIELD: Final[str] = "sort"
OPENROUTER_REQUIRE_PARAMETERS_FIELD: Final[str] = "require_parameters"
OPENROUTER_IGNORE_PROVIDERS_FIELD: Final[str] = "ignore"
OPENROUTER_REASONING_FIELD: Final[str] = "reasoning"
OPENROUTER_REASONING_EFFORT_FIELD: Final[str] = "effort"

OPENROUTER_THROUGHPUT_SORT: Final[OpenRouterSort] = "throughput"
OPENROUTER_REQUIRE_SUPPORTED_PARAMETERS: Final[bool] = True
OPENROUTER_IGNORED_PROVIDERS: Final[tuple[str, ...]] = ()


def openrouter_request_options(
    *,
    reasoning_effort: OpenRouterReasoningEffort | None = None,
    ignored_providers: Sequence[str] = OPENROUTER_IGNORED_PROVIDERS,
) -> RequestOptions:
    """Build the request body replacing OpenRouter's former ``:nitro`` route.

    Throughput sorting plus required parameter support is the request-level
    equivalent of selecting the old URL/model suffix. Reasoning effort is
    attached independently for each endpoint use, while catalog model names and
    dependency identities remain canonical.
    """
    provider: dict[str, JSONValue] = {
        OPENROUTER_PROVIDER_SORT_FIELD: OPENROUTER_THROUGHPUT_SORT,
        OPENROUTER_REQUIRE_PARAMETERS_FIELD: (OPENROUTER_REQUIRE_SUPPORTED_PARAMETERS),
    }
    if ignored_providers:
        provider[OPENROUTER_IGNORE_PROVIDERS_FIELD] = list(ignored_providers)
    extra_body: RequestOptions = {OPENROUTER_PROVIDER_FIELD: provider}
    if reasoning_effort is not None:
        extra_body[OPENROUTER_REASONING_FIELD] = {
            OPENROUTER_REASONING_EFFORT_FIELD: reasoning_effort
        }
    return extra_body


@overload
def with_openrouter_policy(
    endpoint: LLMEndpoint,
    *,
    reasoning_effort: OpenRouterReasoningEffort | None = None,
    ignored_providers: Sequence[str] = OPENROUTER_IGNORED_PROVIDERS,
) -> LLMEndpoint: ...


@overload
def with_openrouter_policy(
    endpoint: LLMEndpointRoute[LLMEndpoint],
    *,
    reasoning_effort: OpenRouterReasoningEffort | None = None,
    ignored_providers: Sequence[str] = OPENROUTER_IGNORED_PROVIDERS,
) -> LLMEndpointRoute[LLMEndpoint]: ...


def with_openrouter_policy(
    endpoint: LLMEndpoint | LLMEndpointRoute[LLMEndpoint],
    *,
    reasoning_effort: OpenRouterReasoningEffort | None = None,
    ignored_providers: Sequence[str] = OPENROUTER_IGNORED_PROVIDERS,
) -> LLMEndpoint | LLMEndpointRoute[LLMEndpoint]:
    """Copy an endpoint with OpenRouter policy while retaining its identity."""
    return with_request_options(
        endpoint,
        extra_body=openrouter_request_options(
            reasoning_effort=reasoning_effort,
            ignored_providers=ignored_providers,
        ),
    )


__all__ = [
    "OPENROUTER_IGNORED_PROVIDERS",
    "OPENROUTER_REQUIRE_SUPPORTED_PARAMETERS",
    "OPENROUTER_THROUGHPUT_SORT",
    "OpenRouterReasoningEffort",
    "OpenRouterSort",
    "openrouter_request_options",
    "with_openrouter_policy",
]
