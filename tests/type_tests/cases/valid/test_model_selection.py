from typing import assert_type
from roboz.llm import EndpointLike, LLMEndpoint, LLMEndpointRoute, ModelSelector
from roboz.llm.endpoints import ModelSelector as EndpointModelSelector


def select_model(model: LLMEndpoint) -> EndpointLike:
    selector: EndpointModelSelector = ModelSelector({"Model": model}, default=model)
    assert_type(selector.selected_endpoint, LLMEndpoint)
    assert_type(selector.endpoint(model.dependency_id), LLMEndpoint)
    return LLMEndpointRoute(lambda: selector.selected_endpoint)
