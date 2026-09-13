"""Concrete endpoint contexts and calls without agents, catalogs, or credentials."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from roboz import Message, Role, Str, factory
from roboz.dependencies import ExecutableDependency, ExternalDependencyKind
from roboz.llm import (
    EndpointLike,
    LLMEndpoint,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    ModelSelector,
    TranscriptionEndpoint,
    call_llm_api,
    call_transcription_api,
    get_completion,
    resolve_endpoint,
    with_openrouter_policy,
    with_request_options,
)
from roboz.llm.binding import resolve_transcription_endpoint


class ScriptedClient:
    """Record calls at the SDK boundary and return deterministic responses."""

    def __init__(self):
        self.models = SimpleNamespace(list=self.list_models)
        self.chat_requests = []
        self.audio_requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.complete))
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=self.transcribe)
        )

    def close(self):
        pass

    def list_models(self, *, timeout):
        raise AssertionError("model discovery was not requested")

    def complete(self, **request):
        self.chat_requests.append(request)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"value": "configured endpoint output"}'
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    def transcribe(self, **request):
        self.audio_requests.append(request)
        return SimpleNamespace(text=" scripted transcript ")


@factory
def summarize(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    """Summarize the supplied text using the available conversation."""
    completion = get_completion(
        messages=messages + [Message(role=Role.USER, content=input.value)],
        LlmOutputModel=Str,
        call_llm_api=lambda current: call_llm_api(ctx, current),
    )
    return Str.model_validate(completion)


def test_concrete_endpoint_drives_factory_execution_and_inspection():
    client = ScriptedClient()
    endpoint = LLMEndpoint(
        client=client,
        api_name="scripted",
        model_name="chat",
        stream=False,
    )
    bound = summarize(endpoint)
    copied = bound.copy()
    assert bound.external_dependencies()[0] is endpoint
    assert copied.external_dependencies()[0] is endpoint
    assert client.chat_requests == []
    assert resolve_endpoint(endpoint) is endpoint
    assert endpoint.client is client

    result = bound(Str(value="Summarize this."), [])
    assert result.value == "configured endpoint output"
    assert len(client.chat_requests) == 1
    assert client.chat_requests[0]["model"] == "chat"
    assert client.chat_requests[0]["messages"] == [
        {"role": "user", "content": "Summarize this."},
    ]


def test_transcription_endpoint_can_be_a_direct_context():
    @factory
    def transcribe_sample(
        input: Str, messages: list[Message], ctx: TranscriptionEndpoint
    ) -> Str:
        return Str(
            value=call_transcription_api(
                ctx,
                b"sample",
                filename=input.value,
                content_type="audio/wav",
            )
        )

    client = ScriptedClient()
    endpoint = TranscriptionEndpoint(
        client=client,
        api_name="scripted",
        model_name="speech",
        language="en",
    )
    bound = transcribe_sample(endpoint)
    assert bound.external_dependencies()[0] is endpoint
    assert resolve_transcription_endpoint(endpoint) is endpoint
    assert client.audio_requests == []
    assert bound(Str(value="sample.wav"), []).value == "scripted transcript"
    assert client.audio_requests == [
        {
            "file": ("sample.wav", b"sample", "audio/wav"),
            "model": "speech",
            "temperature": 0.0,
            "language": "en",
        }
    ]


@pytest.mark.parametrize(
    "endpoint_class, endpoint_type",
    [(LLMEndpoint, "llm"), (TranscriptionEndpoint, "transcription")],
)
def test_endpoint_resource_contract_retains_identity_and_safe_metadata(
    endpoint_class,
    endpoint_type,
):
    endpoint = endpoint_class(
        client=ScriptedClient(), api_name="test", model_name="model"
    )
    assert endpoint.dependency_id == "model:test:model"
    assert endpoint.kind is ExternalDependencyKind.MODEL_ENDPOINT
    assert endpoint.redacted_metadata() == {
        "api_name": "test",
        "model_name": "model",
        "endpoint_type": endpoint_type,
    }
    assert endpoint.external_dependencies()[0] is endpoint
    serialized = endpoint.model_dump()
    assert "dependency_id" not in serialized
    assert "kind" not in serialized
    assert endpoint.materialize() is endpoint
    assert endpoint.client is serialized["client"]


def test_endpoint_schema_defaults_and_validation_remain_unchanged():
    endpoint = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="model")
    assert endpoint.temperature == 0.7
    assert endpoint.max_context_tokens == 128_000
    assert endpoint.output_format == "text"
    assert endpoint.stream is True
    assert endpoint.extra_body is None
    with pytest.raises(ValidationError):
        LLMEndpoint(
            client=ScriptedClient(), api_name="test", model_name="model", temperature=3
        )
    with pytest.raises(ValidationError):
        LLMEndpoint(
            client=ScriptedClient(),
            api_name="test",
            model_name="model",
            max_context_tokens=0,
        )


def test_request_policy_copies_options_while_retaining_client_and_resource_identity():
    client = ScriptedClient()
    endpoint = LLMEndpoint(client=client, api_name="test", model_name="model")
    options = {"provider": {"sort": "throughput"}}
    configured = with_request_options(endpoint, extra_body=options)
    options["provider"]["sort"] = "price"
    assert configured is not endpoint
    assert configured.client is client
    assert configured.dependency_id == endpoint.dependency_id
    assert configured.redacted_metadata() == endpoint.redacted_metadata()
    assert configured.extra_body == {"provider": {"sort": "throughput"}}
    assert endpoint.extra_body is None
    assert "extra_body" not in configured.model_dump()
    assert summarize(configured).external_dependencies()[0] is configured

    policy = with_openrouter_policy(endpoint, reasoning_effort="high")
    assert policy.client is client
    assert policy.dependency_id == endpoint.dependency_id
    assert policy.extra_body == {
        "provider": {"sort": "throughput", "require_parameters": True},
        "reasoning": {"effort": "high"},
    }
    assert client.chat_requests == []


@pytest.mark.parametrize(
    "key",
    [
        "model",
        "messages",
        "temperature",
        "response_format",
        "stream",
        "stream_options",
    ],
)
def test_request_policy_still_rejects_framework_owned_fields(key):
    endpoint = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="model")
    with pytest.raises(ValueError, match="framework-owned"):
        with_request_options(endpoint, extra_body={key: True})


@pytest.mark.parametrize("value", [set(), float("nan")])
def test_request_policy_still_rejects_non_json_options(value):
    endpoint = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="model")
    with pytest.raises(ValueError, match="JSON-compatible"):
        with_request_options(endpoint, extra_body={"provider": value})


class FormerReference:
    def materialize(self):
        pytest.fail("endpoint validation must not materialize a reference")


@pytest.mark.parametrize(
    "invalid", [object(), ExecutableDependency("python"), FormerReference()]
)
def test_endpoint_operations_reject_non_endpoints_without_materialization(invalid):
    with pytest.raises(TypeError, match="endpoint"):
        resolve_endpoint(invalid)
    with pytest.raises(TypeError, match="endpoint"):
        resolve_transcription_endpoint(invalid)
    with pytest.raises(TypeError, match="endpoint"):
        with_request_options(invalid, extra_body={})


def test_chat_and_transcription_validation_keep_the_endpoint_families_separate():
    chat = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="chat")
    speech = TranscriptionEndpoint(
        client=ScriptedClient(), api_name="test", model_name="speech"
    )
    with pytest.raises(TypeError, match="LLMEndpoint"):
        resolve_endpoint(speech)
    with pytest.raises(TypeError, match="TranscriptionEndpoint"):
        resolve_transcription_endpoint(chat)


def test_scripted_endpoints_satisfy_context_without_reporting_external_resources():
    @factory
    def echo_completion(input: Str, messages: list[Message], ctx: EndpointLike) -> Str:
        text, _ = call_llm_api(ctx, messages)
        return Str.model_validate_json(text)

    endpoint = MockLLMEndpoint([{"value": "scripted"}])
    bound = echo_completion(endpoint)
    assert bound.external_dependencies() == ()
    assert bound.copy().external_dependencies() == ()
    assert len(endpoint.mock_responses) == 1
    assert bound(Str(value="input"), []).value == "scripted"

    transcription = MockTranscriptionEndpoint(["scripted transcript"])
    assert transcription.external_dependencies() == ()
    assert resolve_transcription_endpoint(transcription) is transcription


def test_selector_returns_the_supplied_concrete_endpoints():
    first = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="first")
    second = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="second")
    selector = ModelSelector({"First": first, "Second": second}, default=first)
    assert selector.selected_endpoint is first
    selector.select(second.dependency_id)
    assert selector.selected_endpoint is second
    assert selector.endpoint(first.dependency_id) is first
    with pytest.raises(KeyError):
        selector.select("model:test:missing")
    assert selector.selected_endpoint is second
