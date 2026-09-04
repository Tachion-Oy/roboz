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
