"""Immutable model specifications used by catalogues and inventory data."""

from dataclasses import dataclass
from typing import ClassVar, Literal


@dataclass(frozen=True, slots=True)
class ChatModelSpec:
    """A canonical chat-model route and its configured context limit."""

    endpoint_type: ClassVar[Literal["llm"]] = "llm"
    model_id: str
    max_context_tokens: int
    accepts_images: bool = False
    # Some reasoning models reject a temperature; their requests then omit it.
    accepts_temperature: bool = True
    # USD per 1M tokens; a model has both prices or neither.
    input_price_per_million_tokens: float | None = None
    output_price_per_million_tokens: float | None = None
    # "json" requests JSON mode, which not every model or provider supports.
    output_format: Literal["text", "json"] = "text"

    def __post_init__(self) -> None:
        """Require a nonblank model ID, a positive context limit and paired prices."""
        if not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if self.max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")
        input_price = self.input_price_per_million_tokens
        output_price = self.output_price_per_million_tokens
        if (input_price is None) != (output_price is None):
            raise ValueError(
                "input_price_per_million_tokens and output_price_per_million_tokens"
                " must be set together"
            )
        if input_price is not None and input_price < 0:
            raise ValueError("input_price_per_million_tokens must not be negative")
        if output_price is not None and output_price < 0:
            raise ValueError("output_price_per_million_tokens must not be negative")


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
ChatModelSpec.__module__ = "roboz.endpoints.inventory"
TranscriptionModelSpec.__module__ = "roboz.endpoints.inventory"
