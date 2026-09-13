from typing import assert_type
from roboz.llm import LLMEndpoint, LLMEndpointRoute, with_openrouter_policy


def configure(endpoint: LLMEndpoint) -> None:
    configured: LLMEndpoint = with_openrouter_policy(endpoint, reasoning_effort="high")
    route = LLMEndpointRoute(lambda: endpoint)
    configured_route: LLMEndpointRoute[LLMEndpoint] = with_openrouter_policy(
        route, reasoning_effort="low"
    )

    assert_type(configured, LLMEndpoint)
    assert_type(configured_route, LLMEndpointRoute[LLMEndpoint])
