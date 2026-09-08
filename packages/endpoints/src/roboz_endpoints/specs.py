"""Immutable model specifications used by catalogues and inventory data."""

from dataclasses import dataclass
from typing import ClassVar, Literal


@dataclass(frozen=True, slots=True)
class ChatModelSpec:
    """A canonical chat-model route and its configured context limit."""

    endpoint_type: ClassVar[Literal["llm"]] = "llm"
    model_id: str
    max_context_tokens: int

    def __post_init__(self) -> None:
        """Require a nonblank model ID and a positive context limit."""
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if self.max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")


@dataclass(frozen=True, slots=True)
class TranscriptionModelSpec:
    """A canonical transcription-model route, without a chat context limit."""

    endpoint_type: ClassVar[Literal["transcription"]] = "transcription"
    model_id: str

    def __post_init__(self) -> None:
        """Require a nonblank model ID."""
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")


type ModelSpec = ChatModelSpec | TranscriptionModelSpec

# Keep the public serialization identity while separating types from data.
ChatModelSpec.__module__ = "roboz_endpoints.inventory"
TranscriptionModelSpec.__module__ = "roboz_endpoints.inventory"
