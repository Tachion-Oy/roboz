"""Configured tool and skill capabilities for Shed agent definitions."""

import math
from _thread import LockType
from collections.abc import Collection
from pathlib import Path
from threading import Lock
from typing import cast

from roboz.shed.identifiers import (
    CONSOLIDATE_MEMORY_TOOL_NAME,
    PURGE_LOGS_TOOL_NAME,
    PURGE_MEMORY_TOOL_NAME,
    PURGE_SNAPSHOTS_TOOL_NAME,
    SLEEP_BETWEEN_RUNS_TOOL_NAME,
    SNAPSHOT_CONVERSATIONS_TOOL_NAME,
    STOP_WHEN_WATCHED_AGENTS_INACTIVE_TOOL_NAME,
)
from roboz.shed.skills import FilesystemContext, email_skill, filesystem_skill
from roboz.shed.tools import get_compactify_messages_when_needed_tool
from roboz.shed.tools.compactification import (
    DEFAULT_MAX_CHARS_TOLERANCE_PERCENT,
    DEFAULT_THRESHOLD_PERCENT,
)
from roboz.shed.tools.consolidate_memory import consolidate_memory
from roboz.shed.tools.contexts import (
    ConsolidateMemoryContext,
    PurgeFilesContext,
    SleepBetweenRunsContext,
    SnapshotConversationsContext,
    StopWhenWatchedAgentsInactiveContext,
)
from roboz.shed.tools.email import EmailService, get_work_with_email
from roboz.shed.tools.email.factory import DEFAULT_EMAIL_OPERATION_TIMEOUT_S
from roboz.shed.tools.purge_files import purge_files
from roboz.shed.tools.sleep_between_runs import sleep_between_runs
from roboz.shed.tools.safe_scripts import (
    RemoteScriptContext,
    ScriptSocketDependency,
    SafeScriptContext,
    run_shell_script,
)
from roboz.shed.tools.safe_scripts.protocol import (
    DEFAULT_TIMEOUT_S,
    DEFAULT_MAX_OUTPUT_BYTES,
    ExecutionPolicy,
    require_linux_transport,
)
from roboz.shed.tools.stop_when_watched_agents_inactive import (
    stop_when_watched_agents_inactive,
)
from roboz.shed.tools.snapshot_conversations import snapshot_conversations
from roboz.shed.sandbox import Sandbox
from roboz.deployment import (
    Capability,
    ToolLabel,
    SkillLabel,
    SkillLoading,
    DeployableAgent,
    RequiredAttributeType,
    RequiredAttributes,
)
from roboz.llm import EndpointLike, LLMEndpoint, LLMEndpointRoute, MockLLMEndpoint
from roboz.runtime import EventPipe
from roboz.skill import Skill
from roboz.tooling import Tool


class SafeScripts(Capability):
    """Bind trusted Bash scripts locally or through a private Linux host socket.

    The deployment must protect the script directory and its helper files from
    agent writes. Scripts run with the local or serving process's privileges.
    Remote execution settings and working directories belong to the host.
    """

    def __init__(
        self,
        *,
        label: ToolLabel = ToolLabel("safe_scripts"),
        scripts_dir: Path | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        env_allowlist: tuple[str, ...] = (),
        socket_path: Path | None = None,
    ) -> None:
        """Keep configuration for fresh runtime bindings on each build."""
        super().__init__(label=label)
        self.scripts_dir = scripts_dir
        self.timeout_s = timeout_s
        self.max_output_bytes = max_output_bytes
        self.env_allowlist = env_allowlist
        self.socket_path = socket_path
        self._execution_lock: LockType = Lock()
        if (self.scripts_dir is None) == (self.socket_path is None):
            raise ValueError("supply exactly one of scripts_dir or socket_path")
        ExecutionPolicy(self.timeout_s, self.max_output_bytes, self.env_allowlist)
        if self.socket_path is not None:
            require_linux_transport()
            if (self.timeout_s, self.max_output_bytes, self.env_allowlist) != (
                DEFAULT_TIMEOUT_S,
                DEFAULT_MAX_OUTPUT_BYTES,
                (),
            ):
                raise ValueError("remote execution settings belong to the host service")

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require the sandbox layout only for local execution."""
        return {"sandbox": Sandbox} if self.socket_path is None else {}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Tool, ...]:
        """Bind a fresh script tool to this run's events and execution target."""
        ctx: SafeScriptContext | RemoteScriptContext
        if self.socket_path is not None:
            ctx = RemoteScriptContext(ScriptSocketDependency(self.socket_path), pipe)
        else:
            assert self.scripts_dir is not None
            sandbox = cast(Sandbox, agent.sandbox)
            ctx = SafeScriptContext(
                scripts_dir=self.scripts_dir,
                cwd=sandbox.resolved_root,
                pipe=pipe,
                timeout_s=self.timeout_s,
                max_output_bytes=self.max_output_bytes,
                env_allowlist=self.env_allowlist,
                execution_lock=self._execution_lock,
            )
        return (run_shell_script(ctx).copy(),)


_ENDPOINT_TYPES = (LLMEndpoint, MockLLMEndpoint, LLMEndpointRoute)
_AGENT_ENDPOINT_REQUIRED: RequiredAttributes = {
    "agent_endpoint": _ENDPOINT_TYPES,
}


class Email(Capability):
    """Bind email guidance and tools as one complete skill.

    Supply a configured service, such as ProtonBridgeEmailService. Credential
    loading belongs to the application; building does not contact the provider.
    """

    def __init__(
        self,
        *,
        label: SkillLabel = SkillLabel("email", loading=SkillLoading.AUTOMATIC),
        service: EmailService,
        timeout_s: float = DEFAULT_EMAIL_OPERATION_TIMEOUT_S,
        prompt_before_inbox_read: bool = False,
    ) -> None:
        """Keep configuration for fresh runtime bindings on each build."""
        super().__init__(label=label)
        self.service = service
        self.timeout_s = timeout_s
        self.prompt_before_inbox_read = prompt_before_inbox_read

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require the sandbox for draft attachments and downloaded files."""
        return {"sandbox": Sandbox}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Skill, ...]:
        """Bind the existing email factory and its guidance to this agent's pipe."""
        sandbox = cast(Sandbox, agent.sandbox)
        tools = get_work_with_email(
            service=self.service,
            **sandbox.permissions().tool_options(pipe),
            is_cancelled=lambda: pipe.cancelled,
            timeout_s=self.timeout_s,
            prompt_before_inbox_read=self.prompt_before_inbox_read,
        )
        return (email_skill.copy(tools=tools),)


class Filesystem(Capability):
    """Bind guarded file commands and patches as one complete filesystem skill.

    The skill label defaults to automatic loading. A selectable label also
    permits the owning agent to choose on-demand loading or disable the skill.
    """

    def __init__(
        self,
        *,
        label: SkillLabel = SkillLabel("filesystem", loading=SkillLoading.AUTOMATIC),
    ) -> None:
        """Keep configuration for fresh runtime bindings on each build."""
        super().__init__(label=label)

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require the owning agent's configured sandbox."""
        return {"sandbox": Sandbox}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Skill, ...]:
        """Construct both tool chains with this agent's permissions and event pipe."""
        sandbox = cast(Sandbox, agent.sandbox)
        skill = filesystem_skill(
            FilesystemContext(permissions=sandbox.permissions(), pipe=pipe)
        )
        return (skill,)


class Compactification(Capability):
    """Automatic context compaction, bound to the selected or owning endpoint."""

    def __init__(
        self,
        *,
        label: ToolLabel = ToolLabel("compactification", default=True),
        endpoint: EndpointLike | None = None,
        threshold_percent: float = DEFAULT_THRESHOLD_PERCENT,
        timeout_s: float | None = None,
    ) -> None:
        """Keep configuration for fresh runtime bindings on each build."""
        super().__init__(label=label)
        self.endpoint = endpoint
        self.threshold_percent = threshold_percent
        self.timeout_s = timeout_s

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require the owner's endpoint only when no override is configured."""
        return _AGENT_ENDPOINT_REQUIRED if self.endpoint is None else {}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Tool, ...]:
        """Use the configured endpoint, falling back to the agent's endpoint."""
        endpoint = (
            self.endpoint
            if self.endpoint is not None
            else cast(EndpointLike, agent.agent_endpoint)
        )
        return (
            get_compactify_messages_when_needed_tool(
                endpoint=endpoint,
                threshold_percent=self.threshold_percent,
                timeout_s=self.timeout_s,
                pipe=pipe,
            ),
        )


class ConversationSnapshots(Capability):
    """Automatic bounded snapshots of the selected agents' conversations.

    The endpoint defaults to the owning agent's model. Timeout is in seconds;
    summary tolerance is a finite, non-negative percentage above max_chars.
    """

    def __init__(
        self,
        *,
        label: ToolLabel = ToolLabel("conversation_snapshots", default=True),
        endpoint: EndpointLike | None = None,
        token_growth_threshold: int = 20000,
        max_chars: int = 8000,
        max_chars_tolerance_percent: float = DEFAULT_MAX_CHARS_TOLERANCE_PERCENT,
        timeout_s: float = 300.0,
    ) -> None:
        """Keep configuration for fresh runtime bindings on each build."""
        super().__init__(label=label)
        self.endpoint = endpoint
        self.token_growth_threshold = token_growth_threshold
        self.max_chars = max_chars
        self.max_chars_tolerance_percent = max_chars_tolerance_percent
        self.timeout_s = timeout_s
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

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Tool, ...]:
        """Bind snapshotting to its chosen model and the owning agent's pipe."""
        sandbox = cast(Sandbox, agent.sandbox)
        watched_agent_names = cast(Collection[str], agent.watched_agent_names)
        endpoint = (
            self.endpoint
            if self.endpoint is not None
            else cast(EndpointLike, agent.agent_endpoint)
        )
        return (
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


class MemoryConsolidation(Capability):
    """Automatic consolidation of pending snapshots into durable memory.

    The endpoint defaults to the owning agent's model. Age and timeout are in
    seconds; summary tolerance is a finite, non-negative percentage above max_chars.
    """

    def __init__(
        self,
        *,
        label: ToolLabel = ToolLabel("memory_consolidation", default=True),
        endpoint: EndpointLike | None = None,
        min_pending_snapshots: int = 3,
        max_pending_age_seconds: float = 86400.0,
        max_chars: int = 12000,
        max_chars_tolerance_percent: float = DEFAULT_MAX_CHARS_TOLERANCE_PERCENT,
        timeout_s: float = 300.0,
    ) -> None:
        """Keep configuration for fresh runtime bindings on each build."""
        super().__init__(label=label)
        self.endpoint = endpoint
        self.min_pending_snapshots = min_pending_snapshots
        self.max_pending_age_seconds = max_pending_age_seconds
        self.max_chars = max_chars
        self.max_chars_tolerance_percent = max_chars_tolerance_percent
        self.timeout_s = timeout_s
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

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Tool, ...]:
        """Bind consolidation to its chosen model and the owning agent's pipe."""
        sandbox = cast(Sandbox, agent.sandbox)
        watched_agent_names = cast(Collection[str], agent.watched_agent_names)
        endpoint = (
            self.endpoint
            if self.endpoint is not None
            else cast(EndpointLike, agent.agent_endpoint)
        )
        return (
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


class ArtifactRetention(Capability):
    """Automatic retention limits for project logs, snapshots, and memory."""

    def __init__(
        self,
        *,
        label: ToolLabel = ToolLabel("artifact_retention", default=True),
        max_log_files: int = 500,
        max_snapshot_files: int = 100,
        max_memory_files: int = 10,
    ) -> None:
        """Keep configuration for fresh runtime bindings on each build."""
        super().__init__(label=label)
        self.max_log_files = max_log_files
        self.max_snapshot_files = max_snapshot_files
        self.max_memory_files = max_memory_files

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require the owning agent's project sandbox."""
        return {"sandbox": Sandbox}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Tool, ...]:
        """Bind retention to the selected project without requiring a model."""
        sandbox = cast(Sandbox, agent.sandbox)
        return (
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


class MaintenanceCadence(Capability):
    """Wait between cycles while watched conversations are active; stop when idle.

    Place this after maintenance capabilities so their work runs before waiting.
    Waiting observes the owning agent's cancellation independently of any model.
    """

    def __init__(
        self,
        *,
        label: ToolLabel = ToolLabel("maintenance_cadence", default=True),
        seconds: float = 120.0,
    ) -> None:
        """Keep configuration for fresh runtime bindings on each build."""
        super().__init__(label=label)
        self.seconds = seconds

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Require the project and conversations observed between cycles."""
        return {
            "sandbox": Sandbox,
            "watched_agent_names": Collection,
        }

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Tool, ...]:
        """Bind cadence and inactive-agent stopping to cancellation state."""
        sandbox = cast(Sandbox, agent.sandbox)
        watched_agent_names = cast(Collection[str], agent.watched_agent_names)
        return (
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
