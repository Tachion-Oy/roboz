"""Lean composition for the deterministic Librarian maintenance daemon."""

import math
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from roboz.agent import Agent
from roboz.llm import EndpointLike, bind_endpoint
from roboz.runtime import EventPipe, EventSink, default_event_sinks
from roboz.tooling import Tool
from roboz.tools._identifiers import (
    CONSOLIDATE_MEMORY_TOOL_NAME,
    LIBRARIAN_AGENT_NAME,
    PURGE_LOGS_TOOL_NAME,
    PURGE_MEMORY_TOOL_NAME,
    PURGE_SNAPSHOTS_TOOL_NAME,
    SLEEP_BETWEEN_RUNS_TOOL_NAME,
    SNAPSHOT_CONVERSATIONS_TOOL_NAME,
)
from roboz.tools.consolidate_memory import consolidate_memory
from roboz.tools.compactification import DEFAULT_MAX_CHARS_TOLERANCE_PERCENT
from roboz.tools.memory_contexts import (
    ConsolidateMemoryCtx,
    PurgeFilesCtx,
    SleepBetweenRunsCtx,
    SnapshotConversationsCtx,
)
from roboz.tools.purge_files import purge_files
from roboz.tools.sleep_between_runs import sleep_between_runs
from roboz.tools.snapshot_conversations import snapshot_conversations

LIBRARIAN_AGENT_DESCRIPTION: Final[str] = (
    "Runs deterministic maintenance cycles that snapshot conversations, "
    "consolidate memory, and manage retention for logs and memory artifacts."
)

DEFAULT_MIN_PENDING_SNAPSHOTS: Final[int] = 3
DEFAULT_MAX_PENDING_AGE_SECONDS: Final[float] = 86_400.0
DEFAULT_SLEEP_SECONDS: Final[float] = 120.0
DEFAULT_TOKEN_GROWTH_THRESHOLD: Final[int] = 20_000
DEFAULT_MAX_LOG_FILES: Final[int] = 500
DEFAULT_MAX_SNAPSHOT_FILES: Final[int] = 100
DEFAULT_MAX_MEMORY_FILES: Final[int] = 10
DEFAULT_MAX_SNAPSHOT_CHARS: Final[int] = 8_000
DEFAULT_MAX_MEMORY_CHARS: Final[int] = 12_000
DEFAULT_LLM_TIMEOUT_SECONDS: Final[float] = 300.0

JSON_ARTIFACT_PATTERN: Final[str] = "*.json"
MARKDOWN_ARTIFACT_PATTERN: Final[str] = "*.md"
_MIN_TIMEOUT_SECONDS: Final[float] = 0.0
_MIN_TOLERANCE_PERCENT: Final[float] = 0.0

type IsCancelled = Callable[[], bool]
type EndpointFactory = Callable[[IsCancelled], EndpointLike]


@dataclass(frozen=True)
class LibrarianPaths:
    """Filesystem tier consumed by Librarian maintenance."""

    conversation_root: Path
    snapshot_root: Path
    memory_root: Path


@dataclass(frozen=True)
class LibrarianTuning:
    """Cadence, retention, and bounded-summary settings."""

    min_pending_snapshots: int = DEFAULT_MIN_PENDING_SNAPSHOTS
    max_pending_age_seconds: float = DEFAULT_MAX_PENDING_AGE_SECONDS
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS
    token_growth_threshold: int = DEFAULT_TOKEN_GROWTH_THRESHOLD
    max_log_files: int = DEFAULT_MAX_LOG_FILES
    max_snapshot_files: int = DEFAULT_MAX_SNAPSHOT_FILES
    max_memory_files: int = DEFAULT_MAX_MEMORY_FILES
    max_snapshot_chars: int = DEFAULT_MAX_SNAPSHOT_CHARS
    max_memory_chars: int = DEFAULT_MAX_MEMORY_CHARS
    max_chars_tolerance_percent: float = DEFAULT_MAX_CHARS_TOLERANCE_PERCENT
    llm_timeout_s: float = DEFAULT_LLM_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.llm_timeout_s <= _MIN_TIMEOUT_SECONDS:
            raise ValueError("llm_timeout_s must be greater than zero")
        if not math.isfinite(self.max_chars_tolerance_percent) or (
            self.max_chars_tolerance_percent < _MIN_TOLERANCE_PERCENT
        ):
            raise ValueError(
                "max_chars_tolerance_percent must be finite and non-negative"
            )


class CancellationProbe:
    """Bind callbacks created before the owning Agent's pipe is available."""

    def __init__(self) -> None:
        self._pipe: EventPipe | None = None

    def is_cancelled(self) -> bool:
        return self._pipe is not None and self._pipe.cancelled

    def bind(self, pipe: EventPipe) -> None:
        self._pipe = pipe


@dataclass(frozen=True)
class LibrarianConstructor:
    """Build a non-agentic background Agent with a fixed maintenance pipeline."""

    snapshot_endpoint: EndpointLike | None = None
    endpoint_factory: EndpointFactory | None = None
    tuning: LibrarianTuning = LibrarianTuning()
    agent_name: str = LIBRARIAN_AGENT_NAME

    def __post_init__(self) -> None:
        if (self.snapshot_endpoint is None) == (self.endpoint_factory is None):
            raise ValueError(
                "set exactly one of snapshot_endpoint or endpoint_factory"
            )

    def pipeline(
        self,
        *,
        paths: LibrarianPaths,
        agent_names: Collection[str],
        pipe: EventPipe,
        probe: CancellationProbe,
    ) -> tuple[Tool, ...]:
        """Build the exact ordered tool sequence used for every cycle."""
        endpoint = (
            self.endpoint_factory(probe.is_cancelled)
            if self.endpoint_factory is not None
            else self.snapshot_endpoint
        )
        assert endpoint is not None
        endpoint_binding = bind_endpoint(endpoint)
        watched_agents = set(agent_names)
        tuning = self.tuning

        snapshot = snapshot_conversations(
            SnapshotConversationsCtx(
                endpoint=endpoint_binding,
                conversation_root=paths.conversation_root,
                snapshot_root=paths.snapshot_root,
                memory_root=paths.memory_root,
                agent_names=watched_agents,
                token_growth_threshold=tuning.token_growth_threshold,
                max_chars=tuning.max_snapshot_chars,
                max_chars_tolerance_percent=tuning.max_chars_tolerance_percent,
                timeout_s=tuning.llm_timeout_s,
                pipe=pipe,
            )
        ).copy(name=SNAPSHOT_CONVERSATIONS_TOOL_NAME)
        consolidate = consolidate_memory(
            ConsolidateMemoryCtx(
                endpoint=endpoint_binding,
                snapshot_root=paths.snapshot_root,
                memory_root=paths.memory_root,
                conversation_root=paths.conversation_root,
                agent_names=watched_agents,
                min_pending_snapshots=tuning.min_pending_snapshots,
                max_pending_age_seconds=tuning.max_pending_age_seconds,
                max_chars=tuning.max_memory_chars,
                max_chars_tolerance_percent=tuning.max_chars_tolerance_percent,
                timeout_s=tuning.llm_timeout_s,
                pipe=pipe,
            )
        ).copy(name=CONSOLIDATE_MEMORY_TOOL_NAME)
        purge_logs = purge_files(
            PurgeFilesCtx(
                folders=[paths.conversation_root],
                pattern=JSON_ARTIFACT_PATTERN,
                max_files=tuning.max_log_files,
            )
        ).copy(name=PURGE_LOGS_TOOL_NAME)
        purge_snapshots = purge_files(
            PurgeFilesCtx(
                folders=[paths.snapshot_root],
                pattern=MARKDOWN_ARTIFACT_PATTERN,
                max_files=tuning.max_snapshot_files,
                prune_empty_directories=True,
            )
        ).copy(name=PURGE_SNAPSHOTS_TOOL_NAME)
        purge_memory = purge_files(
            PurgeFilesCtx(
                folders=[paths.memory_root],
                pattern=MARKDOWN_ARTIFACT_PATTERN,
                max_files=tuning.max_memory_files,
            )
        ).copy(name=PURGE_MEMORY_TOOL_NAME)
        wait = sleep_between_runs(
            SleepBetweenRunsCtx(
                seconds=tuning.sleep_seconds,
                is_cancelled=probe.is_cancelled,
                conversation_root=paths.conversation_root,
                agent_names=watched_agents,
            )
        ).copy(name=SLEEP_BETWEEN_RUNS_TOOL_NAME)
        return (
            snapshot,
            consolidate,
            purge_logs,
            purge_snapshots,
            purge_memory,
            wait,
        )

    def build(
        self,
        *,
        paths: LibrarianPaths,
        agent_names: Collection[str],
        event_sinks: Sequence[EventSink] = (),
        include_cli_output: bool = False,
    ) -> Agent:
        """Construct a headless daemon and persist its own maintenance run log."""
        built_in_sinks = default_event_sinks(
            data_path=paths.conversation_root / self.agent_name,
            include_cli=include_cli_output,
        )
        pipe = EventPipe(event_sinks=(*built_in_sinks, *event_sinks))
        probe = CancellationProbe()
        pipeline = self.pipeline(
            paths=paths,
            agent_names=agent_names,
            pipe=pipe,
            probe=probe,
        )
        agent = Agent(
            name=self.agent_name,
            description=LIBRARIAN_AGENT_DESCRIPTION,
            interaction_mode=None,
            event_pipe=pipe,
            agent_endpoint=None,
            is_agentic=False,
            automatic_tool_prompt=False,
            default_tools=pipeline,
        )
        probe.bind(agent.pipe)
        return agent


__all__ = [
    "CancellationProbe",
    "EndpointFactory",
    "IsCancelled",
    "LIBRARIAN_AGENT_DESCRIPTION",
    "LibrarianConstructor",
    "LibrarianPaths",
    "LibrarianTuning",
]
