"""Typed factory contexts for dependency-free memory maintenance tools."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from roboz.llm import EndpointBinding
from roboz.runtime import EventPipe
from roboz.tooling import FactoryCtx
from roboz.tools.compactification import DEFAULT_MAX_CHARS_TOLERANCE_PERCENT


@dataclass(frozen=True)
class SnapshotConversationsCtx(FactoryCtx):
    """Configuration for generating incremental conversation snapshots."""

    endpoint: EndpointBinding
    conversation_root: Path
    snapshot_root: Path
    memory_root: Path
    agent_names: set[str]
    token_growth_threshold: int
    max_chars: int
    max_chars_tolerance_percent: float = DEFAULT_MAX_CHARS_TOLERANCE_PERCENT
    timeout_s: float | None = None
    pipe: EventPipe | None = None


@dataclass(frozen=True)
class ConsolidateMemoryCtx(FactoryCtx):
    """Configuration for folding snapshots into persistent memory."""

    endpoint: EndpointBinding
    snapshot_root: Path
    memory_root: Path
    conversation_root: Path
    agent_names: set[str]
    min_pending_snapshots: int
    max_pending_age_seconds: float
    max_chars: int
    max_chars_tolerance_percent: float = DEFAULT_MAX_CHARS_TOLERANCE_PERCENT
    timeout_s: float | None = None
    pipe: EventPipe | None = None


@dataclass(frozen=True)
class PurgeFilesCtx(FactoryCtx):
    """Configuration for threshold-based artifact retention."""

    pattern: str
    max_files: int
    folders: list[Path]
    prune_empty_directories: bool = False


@dataclass(frozen=True)
class SleepBetweenRunsCtx(FactoryCtx):
    """Configuration for the cancellable wait at the end of a memory cycle."""

    seconds: float
    is_cancelled: Callable[[], bool] | None = None
    conversation_root: Path | None = None
    agent_names: set[str] = field(default_factory=set)


__all__ = [
    "ConsolidateMemoryCtx",
    "PurgeFilesCtx",
    "SleepBetweenRunsCtx",
    "SnapshotConversationsCtx",
]
