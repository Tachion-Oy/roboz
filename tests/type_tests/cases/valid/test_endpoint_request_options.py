from roboz import Agent, Ctx, ExternalDependency, ExternalDependencyReference
from roboz.llm import EndpointLike, with_openrouter_policy
from typing import assert_type

from roboz.llm import LLMEndpoint, with_request_options
from roboz.tooling import ExternalDependencyKind, LazyExternalDependency


endpoint = LLMEndpoint(client=object(), api_name="test", model_name="model")
configured = with_request_options(
    endpoint,
    extra_body={"reasoning": {"effort": "high"}},
)
assert_type(configured, LLMEndpoint)


lazy_endpoint: LazyExternalDependency[LLMEndpoint] = LazyExternalDependency(
    dependency_id_value=endpoint.dependency_id,
    dependency_kind=ExternalDependencyKind.MODEL_ENDPOINT,
    metadata=endpoint.redacted_metadata(),
    resolver=lambda: endpoint,
)
configured_lazy = with_request_options(
    lazy_endpoint,
    extra_body={"provider": {"sort": "throughput"}},
)
assert_type(configured_lazy, LazyExternalDependency[LLMEndpoint])




class SelectedEndpoint(ExternalDependencyReference[LLMEndpoint]):
    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        return (lazy_endpoint,)

    def materialize(self) -> LLMEndpoint:
        return lazy_endpoint.materialize()


reference = SelectedEndpoint()
endpoint_like: EndpointLike = reference
assert_type(
    with_request_options(reference, extra_body={}),
    ExternalDependencyReference[LLMEndpoint],
)
assert_type(with_openrouter_policy(reference), ExternalDependencyReference[LLMEndpoint])
assert_type(with_openrouter_policy(lazy_endpoint), LazyExternalDependency[LLMEndpoint])
assert_type(with_openrouter_policy(endpoint), LLMEndpoint)
assert_type(lazy_endpoint.materialize(), LLMEndpoint)
ctx = Ctx(endpoint=reference)
agent = Agent(name="typed_reference", system_prompt="Stop.", agent_endpoint=reference)
