"""Configured tool and skill capabilities for Shed agent definitions."""

import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import cast

from roboshed.identifiers import (
    CONSOLIDATE_MEMORY_TOOL_NAME,
    PURGE_LOGS_TOOL_NAME,
    PURGE_MEMORY_TOOL_NAME,
    PURGE_SNAPSHOTS_TOOL_NAME,
    SLEEP_BETWEEN_RUNS_TOOL_NAME,
    SNAPSHOT_CONVERSATIONS_TOOL_NAME,
    STOP_WHEN_WATCHED_AGENTS_INACTIVE_TOOL_NAME,
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
from roboshed.tools.contexts import (
    ConsolidateMemoryContext,
    PurgeFilesContext,
    SleepBetweenRunsContext,
    SnapshotConversationsContext,
    StopWhenWatchedAgentsInactiveContext,
)
from roboshed.tools.purge_files import purge_files
from roboshed.tools.sleep_between_runs import sleep_between_runs
from roboshed.tools.stop_when_watched_agents_inactive import (
    stop_when_watched_agents_inactive,
)
from roboshed.tools.snapshot_conversations import snapshot_conversations
from roboshed.sandbox import PermissionPolicy, Sandbox
from roboz.deployment import (
    AgentCapability,
    Capability,
    DeployableAgent,
    RequiredAttributeType,
    RequiredAttributes,
)
from roboz.llm import EndpointLike, LLMEndpoint, LLMEndpointRoute, MockLLMEndpoint
from roboz.runtime import EventPipe


_ENDPOINT_TYPES = (LLMEndpoint, MockLLMEndpoint, LLMEndpointRoute)
_AGENT_ENDPOINT_REQUIRED: RequiredAttributes = {
    "agent_endpoint": _ENDPOINT_TYPES,
}


@dataclass(frozen=True)
class FileCommands(AgentCapability):
    """Guarded read commands, optionally accompanied by their orientation skill."""

    auto_load_skill: bool = True

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require a file permission policy from the owning agent."""
        return {"permissions": PermissionPolicy}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        """Build a read-tool chain using this agent's pipe and selected policy."""
        permissions = cast(PermissionPolicy, agent.permissions)
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

    auto_load_skill: bool = True

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require a file permission policy from the owning agent."""
        return {"permissions": PermissionPolicy}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        """Build patch editing and optional orientation against the owning pipe."""
        permissions = cast(PermissionPolicy, agent.permissions)
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

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require the owner's endpoint only when no override is configured."""
        return _AGENT_ENDPOINT_REQUIRED if self.endpoint is None else {}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        """Use the configured endpoint, falling back to the agent's endpoint."""
        endpoint = (
            self.endpoint
            if self.endpoint is not None
            else cast(EndpointLike, agent.agent_endpoint)
        )
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

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Declare project, watched-name, and optional endpoint inputs."""
        required: dict[str, RequiredAttributeType] = {
            "sandbox": Sandbox,
            "watched_agent_names": Collection,
        }
        if self.endpoint is None:
            required.update(_AGENT_ENDPOINT_REQUIRED)
        return required

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        """Bind snapshotting to its chosen model and the owning agent's pipe."""
        sandbox = cast(Sandbox, agent.sandbox)
        watched_agent_names = cast(Collection[str], agent.watched_agent_names)
        endpoint = (
            self.endpoint
            if self.endpoint is not None
            else cast(EndpointLike, agent.agent_endpoint)
        )
        return Capability(
            default_tools=(
                snapshot_conversations(
                    SnapshotConversationsContext(
                        endpoint=endpoint,
                        conversation_root=sandbox.project_logs_dir(),
                        snapshot_root=sandbox.project_snapshots_dir(),
                        memory_root=sandbox.project_memory_dir(),
                        agent_names=set(watched_agent_names),
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

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Declare project, watched-name, and optional endpoint inputs."""
        required: dict[str, RequiredAttributeType] = {
            "sandbox": Sandbox,
            "watched_agent_names": Collection,
        }
        if self.endpoint is None:
            required.update(_AGENT_ENDPOINT_REQUIRED)
        return required

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        """Bind consolidation to its chosen model and the owning agent's pipe."""
        sandbox = cast(Sandbox, agent.sandbox)
        watched_agent_names = cast(Collection[str], agent.watched_agent_names)
        endpoint = (
            self.endpoint
            if self.endpoint is not None
            else cast(EndpointLike, agent.agent_endpoint)
        )
        return Capability(
            default_tools=(
                consolidate_memory(
                    ConsolidateMemoryContext(
                        endpoint=endpoint,
                        snapshot_root=sandbox.project_snapshots_dir(),
                        memory_root=sandbox.project_memory_dir(),
                        conversation_root=sandbox.project_logs_dir(),
                        agent_names=set(watched_agent_names),
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

    max_log_files: int = 500
    max_snapshot_files: int = 100
    max_memory_files: int = 10

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require the owning agent's project sandbox."""
        return {"sandbox": Sandbox}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        """Bind retention to the selected project without requiring a model."""
        sandbox = cast(Sandbox, agent.sandbox)
        return Capability(
            default_tools=(
                purge_files(
                    PurgeFilesContext(
                        folders=[sandbox.project_logs_dir()],
                        pattern="*.json",
                        max_files=self.max_log_files,
                    )
                ).copy(name=PURGE_LOGS_TOOL_NAME),
                purge_files(
                    PurgeFilesContext(
                        folders=[sandbox.project_snapshots_dir()],
                        pattern="*.md",
                        max_files=self.max_snapshot_files,
                        prune_empty_directories=True,
                    )
                ).copy(name=PURGE_SNAPSHOTS_TOOL_NAME),
                purge_files(
                    PurgeFilesContext(
                        folders=[sandbox.project_memory_dir()],
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

    seconds: float = 120.0

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require the project and conversations observed between cycles."""
        return {
            "sandbox": Sandbox,
            "watched_agent_names": Collection,
        }

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        """Bind cadence and inactive-agent stopping to cancellation state."""
        sandbox = cast(Sandbox, agent.sandbox)
        watched_agent_names = cast(Collection[str], agent.watched_agent_names)
        return Capability(
            default_tools=(
                stop_when_watched_agents_inactive(
                    StopWhenWatchedAgentsInactiveContext(
                        conversation_root=sandbox.project_logs_dir(),
                        agent_names=set(watched_agent_names),
                    )
                ).copy(name=STOP_WHEN_WATCHED_AGENTS_INACTIVE_TOOL_NAME),
                sleep_between_runs(
                    SleepBetweenRunsContext(
                        seconds=self.seconds,
                        is_cancelled=lambda: pipe.cancelled,
                        conversation_root=sandbox.project_logs_dir(),
                        agent_names=set(watched_agent_names),
                    )
                ).copy(name=SLEEP_BETWEEN_RUNS_TOOL_NAME),
            )
        )
