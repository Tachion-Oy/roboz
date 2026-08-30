from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from roboz.models import Message
from roboz.runtime.observability import (
    RuntimeEventCategory,
    RuntimeEventKind,
    RuntimeEventLevel,
)
from roboz.runtime.persistence.schema import RunStatus
from roboz.models import Role


@dataclass(frozen=True)
class MessageEvent:
    message: Message
    sequence: int
    message_id: str | None = None


@dataclass(frozen=True)
class RunLifecycleEvent:
    kind: Literal["started", "stopped"]
    agent_name: str
    sequence: int
    agent_description: str | None = None
    parent_agent_name: str | None = None
    status: RunStatus | None = None
    api_name: str | None = None
    model_name: str | None = None
    max_context_tokens: int | None = None
    temperature: float | None = None
    output_format: Literal["text", "json"] | None = None


@dataclass(frozen=True)
class ScriptOutputEvent:
    content: str
    sequence: int


@dataclass(frozen=True)
class MessageDeltaEvent:
    message_id: str
    delta: str
    chunk_index: int
    role: Role
    agent_name: str
    sequence: int


@dataclass(frozen=True)
class RuntimeEvent:
    category: RuntimeEventCategory
    kind: RuntimeEventKind
    level: RuntimeEventLevel
    message: str
    sequence: int
    agent_name: str
    data: dict[str, Any] | None = None


PipeEvent = (
    MessageEvent
    | RunLifecycleEvent
    | ScriptOutputEvent
    | MessageDeltaEvent
    | RuntimeEvent
)
EventSink = Callable[[PipeEvent], None]
