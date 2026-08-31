from pydantic import BaseModel, ConfigDict, Field

from roboz.models.core import MessageKind
from roboz.models.truncation import TruncationSpec


class NonAgentFacingFields(BaseModel):
    truncation: TruncationSpec = Field(...)
    message_kind: MessageKind | None = None
    endpoint: str | None = None
    model: str | None = None
    token_input: int | None = None
    token_output: int | None = None


class LLMTelemetry(BaseModel):
    """Provider route id, model id, and billed usage; aligned with messages."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = None
    model: str | None = None
    token_input: int | None = None
    token_output: int | None = None
