from roboz.llm import LLMEndpoint, with_openrouter_policy
from roboz.tooling import ExternalDependencyKind, LazyExternalDependency

endpoint = LLMEndpoint(client=object(), model_name="model", api_name="openrouter")
configured: LLMEndpoint = with_openrouter_policy(
    endpoint,
    reasoning_effort="high",
)

lazy = LazyExternalDependency(
    dependency_id_value="model:openrouter:model",
    dependency_kind=ExternalDependencyKind.MODEL_ENDPOINT,
    metadata={"api_name": "openrouter", "model_name": "model"},
    resolver=lambda: endpoint,
)
configured_lazy: LazyExternalDependency[LLMEndpoint] = with_openrouter_policy(
    lazy,
    reasoning_effort="low",
)
