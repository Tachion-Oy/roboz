"""Structural client types for the synchronous OpenAI-compatible operations in core.

These protocols describe the subset Roboz calls, without importing a provider
SDK or wrapping clients. Message payloads remain the existing open dictionary
boundary; client methods, request controls, and consumed response fields are
explicitly typed. Additional response metadata is inspected by the diagnostics
code. Model listings are validated when an availability check is requested.
"""

from collections.abc import Iterable, Sequence
from typing import Any, Literal, Protocol, TypedDict, overload, runtime_checkable


class _TextFormat(TypedDict):
    type: Literal["text"]


class _JSONFormat(TypedDict):
    type: Literal["json_object"]


type OpenAIResponseFormat = _TextFormat | _JSONFormat


class OpenAIStreamOptions(TypedDict, total=False):
    """OpenAI-compatible streaming controls used by Roboz."""

    include_usage: bool
    include_obfuscation: bool


class _Message(Protocol):
    @property
    def content(self) -> str | None: ...


class _Choice(Protocol):
    @property
    def message(self) -> _Message: ...


class OpenAIChatCompletion(Protocol):
    """Required response fields for a non-streaming chat completion."""

    @property
    def choices(self) -> Sequence[_Choice]:
        """Return completion choices containing assistant messages."""
        ...


class OpenAITranscription(Protocol):
    """Required response field for a non-streaming transcription."""

    @property
    def text(self) -> str:
        """Return the transcribed text."""
        ...


class _Completions(Protocol):
    @overload
    def create(
        self,
        *,
        messages: Iterable[Any],
        model: str,
        temperature: float,
        response_format: OpenAIResponseFormat,
        extra_body: object = None,
        stream: Literal[False] = False,
    ) -> OpenAIChatCompletion: ...

    @overload
    def create(
        self,
        *,
        messages: Iterable[Any],
        model: str,
        temperature: float,
        response_format: OpenAIResponseFormat,
        extra_body: object = None,
        stream: Literal[True],
        stream_options: OpenAIStreamOptions = ...,
    ) -> Iterable[object]: ...


class _Chat(Protocol):
    @property
    def completions(self) -> _Completions: ...


class _Transcriptions(Protocol):
    def create(
        self,
        *,
        file: tuple[str, bytes, str],
        model: str,
        temperature: float,
        language: str = ...,
        prompt: str = ...,
    ) -> OpenAITranscription: ...


class _Audio(Protocol):
    @property
    def transcriptions(self) -> _Transcriptions: ...


class _Models(Protocol):
    def list(self, *, timeout: float) -> object: ...


@runtime_checkable
class OpenAICompatibleModelsClient(Protocol):
    """Synchronous client supporting OpenAI-compatible model discovery."""

    @property
    def models(self) -> _Models:
        """Return the model-discovery API; accessing it must not make requests."""
        ...


@runtime_checkable
class OpenAICompatibleChatClient(OpenAICompatibleModelsClient, Protocol):
    """Synchronous client supporting model discovery and chat completions."""

    @property
    def chat(self) -> _Chat:
        """Return the chat API; accessing it must not make requests."""
        ...


@runtime_checkable
class OpenAICompatibleTranscriptionClient(OpenAICompatibleModelsClient, Protocol):
    """Synchronous client supporting model discovery and audio transcription."""

    @property
    def audio(self) -> _Audio:
        """Return the audio API; accessing it must not make requests."""
        ...
