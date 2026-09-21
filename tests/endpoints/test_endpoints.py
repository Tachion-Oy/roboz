import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import openai
import pytest

from roboz import Agent, factory
from roboz.models import Message, Str
from roboz.tools import stop
from roboz.llm import (
    LLMEndpoint,
    TranscriptionEndpoint,
    call_llm_api,
    call_transcription_api,
    with_openrouter_policy,
    with_request_options,
)
from roboz.endpoints.adapters.openai_compatible import (
    OpenAICompatibleAdapter,
    chat_endpoint,
    transcription_endpoint,
)
from roboz.endpoints.inventory import cerebras, groq, openrouter
from roboz.endpoints.specs import ChatModelSpec


def test_service_validation_message_names_only_service_fields():
    adapter = OpenAICompatibleAdapter(api_name="", api_key_env="")
    with pytest.raises(ValueError, match=r"^api_name and api_key_env must be non-empty$"):
        adapter._validate_service_settings()


@pytest.mark.parametrize(
    "provider_class,attribute",
    [
        (openrouter.configured, "z_ai__glm_5_3"),
        (cerebras.configured, "gpt_oss_120b"),
        (groq.configured, "whisper_large_v3_turbo"),
    ],
)
def test_catalogue_instances_keep_independent_configuration(sdk_http, provider_class, attribute):
    clients, requests = sdk_http(lambda _: pytest.fail("unexpected request"))
    first = provider_class(
        api_key="first-key", timeout_s=12, stream=False
    )
    second = provider_class(api_key="second-key", timeout_s=34)
    chat = isinstance(first.models_by_attribute[attribute], ChatModelSpec)
    # Select both before client creation so shared catalogue state cannot hide.
    first_endpoint = getattr(first, attribute)
    second_endpoint = getattr(second, attribute)
    assert not clients
    assert first_endpoint.client is not second_endpoint.client
    assert first_endpoint.client.models is first_endpoint.client.models
    assert second_endpoint.client.models is second_endpoint.client.models
    assert len(clients) == 2 and not requests
    assert clients[0].api_key == "first-key"
    assert clients[1].api_key == "second-key"
    assert clients[0].timeout == 12
    assert clients[1].timeout == 34
    assert getattr(first, attribute) is first_endpoint
    assert getattr(second, attribute) is second_endpoint
    if chat:
        assert first_endpoint.stream is False
        assert second_endpoint.stream is True
    first_endpoint.client.close()
    second_endpoint.client.close()
    assert all(client.is_closed() for client in clients)


def test_inspection_is_lazy_and_credentials_are_redacted(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    endpoint = chat_endpoint(
        api_name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        model="test/model",
        max_context_tokens=4096,
    )
    agent = Agent(
        name="test", system_prompt="Stop.", tools=[stop], agent_endpoint=endpoint
    )
    assert endpoint.dependency_id == "model:openrouter:test/model"
    assert agent.external_dependencies() == (endpoint,)
    assert isinstance(endpoint, LLMEndpoint)
    assert callable(endpoint.materialize)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        endpoint.check()


@pytest.mark.parametrize("stream", [False, True])
def test_sdk_transport_and_policy_through_agent(monkeypatch, stream):
    requests = []

    def reply(request: httpx.Request):
        requests.append(request)
        if stream:
            chunk = {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test/model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": None,
                        "delta": {
                            "content": json.dumps(
                                {"action": "stop", "rationale": "test", "value": "ok"}
                            )
                        },
                    }
                ],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test/model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {"action": "stop", "rationale": "test", "value": "ok"}
                            ),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    real_client = openai.OpenAI
    clients = []

    def client(**kwargs):
        assert kwargs["timeout"] == 12
        assert kwargs["max_retries"] == 0
        result = real_client(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(reply))
        )
        clients.append(result)
        return result

    monkeypatch.setattr(openai, "OpenAI", client)
    options = {"provider": {"sort": "throughput"}}
    endpoint = chat_endpoint(
        api_name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        model="test/model",
        max_context_tokens=4096,
        api_key="test-secret",
        stream=stream,
        timeout_s=12,
        extra_body=options,
    )
    options["provider"]["sort"] = "price"
    assert "test-secret" not in repr(endpoint)
    assert "test-secret" not in json.dumps(dict(endpoint.redacted_metadata()))
    assert not clients
    try:
        configured = with_request_options(
            endpoint, extra_body={"reasoning": {"effort": "low"}}
        )
        assert configured.dependency_id == endpoint.dependency_id
        agent = Agent(
            name="test", system_prompt="Stop.", tools=[stop], agent_endpoint=endpoint
        )
        assert not clients
        assert agent.invoke()[0].value == "ok"
        assert len(clients) == len(requests) == 1
        request = requests[0]
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "test/model"
        assert body["provider"] == {"sort": "throughput"}
        assert body.get("stream", False) is stream
    finally:
        for value in clients:
            value.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": ""},
        {"max_context_tokens": 0},
        {"timeout_s": float("nan")},
        {"timeout_s": 0},
        {"base_url": "file:///tmp/model"},
        {"base_url": "https://user:secret@example.com/v1"},
        {"extra_body": {"model": "override"}},
    ],
)
def test_invalid_settings_fail_before_client_creation(overrides):
    settings = {"model": "test", "max_context_tokens": 4096, **overrides}
    with pytest.raises(ValueError):
        chat_endpoint(**settings)


@pytest.fixture
def sdk_http(monkeypatch):
    real_client = openai.OpenAI
    clients, requests = [], []

    def install(reply):
        def handle(request):
            requests.append(request)
            return reply(request)

        def client(**kwargs):
            value = real_client(
                **kwargs,
                http_client=httpx.Client(transport=httpx.MockTransport(handle)),
            )
            clients.append(value)
            return value

        monkeypatch.setattr(openai, "OpenAI", client)
        return clients, requests

    yield install
    for client in clients:
        client.close()


def test_adapter_reuses_service_settings_for_independent_lazy_endpoints(sdk_http):
    clients, _ = sdk_http(lambda _: pytest.fail("unexpected request"))
    adapter = OpenAICompatibleAdapter(
        api_name="custom",
        base_url="https://models.example.com/v1",
        api_key="explicit-secret",
        api_key_env="UNUSED_API_KEY",
        timeout_s=12,
    )
    chat = adapter.chat_endpoint(
        model="chat",
        max_context_tokens=123,
        stream=False,
        extra_body={"reasoning": {"effort": "low"}},
    )
    audio = adapter.transcription_endpoint(model="audio")
    assert not clients
    assert "explicit-secret" not in repr(adapter)
    assert chat.dependency_id == "model:custom:chat"
    assert audio.dependency_id == "model:custom:audio"

    assert isinstance(chat, LLMEndpoint)
    assert isinstance(audio, TranscriptionEndpoint)
    assert chat.client.models is chat.client.models
    assert audio.client.models is audio.client.models
    assert chat.client is not audio.client
    assert len(clients) == 2
    for client in clients:
        assert str(client.base_url) == "https://models.example.com/v1/"
        assert client.api_key == "explicit-secret"
        assert client.timeout == 12
        assert client.max_retries == 0
    assert chat.stream is False
    assert chat.max_context_tokens == 123
    assert chat.extra_body == {"reasoning": {"effort": "low"}}


def test_adapter_defers_settings_validation_until_endpoint_selection():
    adapter = OpenAICompatibleAdapter(timeout_s=0)
    with pytest.raises(ValueError, match="timeout_s must be finite and positive"):
        adapter.chat_endpoint(model="chat", max_context_tokens=123)
    with pytest.raises(ValueError, match="timeout_s must be finite and positive"):
        adapter.transcription_endpoint(model="audio")


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "provider_class,attribute,model,api_name,base_url,context",
    [
        (
            openrouter.configured,
            "z_ai__glm_5_3",
            "z-ai/glm-5.3",
            "openrouter",
            "https://openrouter.ai/api/v1",
            1_310_720,
        ),
        (
            openrouter.configured,
            "z_ai__glm_5_3_flash",
            "z-ai/glm-5.3-flash",
            "openrouter",
            "https://openrouter.ai/api/v1",
            1_310_720,
        ),
        (
            cerebras.configured,
            "gpt_oss_120b",
            "gpt-oss-120b",
            "cerebras",
            "https://api.cerebras.ai/v1",
            131_072,
        ),
    ],
)
def test_catalogue_chat_transport(
    monkeypatch,
    sdk_http,
    stream,
    provider_class,
    attribute,
    model,
    api_name,
    base_url,
    context,
):
    monkeypatch.setenv(f"{api_name.upper()}_API_KEY", "catalogue-secret")
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def reply(request):
        body = {
            "id": "test",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop"}],
        }
        if stream:
            body["object"] = "chat.completion.chunk"
            body["choices"][0]["delta"] = {"content": "ok"}
            final = {**body, "choices": [], "usage": usage}
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(body)}\n\ndata: {json.dumps(final)}\n\ndata: [DONE]\n\n",
            )
        body.update(object="chat.completion", usage=usage)
        body["choices"][0]["message"] = {"role": "assistant", "content": "ok"}
        return httpx.Response(200, json=body)

    clients, requests = sdk_http(reply)
    provider = provider_class(stream=stream)
    canonical = getattr(provider, attribute)
    endpoint = (
        with_openrouter_policy(canonical, reasoning_effort="low")
        if api_name == "openrouter"
        else canonical
    )
    assert not clients
    deltas = []
    result, telemetry = call_llm_api(
        endpoint,
        [],
        on_delta=deltas.append if stream else None,
    )
    assert result == "ok"
    assert telemetry["token_input"] == 10
    assert telemetry["token_output"] == 5
    assert deltas == (["ok"] if stream else [])
    assert len(requests) == len(clients) == 1
    assert clients[0].timeout == 60
    assert clients[0].max_retries == 0
    assert endpoint.max_context_tokens == context
    assert str(requests[0].url) == f"{base_url}/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer catalogue-secret"
    body = json.loads(requests[0].content)
    assert body["model"] == model
    assert body.get("stream", False) is stream
    if stream:
        assert body["stream_options"] == {"include_usage": True}
    if api_name == "openrouter":
        assert body["provider"] == {"sort": "throughput", "require_parameters": True}
        assert body["reasoning"] == {"effort": "low"}
        memory = with_openrouter_policy(canonical, reasoning_effort="high")
        assert memory.dependency_id == endpoint.dependency_id == canonical.dependency_id
        assert memory.client is endpoint.client
        assert memory.extra_body["reasoning"] == {"effort": "high"}
        assert canonical.extra_body is None
    else:
        assert "provider" not in body and "reasoning" not in body


def test_groq_transcription_transport(monkeypatch, sdk_http):
    from email.parser import BytesParser
    from email.policy import default

    monkeypatch.setenv("GROQ_API_KEY", "groq-secret")
    clients, requests = sdk_http(
        lambda _: httpx.Response(200, json={"text": " spoken words "})
    )
    endpoint = groq.configured(timeout_s=12).whisper_large_v3_turbo
    assert not clients
    assert (
        call_transcription_api(
            endpoint,
            b"audio content",
            filename="voice.webm",
            content_type="audio/webm",
            language="fi",
            prompt="Names",
            temperature=0.1,
        )
        == "spoken words"
    )
    assert len(clients) == len(requests) == 1
    assert clients[0].timeout == 12 and clients[0].max_retries == 0
    assert endpoint.client.audio is clients[0].audio
    request = requests[0]
    assert str(request.url) == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer groq-secret"
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
        + request.content
    )
    fields = {
        part.get_param("name", header="content-disposition"): part
        for part in message.iter_parts()
    }
    assert fields["file"].get_filename() == "voice.webm"
    assert fields["file"].get_content_type() == "audio/webm"
    assert fields["file"].get_payload(decode=True) == b"audio content"
    for name, value in {
        "model": "whisper-large-v3-turbo",
        "language": "fi",
        "prompt": "Names",
        "temperature": "0.1",
    }.items():
        assert fields[name].get_payload(decode=True).decode() == value
    assert endpoint.redacted_metadata() == {
        "api_name": "groq",
        "model_name": "whisper-large-v3-turbo",
        "endpoint_type": "transcription",
    }


@pytest.mark.parametrize("chat", [False, True])
def test_failed_credentials_can_be_retried(monkeypatch, sdk_http, chat):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    clients, _ = sdk_http(lambda _: pytest.fail("unexpected request"))
    endpoint = (
        chat_endpoint(model="custom", max_context_tokens=123)
        if chat
        else transcription_endpoint(model="custom")
    )
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        endpoint.materialize()
    assert clients == []
    monkeypatch.setenv("OPENAI_API_KEY", "later-secret")
    with ThreadPoolExecutor(max_workers=4) as executor:
        resources = list(executor.map(lambda _: endpoint.materialize(), range(8)))
    assert all(resource is resources[0] for resource in resources)
    assert resources[0] is endpoint
    assert endpoint.model_name == "custom"
    assert len(clients) == 1
    assert "later-secret" not in repr(endpoint)


def test_endpoint_validation_fails_before_client_creation(sdk_http):
    clients, _ = sdk_http(lambda _: pytest.fail("unexpected request"))
    with pytest.raises(ValueError):
        chat_endpoint(model="custom", max_context_tokens=1.5, api_key="test")
    assert clients == []


@pytest.mark.parametrize("used", [False, True])
def test_close_is_shared_idempotent_and_does_not_reopen(sdk_http, used):
    clients, requests = sdk_http(lambda _: pytest.fail("unexpected request"))
    endpoint = chat_endpoint(model="custom", max_context_tokens=123, api_key="test")
    configured = with_openrouter_policy(endpoint)
    if used:
        assert endpoint.client.chat is endpoint.client.chat
    configured.client.close()
    endpoint.client.close()
    assert len(clients) == int(used)
    assert all(client.is_closed() for client in clients)
    with pytest.raises(RuntimeError, match="closed"):
        endpoint.check()
    assert len(clients) == int(used) and not requests


@pytest.mark.parametrize("chat", [False, True])
def test_direct_binding_inspection_and_check_use_the_same_endpoint(sdk_http, chat):
    def reply(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer test"
        assert set(request.extensions["timeout"].values()) == {10.0}
        return httpx.Response(200, json={"data": [{"id": "custom"}]})

    clients, requests = sdk_http(reply)
    endpoint = (
        chat_endpoint(model="custom", max_context_tokens=123, api_key="test")
        if chat else transcription_endpoint(model="custom", api_key="test")
    )

    @factory
    def describe(input: Str, messages: list[Message], ctx: LLMEndpoint | TranscriptionEndpoint) -> Str:
        assert ctx is endpoint
        assert len(clients) == 1 and not requests
        return Str(value=ctx.model_name)

    bound = describe(endpoint)
    assert bound.external_dependencies()[0] is endpoint
    assert bound.copy().external_dependencies()[0] is endpoint
    assert clients == requests == []
    assert bound(Str(value="describe"), []).value == "custom"
    assert len(clients) == 1 and not requests
    assert endpoint.materialize() is endpoint
    assert endpoint.check() is True
    assert endpoint.check() is True
    assert len(clients) == 1 and len(requests) == 2


@pytest.mark.parametrize(
    "status,message,expected",
    [
        (401, "Invalid key", "LLMAuthError"),
        (429, "Rate limit", "LLMRateLimitExceededError"),
        (400, "Maximum context length exceeded", "LLMContextLimitExceededError"),
        (400, "Unsupported response format", "LLMProviderRequestError"),
    ],
)
def test_sdk_errors_use_core_status_and_message_classification(sdk_http, status, message, expected):
    from roboz import exceptions

    clients, requests = sdk_http(
        lambda _: httpx.Response(status, json={"error": {"message": message}})
    )
    endpoint = chat_endpoint(model="custom", max_context_tokens=123, api_key="test", stream=False)
    with pytest.raises(getattr(exceptions, expected)):
        call_llm_api(endpoint, [], max_attempts=1)
    assert len(clients) == len(requests) == 1


def test_explicit_materialization_shares_client_with_policy_copies(sdk_http):
    clients, requests = sdk_http(lambda _: pytest.fail("unexpected request"))
    endpoint = chat_endpoint(model="custom", max_context_tokens=123, api_key="test")
    configured = with_openrouter_policy(endpoint)
    assert clients == requests == []
    assert configured.materialize() is configured
    assert endpoint.materialize() is endpoint
    assert configured.client is endpoint.client
    assert len(clients) == 1 and not requests
