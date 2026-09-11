"""Configured tool and skill capabilities for Shed agent definitions."""

import math
from collections.abc import Callable, Collection
from dataclasses import dataclass

from roboshed.identifiers import (
    CONSOLIDATE_MEMORY_TOOL_NAME,
    PURGE_LOGS_TOOL_NAME,
    PURGE_MEMORY_TOOL_NAME,
    PURGE_SNAPSHOTS_TOOL_NAME,
    SLEEP_BETWEEN_RUNS_TOOL_NAME,
    SNAPSHOT_CONVERSATIONS_TOOL_NAME,
)
from roboshed.skills import cli_skill, file_editing
from roboshed.tools import (
    get_apply_patch,
    get_compactify_messages_when_needed_tool,
    get_run_file_command,
)
from roboshed.tools.cli_commands.run_file_command import FILE_COMMANDS_READ
from roboshed.tools.compactification import (
    DEFAULT_MAX_CHARS_TOLERANCE_PERCENT,
    DEFAULT_THRESHOLD_PERCENT,
)
from roboshed.tools.consolidate_memory import consolidate_memory
from roboshed.tools.purge_files import purge_files
from roboshed.tools.sleep_between_runs import sleep_between_runs
from roboshed.tools.snapshot_conversations import snapshot_conversations
from roboshed.sandbox import PermissionPolicy, Sandbox
from roboz.deployment import AgentCapability, Capability
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe
from roboz.tooling.context import Ctx


@dataclass(frozen=True)
class FileCommands(AgentCapability):
    """Guarded read commands, optionally accompanied by their orientation skill."""

    permissions: PermissionPolicy | Callable[[], PermissionPolicy] | None = None
    auto_load_skill: bool = True

    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        """Build a read-tool chain using this agent's pipe and selected policy."""
        configured = self.permissions
        permissions = configured() if callable(configured) else configured
        if permissions is None:
            raise ValueError("file commands require permissions or a Deployment")
        return Capability(
            tools=(
                get_run_file_command(
                    **permissions.tool_options(pipe),
                    command_specs=FILE_COMMANDS_READ,
                ),
            ),
            auto_loaded_skills=(cli_skill,) if self.auto_load_skill else (),
        )

@dataclass(frozen=True)
class FileEditing(AgentCapability):
    """Literal patch editing with caller-selected file permissions."""

    permissions: PermissionPolicy | Callable[[], PermissionPolicy] | None = None
    auto_load_skill: bool = True

    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        """Build patch editing and optional orientation against the owning pipe."""
        configured = self.permissions
        permissions = configured() if callable(configured) else configured
        if permissions is None:
            raise ValueError("file editing requires permissions or a Deployment")
        return Capability(
            tools=(get_apply_patch(**permissions.tool_options(pipe)),),
            auto_loaded_skills=(file_editing,) if self.auto_load_skill else (),
        )

@dataclass(frozen=True)
class Compactification(AgentCapability):
    """Automatic context compaction, bound to the selected or owning endpoint."""

    endpoint: EndpointLike | None = None
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT
    timeout_s: float | None = None

    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        """Use the configured endpoint, falling back to the agent's endpoint."""
        endpoint = self.endpoint if self.endpoint is not None else default_endpoint
        if endpoint is None:
            raise ValueError("compaction requires an endpoint")
        return Capability(
            default_tools=(
                get_compactify_messages_when_needed_tool(
                    endpoint=endpoint,
                    threshold_percent=self.threshold_percent,
                    timeout_s=self.timeout_s,
                    pipe=pipe,
                ),
            )
        )


@dataclass(frozen=True)
class ConversationSnapshots(AgentCapability):
    """Automatic bounded snapshots of the selected agents' conversations.

    The endpoint defaults to the owning agent's model. Timeout is in seconds;
    summary tolerance is a finite, non-negative percentage above max_chars.
    """

    sandbox: Sandbox | None = None
    project_slug: str | None = None
    agent_names: Collection[str] | None = None
    endpoint: EndpointLike | None = None
    token_growth_threshold: int = 20_000
    max_chars: int = 8_000
    max_chars_tolerance_percent: float = DEFAULT_MAX_CHARS_TOLERANCE_PERCENT
    timeout_s: float = 300.0

    def __post_init__(self) -> None:
        """Validate summary bounds before constructing tools."""
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")
        if (
            not math.isfinite(self.max_chars_tolerance_percent)
            or self.max_chars_tolerance_percent < 0
        ):
            raise ValueError(
                "max_chars_tolerance_percent must be finite and non-negative"
            )

    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        """Bind snapshotting to its chosen model and the owning agent's pipe."""
        sandbox = self.sandbox
        if sandbox is None:
            raise ValueError("conversation snapshots require a sandbox or Deployment")
        names = self.agent_names
        if names is None:
            raise ValueError(
                "conversation snapshots require agent names or a Deployment"
            )
        endpoint = self.endpoint if self.endpoint is not None else default_endpoint
        if endpoint is None:
            raise ValueError("conversation snapshots require an endpoint")
        return Capability(
            default_tools=(
                snapshot_conversations(
                    Ctx(
                        endpoint=endpoint,
                        conversation_root=sandbox.project_logs_dir(self.project_slug),
                        snapshot_root=sandbox.project_snapshots_dir(self.project_slug),
                        memory_root=sandbox.project_memory_dir(self.project_slug),
                        agent_names=set(names),
                        token_growth_threshold=self.token_growth_threshold,
                        max_chars=self.max_chars,
                        max_chars_tolerance_percent=self.max_chars_tolerance_percent,
                        timeout_s=self.timeout_s,
                        pipe=pipe,
                    )
                ).copy(name=SNAPSHOT_CONVERSATIONS_TOOL_NAME),
            )
        )

@dataclass(frozen=True)
class MemoryConsolidation(AgentCapability):
    """Automatic consolidation of pending snapshots into durable memory.

    The endpoint defaults to the owning agent's model. Age and timeout are in
    seconds; summary tolerance is a finite, non-negative percentage above max_chars.
    """

    sandbox: Sandbox | None = None
    project_slug: str | None = None
    agent_names: Collection[str] | None = None
    endpoint: EndpointLike | None = None
    min_pending_snapshots: int = 3
    max_pending_age_seconds: float = 86_400.0
    max_chars: int = 12_000
    max_chars_tolerance_percent: float = DEFAULT_MAX_CHARS_TOLERANCE_PERCENT
    timeout_s: float = 300.0

    def __post_init__(self) -> None:
        """Validate summary bounds before constructing tools."""
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")
        if (
            not math.isfinite(self.max_chars_tolerance_percent)
            or self.max_chars_tolerance_percent < 0
        ):
            raise ValueError(
                "max_chars_tolerance_percent must be finite and non-negative"
            )

    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        """Bind consolidation to its chosen model and the owning agent's pipe."""
        sandbox = self.sandbox
        if sandbox is None:
            raise ValueError("memory consolidation requires a sandbox or Deployment")
        names = self.agent_names
        if names is None:
            raise ValueError(
                "memory consolidation requires agent names or a Deployment"
            )
        endpoint = self.endpoint if self.endpoint is not None else default_endpoint
        if endpoint is None:
            raise ValueError("memory consolidation requires an endpoint")
        return Capability(
            default_tools=(
                consolidate_memory(
                    Ctx(
                        endpoint=endpoint,
                        snapshot_root=sandbox.project_snapshots_dir(self.project_slug),
                        memory_root=sandbox.project_memory_dir(self.project_slug),
                        conversation_root=sandbox.project_logs_dir(self.project_slug),
                        agent_names=set(names),
                        min_pending_snapshots=self.min_pending_snapshots,
                        max_pending_age_seconds=self.max_pending_age_seconds,
                        max_chars=self.max_chars,
                        max_chars_tolerance_percent=self.max_chars_tolerance_percent,
                        timeout_s=self.timeout_s,
                        pipe=pipe,
                    )
                ).copy(name=CONSOLIDATE_MEMORY_TOOL_NAME),
            )
        )

@dataclass(frozen=True)
class ArtifactRetention(AgentCapability):
    """Automatic retention limits for project logs, snapshots, and memory."""

    sandbox: Sandbox | None = None
    project_slug: str | None = None
    max_log_files: int = 500
    max_snapshot_files: int = 100
    max_memory_files: int = 10

    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        """Bind retention to the selected project without requiring a model."""
        sandbox = self.sandbox
        if sandbox is None:
            raise ValueError("artifact retention requires a sandbox or Deployment")
        return Capability(
            default_tools=(
                purge_files(
                    Ctx(
                        folders=[sandbox.project_logs_dir(self.project_slug)],
                        pattern="*.json",
                        max_files=self.max_log_files,
                    )
                ).copy(name=PURGE_LOGS_TOOL_NAME),
                purge_files(
                    Ctx(
                        folders=[sandbox.project_snapshots_dir(self.project_slug)],
                        pattern="*.md",
                        max_files=self.max_snapshot_files,
                        prune_empty_directories=True,
                    )
                ).copy(name=PURGE_SNAPSHOTS_TOOL_NAME),
                purge_files(
                    Ctx(
                        folders=[sandbox.project_memory_dir(self.project_slug)],
                        pattern="*.md",
                        max_files=self.max_memory_files,
                    )
                ).copy(name=PURGE_MEMORY_TOOL_NAME),
            )
        )

@dataclass(frozen=True)
class MaintenanceCadence(AgentCapability):
    """Wait between cycles while watched conversations are active; stop when idle.

    Place this after maintenance capabilities so their work runs before waiting.
    Waiting observes the owning agent's cancellation independently of any model.
    """

    sandbox: Sandbox | None = None
    project_slug: str | None = None
    agent_names: Collection[str] | None = None
    seconds: float = 120.0

    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        """Bind cadence and idle stopping to this agent's cancellation state."""
        sandbox = self.sandbox
        if sandbox is None:
            raise ValueError("maintenance cadence requires a sandbox or Deployment")
        names = self.agent_names
        if names is None:
            raise ValueError("maintenance cadence requires agent names or a Deployment")
        return Capability(
            default_tools=(
                sleep_between_runs(
                    Ctx(
                        seconds=self.seconds,
                        is_cancelled=lambda: pipe.cancelled,
                        conversation_root=sandbox.project_logs_dir(self.project_slug),
                        agent_names=set(names),
                    )
                ).copy(name=SLEEP_BETWEEN_RUNS_TOOL_NAME),
            )
        )
