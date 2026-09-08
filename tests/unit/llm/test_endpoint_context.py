from typing import Any, cast

import pytest

from roboz.llm import (
    LLMEndpoint,
    MockLLMEndpoint,
    resolve_endpoint,
    with_request_options,
)
from roboz.dependencies import ExternalDependencyKind, LazyExternalDependency


def test_with_request_options_copies_endpoint_and_body() -> None:
    endpoint = LLMEndpoint(client=object(), api_name="test", model_name="model")
    body = {"provider": {"sort": "throughput"}}

    configured = with_request_options(endpoint, extra_body=body)
    body["provider"]["sort"] = "price"

    assert configured is not endpoint
    assert configured.extra_body == {"provider": {"sort": "throughput"}}
    assert endpoint.extra_body is None
    assert configured.dependency_id == endpoint.dependency_id
    assert configured.redacted_metadata() == endpoint.redacted_metadata()
    assert "extra_body" not in configured.model_dump()


def test_with_request_options_keeps_lazy_endpoint_lazy() -> None:
    resolutions: list[str] = []

    def resolve() -> LLMEndpoint:
        resolutions.append("resolved")
        return LLMEndpoint(
            client=object(),
            api_name="test",
            model_name="model",
            max_context_tokens=42,
        )

    endpoint = LazyExternalDependency(
        dependency_id_value="model:test:model",
        dependency_kind=ExternalDependencyKind.MODEL_ENDPOINT,
        metadata={
            "api_name": "test",
            "model_name": "model",
            "endpoint_type": "llm",
        },
        resolver=resolve,
    )

    configured = with_request_options(
        endpoint, extra_body={"reasoning": {"effort": "low"}}
    )

    assert resolutions == []
    assert configured is not endpoint
    assert configured.dependency_id == endpoint.dependency_id
    assert configured.redacted_metadata() == endpoint.redacted_metadata()
    materialized = configured.materialize()
    assert resolutions == ["resolved"]
    assert materialized.max_context_tokens == 42
    assert materialized.extra_body == {"reasoning": {"effort": "low"}}


def test_with_request_options_creates_independent_configurations() -> None:
    endpoint = LLMEndpoint(client=object(), api_name="test", model_name="model")
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
    endpoint = LLMEndpoint(client=object(), api_name="test", model_name="model")

    with pytest.raises(ValueError, match="framework-owned"):
        with_request_options(endpoint, extra_body={protected_key: True})


@pytest.mark.parametrize("invalid_value", [{"not-json"}, float("nan")])
def test_with_request_options_rejects_non_json_values(invalid_value: object) -> None:
    endpoint = LLMEndpoint(client=object(), api_name="test", model_name="model")

    with pytest.raises(ValueError, match="extra_body"):
        with_request_options(endpoint, extra_body={"provider": invalid_value})


def test_endpoint_can_be_bound_directly() -> None:
    from roboz import Ctx, Empty, Message, factory

    @factory
    def use_endpoint(input: Empty, messages: list[Message], ctx: Ctx) -> Empty:
        assert resolve_endpoint(ctx.endpoint) is endpoint
        return input

    endpoint = LLMEndpoint(client=object(), api_name="test", model_name="model")
    bound = use_endpoint(Ctx(endpoint=endpoint))
    assert bound.dependencies == (endpoint,)
    bound(Empty(), [])


def test_mock_endpoint_does_not_declare_external_resources() -> None:
    from roboz import Ctx, Empty, Message, factory

    @factory
    def use_endpoint(input: Empty, messages: list[Message], ctx: Ctx) -> Empty:
        assert resolve_endpoint(ctx.endpoint) is endpoint
        return input

    endpoint = MockLLMEndpoint([])
    bound = use_endpoint(Ctx(endpoint=endpoint))
    assert bound.external_dependencies == ()
    bound(Empty(), [])


def test_resolve_endpoint_rejects_non_endpoint_resource() -> None:
    with pytest.raises(TypeError, match="endpoint"):
        resolve_endpoint(cast(Any, object()))


def test_resolve_endpoint_is_a_public_consumer_operation() -> None:
    endpoint = MockLLMEndpoint([])

    assert resolve_endpoint(endpoint) is endpoint
