"""Lean composition for the deterministic Librarian maintenance daemon."""

import math
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Final

from roboshed.identifiers import (
    CONSOLIDATE_MEMORY_TOOL_NAME,
    LIBRARIAN_AGENT_NAME,
    PURGE_LOGS_TOOL_NAME,
    PURGE_MEMORY_TOOL_NAME,
    PURGE_SNAPSHOTS_TOOL_NAME,
    SLEEP_BETWEEN_RUNS_TOOL_NAME,
    SNAPSHOT_CONVERSATIONS_TOOL_NAME,
)
from roboshed.tools.compactification import DEFAULT_MAX_CHARS_TOLERANCE_PERCENT
from roboshed.tools.consolidate_memory import consolidate_memory
from roboshed.tools.purge_files import purge_files
from roboshed.tools.sleep_between_runs import sleep_between_runs
from roboshed.tools.snapshot_conversations import snapshot_conversations
from roboshed.workspace import Project
from roboz.deployment import AgentDefinition, Capability
from roboz.llm import EndpointLike
from roboz.llm.binding import _validate_endpoint
from roboz.runtime import EventPipe
from roboz.tooling.context import Ctx

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
        """Validate timeout and tolerance bounds."""
        if self.llm_timeout_s <= _MIN_TIMEOUT_SECONDS:
            raise ValueError("llm_timeout_s must be greater than zero")
        if not math.isfinite(self.max_chars_tolerance_percent) or (
            self.max_chars_tolerance_percent < _MIN_TOLERANCE_PERCENT
        ):
            raise ValueError(
                "max_chars_tolerance_percent must be finite and non-negative"
            )


@dataclass(frozen=True)
class _LibrarianMaintenance:
    """Bind the fixed maintenance pipeline to its owning agent runtime."""

    project: Project
    agent_names: frozenset[str]

    snapshot_endpoint: EndpointLike | None = None
    endpoint_factory: EndpointFactory | None = None
    tuning: LibrarianTuning = LibrarianTuning()

    def __post_init__(self) -> None:
        """Require exactly one source for the snapshot endpoint."""
        if (self.snapshot_endpoint is None) == (self.endpoint_factory is None):
            raise ValueError("set exactly one of snapshot_endpoint or endpoint_factory")

    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        """Build the exact ordered tool sequence used for every cycle."""
        endpoint = (
            self.endpoint_factory(lambda: pipe.cancelled)
            if self.endpoint_factory is not None
            else self.snapshot_endpoint
        )
        assert endpoint is not None
        _validate_endpoint(endpoint)
        endpoint_binding = endpoint
        project = self.project
        watched_agents = set(self.agent_names)
        tuning = self.tuning

        snapshot = snapshot_conversations(
            Ctx(
                endpoint=endpoint_binding,
                conversation_root=project.logs,
                snapshot_root=project.snapshots,
                memory_root=project.memory,
                agent_names=watched_agents,
                token_growth_threshold=tuning.token_growth_threshold,
                max_chars=tuning.max_snapshot_chars,
                max_chars_tolerance_percent=tuning.max_chars_tolerance_percent,
                timeout_s=tuning.llm_timeout_s,
                pipe=pipe,
            )
        ).copy(name=SNAPSHOT_CONVERSATIONS_TOOL_NAME)
        consolidate = consolidate_memory(
            Ctx(
                endpoint=endpoint_binding,
                snapshot_root=project.snapshots,
                memory_root=project.memory,
                conversation_root=project.logs,
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
            Ctx(
                folders=[project.logs],
                pattern=JSON_ARTIFACT_PATTERN,
                max_files=tuning.max_log_files,
            )
        ).copy(name=PURGE_LOGS_TOOL_NAME)
        purge_snapshots = purge_files(
            Ctx(
                folders=[project.snapshots],
                pattern=MARKDOWN_ARTIFACT_PATTERN,
                max_files=tuning.max_snapshot_files,
                prune_empty_directories=True,
            )
        ).copy(name=PURGE_SNAPSHOTS_TOOL_NAME)
        purge_memory = purge_files(
            Ctx(
                folders=[project.memory],
                pattern=MARKDOWN_ARTIFACT_PATTERN,
                max_files=tuning.max_memory_files,
            )
        ).copy(name=PURGE_MEMORY_TOOL_NAME)
        wait = sleep_between_runs(
            Ctx(
                seconds=tuning.sleep_seconds,
                is_cancelled=lambda: pipe.cancelled,
                conversation_root=project.logs,
                agent_names=watched_agents,
            )
        ).copy(name=SLEEP_BETWEEN_RUNS_TOOL_NAME)
        return Capability(
            default_tools=(
                snapshot,
                consolidate,
                purge_logs,
                purge_snapshots,
                purge_memory,
                wait,
            )
        )


def librarian(
    *,
    project: Project,
    agent_names: Collection[str],
    snapshot_endpoint: EndpointLike | None = None,
    endpoint_factory: EndpointFactory | None = None,
    tuning: LibrarianTuning = LibrarianTuning(),
    name: str = LIBRARIAN_AGENT_NAME,
) -> AgentDefinition:
    """Define a deterministic background agent with a fixed memory pipeline.

    Supply the watched conversation names and exactly one endpoint source. The
    returned generic definition builds fresh tools against its owning pipe;
    constructing it starts no work and selects no event sinks or log location.
    """
    return AgentDefinition(
        name=name,
        description=LIBRARIAN_AGENT_DESCRIPTION,
        interaction_mode=None,
        agent_endpoint=None,
        is_agentic=False,
        automatic_tool_prompt=False,
        capabilities=(
            _LibrarianMaintenance(
                project=project,
                agent_names=frozenset(agent_names),
                snapshot_endpoint=snapshot_endpoint,
                endpoint_factory=endpoint_factory,
                tuning=tuning,
            ),
        ),
    )


__all__ = [
    "EndpointFactory",
    "IsCancelled",
    "LIBRARIAN_AGENT_DESCRIPTION",
    "librarian",
    "LibrarianTuning",
]
