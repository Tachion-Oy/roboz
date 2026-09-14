"""Explicit availability checks stay separate from context binding and inspection."""

from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from roboz.models import Message, Str
from roboz import factory
from roboz.dependencies import (
    ExecutableDependency,
    ExternalDependency,
    ExternalDependencyKind,
)
from roboz.llm import LLMEndpoint, TranscriptionEndpoint


class MissingCheck(ExternalDependency):
    @property
    def dependency_id(self) -> str:
        return "test:missing-check"

    @property
    def kind(self) -> ExternalDependencyKind:
        return ExternalDependencyKind.NETWORK_SERVICE

    def redacted_metadata(self) -> Mapping[str, str]:
        return {}


def test_resources_must_implement_their_availability_check():
    with pytest.raises(TypeError, match="check"):
        MissingCheck()


def test_executable_checks_current_resolution_without_caching(monkeypatch):
    resolutions = []
    available = False

    def which(name):
        resolutions.append(name)
        return "/configured/program" if available else None

    monkeypatch.setattr("shutil.which", which)
    program = ExecutableDependency("program")
    assert program.check() is False
    available = True
    assert program.check() is True
    assert resolutions == ["program", "program"]


class ModelListing:
    def __init__(self, response):
        self.response = response
        self.timeouts = []

    def list(self, *, timeout):
        self.timeouts.append(timeout)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def discovery_client(models):
    def forbidden(**request):
        raise AssertionError("availability checks must not generate output")

    return SimpleNamespace(
        models=models,
        close=lambda: None,
        chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden)),
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=forbidden)),
    )


@pytest.mark.parametrize("endpoint_class", [LLMEndpoint, TranscriptionEndpoint])
def test_endpoint_checks_model_discovery_without_generating_or_caching(endpoint_class):
    models = ModelListing(SimpleNamespace(data=[SimpleNamespace(id="model")]))
    client = discovery_client(models)
    endpoint = endpoint_class(client=client, api_name="test", model_name="model")
    assert models.timeouts == []
    assert endpoint.check() is True
    models.response = {"data": [{"id": "another-model"}]}
    assert endpoint.check() is False
    assert models.timeouts == [10.0, 10.0]


def test_endpoint_check_recognizes_the_existing_canonical_route_name():
    models = ModelListing({"data": [{"id": "provider/model"}]})
    endpoint = LLMEndpoint(
        client=discovery_client(models),
        api_name="test",
        model_name="provider/model:nitro",
    )
    assert endpoint.check() is True


@pytest.mark.parametrize(
    "response",
    [{}, {"data": None}, {"data": "model"}, {"data": [{}]}, {"data": [{"id": 1}]}],
)
def test_endpoint_check_rejects_malformed_discovery_responses(response):
    endpoint = LLMEndpoint(
        client=discovery_client(ModelListing(response)),
        api_name="test",
        model_name="model",
    )
    with pytest.raises(TypeError, match="model discovery"):
        endpoint.check()


def test_endpoint_check_propagates_provider_errors_unchanged():
    error = ConnectionError("scripted provider failure")
    models = ModelListing(error)
    endpoint = LLMEndpoint(
        client=discovery_client(models), api_name="test", model_name="model"
    )
    with pytest.raises(ConnectionError) as failure:
        endpoint.check()
    assert failure.value is error


def test_binding_copying_and_inspection_do_not_check_endpoint_availability():
    models = ModelListing(AssertionError("availability check was not requested"))
    endpoint = LLMEndpoint(
        client=discovery_client(models), api_name="test", model_name="model"
    )

    @factory
    def describe_model(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
        return Str(value=ctx.model_name)

    bound = describe_model(endpoint)
    copied = bound.copy()
    assert bound.external_dependencies()[0] is endpoint
    assert copied.external_dependencies()[0] is endpoint
    assert bound(Str(value="describe"), []).value == "model"
    assert models.timeouts == []


@pytest.mark.parametrize("endpoint_class", [LLMEndpoint, TranscriptionEndpoint])
def test_openai_sdk_check_uses_authenticated_model_discovery(endpoint_class):
    import httpx
    from openai import OpenAI

    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer scripted-test-key"
        assert set(request.extensions["timeout"].values()) == {10.0}
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "model", "object": "model", "created": 0, "owned_by": "test"}
                ],
            },
        )

    with OpenAI(
        api_key="scripted-test-key",
        base_url="https://provider.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    ) as client:
        endpoint = endpoint_class(client=client, api_name="test", model_name="model")
        assert endpoint.external_dependencies()[0] is endpoint
        assert requests == []
        assert endpoint.check() is True
        assert len(requests) == 1
