from typing import assert_type

from roboz.dependencies import DependencyRoute, LazyExternalDependency
from roboz.llm import EndpointLike, LLMEndpoint, ModelSelector
from roboz.llm.endpoints import ModelSelector as EndpointModelSelector


def select_model(model: LazyExternalDependency[LLMEndpoint]) -> EndpointLike:
    selector: EndpointModelSelector = ModelSelector({"Model": model}, default=model)
    assert_type(selector.selected_endpoint, LazyExternalDependency[LLMEndpoint])
    assert_type(selector.endpoint(model.dependency_id), LazyExternalDependency[LLMEndpoint])
    return DependencyRoute(lambda: selector.selected_endpoint)
