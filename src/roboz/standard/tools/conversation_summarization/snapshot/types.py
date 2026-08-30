"""Runtime context for conversation snapshotting."""

from dataclasses import dataclass
from pathlib import Path

from roboz.llm import EndpointBinding
from roboz.runtime import EventPipe
from roboz.tooling import FactoryCtx


@dataclass(frozen=True)
class SnapshotConversationsCtx(FactoryCtx):
    endpoint: EndpointBinding
    conversation_root: Path
    snapshot_root: Path
    memory_root: Path
    agent_names: set[str]
    token_growth_threshold: int
    max_chars: int
    timeout_s: float | None = None
    pipe: EventPipe | None = None


__all__ = ["SnapshotConversationsCtx"]
