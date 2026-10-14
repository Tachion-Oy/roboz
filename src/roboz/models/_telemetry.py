"""Telemetry metadata carried by tool and model outputs."""

from pydantic import BaseModel, ConfigDict


class LLMTelemetry(BaseModel):
    """Provider route id, model id, and billed usage; aligned with messages."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = None
    model: str | None = None
    token_input: int | None = None
    token_output: int | None = None
