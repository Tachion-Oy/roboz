from typing import Any, cast
from types import SimpleNamespace

import pytest

from roboz.llm import (
    EndpointLike,
    LLMEndpoint,
    MockLLMEndpoint,
    resolve_endpoint,
    with_request_options,
)


def test_with_request_options_copies_endpoint_and_body() -> None:
    endpoint = LLMEndpoint(
        client=SimpleNamespace(chat=object(), models=object(), close=lambda: None),
        api_name="test",
        model_name="model",
    )
    body = {"provider": {"sort": "throughput"}}

    configured = with_request_options(endpoint, extra_body=body)
    body["provider"]["sort"] = "price"

    assert configured is not endpoint
    assert configured.extra_body == {"provider": {"sort": "throughput"}}
    assert endpoint.extra_body is None
    assert configured.dependency_id == endpoint.dependency_id
    assert configured.redacted_metadata() == endpoint.redacted_metadata()
    assert "extra_body" not in configured.model_dump()


def test_with_request_options_keeps_client_deferred():
    resolutions = []
    client = SimpleNamespace(
        chat=object(),
        models=object(),
        close=lambda: None,
        materialize=lambda: resolutions.append("resolved"),
    )
    endpoint = LLMEndpoint(
        client=client, api_name="test", model_name="model", max_context_tokens=42
    )
    configured = with_request_options(
        endpoint, extra_body={"reasoning": {"effort": "low"}}
    )
    assert configured is not endpoint
    assert configured.client is endpoint.client
    assert configured.dependency_id == endpoint.dependency_id
    assert resolve_endpoint(configured) is configured
    assert resolutions == []
    assert configured.materialize() is configured
    assert resolutions == ["resolved"]
    assert configured.extra_body == {"reasoning": {"effort": "low"}}


def test_with_request_options_creates_independent_configurations() -> None:
    endpoint = LLMEndpoint(
        client=SimpleNamespace(chat=object(), models=object(), close=lambda: None),
        api_name="test",
        model_name="model",
    )
    low = with_request_options(endpoint, extra_body={"reasoning": {"effort": "low"}})
    high = with_request_options(endpoint, extra_body={"reasoning": {"effort": "high"}})

    assert low.extra_body == {"reasoning": {"effort": "low"}}
    assert high.extra_body == {"reasoning": {"effort": "high"}}
    assert low.extra_body is not None
    low.extra_body["reasoning"] = {"effort": "max"}
    assert high.extra_body == {"reasoning": {"effort": "high"}}


@pytest.mark.parametrize(
    "protected_key",
    [
        "model",
        "messages",
        "temperature",
        "response_format",
        "stream",
        "stream_options",
    ],
)
def test_with_request_options_rejects_framework_owned_keys(
    protected_key: str,
) -> None:
    endpoint = LLMEndpoint(
        client=SimpleNamespace(chat=object(), models=object(), close=lambda: None),
        api_name="test",
        model_name="model",
    )

    with pytest.raises(ValueError, match="framework-owned"):
        with_request_options(endpoint, extra_body={protected_key: True})


@pytest.mark.parametrize("invalid_value", [{"not-json"}, float("nan")])
def test_with_request_options_rejects_non_json_values(invalid_value: object) -> None:
    endpoint = LLMEndpoint(
        client=SimpleNamespace(chat=object(), models=object(), close=lambda: None),
        api_name="test",
        model_name="model",
    )

    with pytest.raises(ValueError, match="extra_body"):
        with_request_options(endpoint, extra_body={"provider": invalid_value})


def test_endpoint_can_be_bound_directly() -> None:
    from roboz.models import Empty, Message
    from roboz import factory

    @factory
    def use_endpoint(input: Empty, messages: list[Message], ctx: EndpointLike) -> Empty:
        assert resolve_endpoint(ctx) is endpoint
        return input

    endpoint = LLMEndpoint(
        client=SimpleNamespace(chat=object(), models=object(), close=lambda: None),
        api_name="test",
        model_name="model",
    )
    bound = use_endpoint(endpoint)
    assert bound.external_dependencies() == (endpoint,)
    bound(Empty(), [])


def test_mock_endpoint_does_not_declare_external_resources() -> None:
    from roboz.models import Empty, Message
    from roboz import factory

    @factory
    def use_endpoint(input: Empty, messages: list[Message], ctx: EndpointLike) -> Empty:
        assert resolve_endpoint(ctx) is endpoint
        return input

    endpoint = MockLLMEndpoint([])
    bound = use_endpoint(endpoint)
    assert bound.external_dependencies() == ()
    bound(Empty(), [])


def test_resolve_endpoint_rejects_non_endpoint_resource() -> None:
    with pytest.raises(TypeError, match="endpoint"):
        resolve_endpoint(cast(Any, object()))


def test_resolve_endpoint_is_a_public_consumer_operation() -> None:
    endpoint = MockLLMEndpoint([])

    assert resolve_endpoint(endpoint) is endpoint
