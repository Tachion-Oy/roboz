"""Immutable value types used by provider model catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class EndpointType(StrEnum):
    """The endpoint behavior required by a provider model."""

    LLM = "llm"
    TRANSCRIPTION = "transcription"


@dataclass(frozen=True, slots=True)
class ChatModelSpec:
    """A chat-model route and its advertised context window."""

    endpoint_type: ClassVar[EndpointType] = EndpointType.LLM
    model_id: str
    max_context_tokens: int

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("model_id must be non-empty")
        if self.max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")


@dataclass(frozen=True, slots=True)
class TranscriptionModelSpec:
    """An audio-transcription model route."""

    endpoint_type: ClassVar[EndpointType] = EndpointType.TRANSCRIPTION
    model_id: str

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("model_id must be non-empty")


ModelSpec = ChatModelSpec | TranscriptionModelSpec


__all__ = [
    "ChatModelSpec",
    "EndpointType",
    "ModelSpec",
    "TranscriptionModelSpec",
]
