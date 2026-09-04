import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import openai
import pytest

from roboz import Agent, stop
from roboz.llm import with_request_options
from roboz_openai import openai_endpoint, openrouter_endpoint


def test_inspection_is_lazy_and_credentials_are_redacted(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    endpoint = openrouter_endpoint(model="test/model", max_context_tokens=4096)
    agent = Agent(
        name="test", system_prompt="Stop.", tools=[stop], agent_endpoint=endpoint
    )
    assert endpoint.dependency_id == "model:openrouter:test/model"
    assert agent.external_dependencies()
    assert "materialized" not in endpoint.__dict__
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        endpoint.materialize()


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
    endpoint = openrouter_endpoint(
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
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: endpoint.materialize(), range(8)))
        assert len(clients) == 1
        assert endpoint.materialize() is endpoint.materialize()
        configured = with_request_options(
            endpoint, extra_body={"reasoning": {"effort": "low"}}
        )
        assert configured.dependency_id == endpoint.dependency_id
        agent = Agent(
            name="test", system_prompt="Stop.", tools=[stop], agent_endpoint=endpoint
        )
        assert agent.invoke()[0].value == "ok"
        assert len(requests) == 1
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
        openai_endpoint(**settings)
