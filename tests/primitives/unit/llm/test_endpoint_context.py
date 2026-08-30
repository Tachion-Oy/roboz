from typing import Any, cast

import pytest

from roboz.llm import (
    LLMEndpoint,
    MockLLMEndpoint,
    bind_endpoint,
    endpoint_resource,
    resolve_endpoint,
)
from roboz.tooling.dependencies import ToolDependency


def test_bind_endpoint_wraps_external_endpoint() -> None:
    endpoint = LLMEndpoint(client=object(), api_name="test", model_name="model")

    binding = bind_endpoint(endpoint)

    assert isinstance(binding, ToolDependency)
    assert binding.resource is endpoint
    assert endpoint_resource(binding) is endpoint


def test_bind_endpoint_leaves_mock_endpoint_unwrapped() -> None:
    endpoint = MockLLMEndpoint([])

    binding = bind_endpoint(endpoint)

    assert binding is endpoint
    assert endpoint_resource(binding) is endpoint


def test_bind_endpoint_rejects_non_endpoint_resource() -> None:
    with pytest.raises(TypeError, match="endpoint must be an external dependency"):
        bind_endpoint(cast(Any, object()))


def test_resolve_endpoint_is_a_public_consumer_operation() -> None:
    endpoint = MockLLMEndpoint([])

    assert resolve_endpoint(endpoint) is endpoint
