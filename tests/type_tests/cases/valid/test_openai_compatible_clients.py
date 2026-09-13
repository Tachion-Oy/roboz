"""The real synchronous OpenAI SDK satisfies the client contracts without casts."""

from collections.abc import Iterable
from typing import assert_type

from openai import OpenAI

from roboz.llm import LLMEndpoint, TranscriptionEndpoint
from roboz.llm.openai_compatible import (
    OpenAIChatCompletion,
    OpenAICompatibleChatClient,
    OpenAICompatibleModelsClient,
    OpenAICompatibleTranscriptionClient,
    OpenAITranscription,
)


def check_sdk_types(client: OpenAI) -> None:
    def accepts_models(client: OpenAICompatibleModelsClient) -> None: ...
    def accepts_chat(client: OpenAICompatibleChatClient) -> None: ...
    def accepts_audio(client: OpenAICompatibleTranscriptionClient) -> None: ...

    accepts_models(client)
    accepts_chat(client)
    accepts_audio(client)
    chat = LLMEndpoint(client=client, api_name="test", model_name="chat")
    audio = TranscriptionEndpoint(client=client, api_name="test", model_name="audio")
    assert_type(chat.client, OpenAICompatibleChatClient)
    assert_type(audio.client, OpenAICompatibleTranscriptionClient)
    assert_type(chat.client.models.list(timeout=10.0), object)
    assert_type(
        chat.client.chat.completions.create(
            model="chat", messages=[], temperature=0.7, response_format={"type": "text"}
        ),
        OpenAIChatCompletion,
    )
    assert_type(
        chat.client.chat.completions.create(
            model="chat",
            messages=[],
            temperature=0.7,
            response_format={"type": "text"},
            stream=True,
            stream_options={"include_usage": True},
        ),
        Iterable[object],
    )
    assert_type(
        audio.client.audio.transcriptions.create(
            file=("sample.wav", b"sample", "audio/wav"), model="audio", temperature=0.0
        ),
        OpenAITranscription,
    )
