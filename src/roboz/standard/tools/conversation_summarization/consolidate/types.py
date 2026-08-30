"""Runtime context for memory consolidation."""

from dataclasses import dataclass
from pathlib import Path

from roboz.llm import EndpointBinding
from roboz.runtime import EventPipe
from roboz.tooling import FactoryCtx


@dataclass(frozen=True)
class ConsolidateMemoryCtx(FactoryCtx):
    endpoint: EndpointBinding
    snapshot_root: Path
    memory_root: Path
    conversation_root: Path
    agent_names: set[str]
    min_pending_snapshots: int
    max_pending_age_seconds: float
    max_chars: int
    timeout_s: float | None = None
    pipe: EventPipe | None = None


__all__ = ["ConsolidateMemoryCtx"]
