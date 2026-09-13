from typing import assert_type

from roboz import Agent, Empty, Factory, Message, Str, Tool, factory
from roboz.dependencies import ExternalDependency
from roboz.llm import (
    EndpointLike,
    LLMEndpoint,
    LLMEndpointRoute,
    MockLLMEndpoint,
    with_request_options,
    with_openrouter_policy,
    resolve_endpoint,
)


@factory
def model_name(input: Empty, messages: list[Message], ctx: EndpointLike) -> Str:
    return Str(value=resolve_endpoint(ctx).model_name)


def configure(endpoint: LLMEndpoint) -> None:
    route = LLMEndpointRoute(lambda: endpoint)
    assert_type(route, LLMEndpointRoute[LLMEndpoint])
    assert_type(route.resolve(), LLMEndpoint)
    assert_type(route.materialize(), LLMEndpointRoute[LLMEndpoint])
    assert_type(route.external_dependencies(), tuple[ExternalDependency, ...])
    assert_type(with_request_options(endpoint, extra_body={}), LLMEndpoint)
    assert_type(
        with_request_options(route, extra_body={}), LLMEndpointRoute[LLMEndpoint]
    )
    assert_type(with_openrouter_policy(route), LLMEndpointRoute[LLMEndpoint])
    assert_type(with_openrouter_policy(endpoint), LLMEndpoint)
    assert_type(model_name, Factory[Empty, Str, EndpointLike])
    assert_type(model_name(route), Tool[Empty, Str])
    assert_type(model_name(endpoint), Tool[Empty, Str])
    Agent(name="typed_reference", system_prompt="Stop.", agent_endpoint=route)
    mock = LLMEndpointRoute(lambda: MockLLMEndpoint([]))
    assert_type(mock.resolve(), MockLLMEndpoint)
    assert_type(model_name(mock), Tool[Empty, Str])
