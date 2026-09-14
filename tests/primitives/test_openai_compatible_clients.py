"""Real SDK contracts exercised against scripted HTTP without a provider service."""

import json

import httpx
from openai import OpenAI
from pydantic import ValidationError
import pytest

from roboz.models import Message, Role
from roboz.llm import (
    LLMEndpoint,
    TranscriptionEndpoint,
    call_llm_api,
    call_transcription_api,
)


def sdk_client(respond):
    return OpenAI(
        api_key="scripted-test-key",
        base_url="https://provider.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )


@pytest.mark.parametrize("streaming", [False, True])
def test_sdk_chat_requests_preserve_typed_stream_and_response_contracts(streaming):
    requests = []

    def respond(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        requests.append(body)
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert body["model"] == "chat"
        assert body["temperature"] == 0.7
        assert body["response_format"] == {"type": "json_object"}
        assert body["custom_option"] is True
        if streaming:
            assert body["stream"] is True
            assert body["stream_options"] == {"include_usage": True}
            chunk = {
                "id": "scripted",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "chat",
                "choices": [
                    {"index": 0, "delta": {"content": "hello"}, "finish_reason": None}
                ],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        assert "stream" not in body
        return httpx.Response(
            200,
            json={
                "id": "scripted",
                "object": "chat.completion",
                "created": 0,
                "model": "chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with sdk_client(respond) as client:
        endpoint = LLMEndpoint(
            client=client,
            api_name="test",
            model_name="chat",
            output_format="json",
            extra_body={"custom_option": True},
        )
        assert endpoint.client is client
        assert endpoint.model_dump()["client"] is client
        assert requests == []
        deltas = []
        text, _ = call_llm_api(
            endpoint,
            [Message(role=Role.USER, content="hello")],
            on_delta=deltas.append if streaming else None,
        )
        assert text == "hello"
        assert deltas == (["hello"] if streaming else [])
        assert len(requests) == 1


def test_sdk_transcription_preserves_file_and_optional_arguments():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/v1/audio/transcriptions"
        body = request.content
        assert b'filename="sample.wav"' in body
        assert b"Content-Type: audio/wav" in body
        assert b"sample audio" in body
        assert b'name="language"\r\n\r\nen' in body
        assert b'name="prompt"\r\n\r\nrecognize this' in body
        return httpx.Response(200, json={"text": " scripted transcript "})

    with sdk_client(respond) as client:
        endpoint = TranscriptionEndpoint(
            client=client,
            api_name="test",
            model_name="audio",
            language="en",
            prompt="recognize this",
        )
        assert endpoint.client is client
        assert requests == []
        assert (
            call_transcription_api(
                endpoint,
                b"sample audio",
                filename="sample.wav",
                content_type="audio/wav",
            )
            == "scripted transcript"
        )
        assert len(requests) == 1


@pytest.mark.parametrize("endpoint_class", [LLMEndpoint, TranscriptionEndpoint])
def test_endpoint_rejects_client_without_required_api_attributes(endpoint_class):
    with pytest.raises(ValidationError, match="client"):
        endpoint_class(client=object(), api_name="test", model_name="model")
