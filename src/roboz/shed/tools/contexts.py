"""Concrete contexts and caller-owned state for Shed tools."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from roboz.shed.models import ActionVerdict, PermissionRule
from roboz.dependencies import ExternalDependency
from roboz.llm import EndpointLike
from roboz.models.truncation import TruncationSpec
from roboz.runtime import EventPipe
from roboz.tooling.context import HasExternalDependencies

if TYPE_CHECKING:
    from roboz.shed.tools.cli_commands.utilities.cmd_spec import CmdSpec
    from roboz.shed.tools.runner import ExecutableCommandCatalog
    from roboz.shed.tools.email.contracts import EmailService


DEFAULT_MAX_CHARS_TOLERANCE_PERCENT: Final[float] = 15.0


@dataclass(frozen=True, kw_only=True)
class GuardContext:
    """Permission rules and caller-owned controls for a guarded operation."""

    base: Path | None
    takes_precedence: ActionVerdict
    deny: list[PermissionRule]
    allow: list[PermissionRule]
    ask: list[PermissionRule]
    default_verdict: ActionVerdict
    command_specs: Sequence[CmdSpec] = ()
    pipe: EventPipe | None = None


@dataclass(frozen=True, kw_only=True)
class FileCommandResolverContext:
    """Command specifications and permission text for file-command resolution."""

    base: Path
    specs: Sequence[CmdSpec]
    allow_rules: list[PermissionRule]
    deny_rules: list[PermissionRule]
    ask_rules: list[PermissionRule]
    takes_precedence: ActionVerdict
    default_verdict: ActionVerdict


@dataclass(frozen=True, kw_only=True)
class FileCommandExecutionContext(HasExternalDependencies):
    """Executable bindings and output limits for permitted file commands."""

    truncation: TruncationSpec
    commands: ExecutableCommandCatalog

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report declared commands without resolving or running executables."""
        return self.commands.external_dependencies()


@dataclass
class CompactionState:
    """Successful compactions shared by tools bound to the same context."""

    count: int = 0


@dataclass(frozen=True, kw_only=True)
class CompactionContext(HasExternalDependencies):
    """Compaction settings and state, fresh for each constructed context."""

    endpoint: EndpointLike
    threshold_percent: float
    system_prompt: str
    skill_message: str
    pipe: EventPipe | None = None
    timeout_s: float | None = None
    state: CompactionState = field(default_factory=CompactionState)

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report the summary endpoint without initializing its client."""
        return self.endpoint.external_dependencies()


@dataclass(frozen=True, kw_only=True)
class SnapshotConversationsContext(HasExternalDependencies):
    """Configured artifact locations, summary endpoint, and processing limits."""

    endpoint: EndpointLike
    conversation_root: Path
    snapshot_root: Path
    memory_root: Path
    agent_names: set[str]
    token_growth_threshold: int
    max_chars: int
    max_chars_tolerance_percent: float = DEFAULT_MAX_CHARS_TOLERANCE_PERCENT
    timeout_s: float | None = None
    pipe: EventPipe | None = None

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report the summary endpoint without reading artifacts or calling it."""
        return self.endpoint.external_dependencies()


@dataclass(frozen=True, kw_only=True)
class ConsolidateMemoryContext(HasExternalDependencies):
    """Configured artifact locations, summary endpoint, and processing limits."""

    endpoint: EndpointLike
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

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report the summary endpoint without reading artifacts or calling it."""
        return self.endpoint.external_dependencies()


@dataclass(frozen=True, kw_only=True)
class PurgeFilesContext:
    """Artifact roots, selection pattern, and retention limits."""

    pattern: str
    max_files: int
    folders: list[Path]
    prune_empty_directories: bool = False


@dataclass(frozen=True, kw_only=True)
class StopWhenWatchedAgentsInactiveContext:
    """Watched conversations used to decide when maintenance may stop."""

    conversation_root: Path
    agent_names: set[str]


@dataclass(frozen=True, kw_only=True)
class SleepBetweenRunsContext:
    """Wait settings for observing activity between maintenance cycles."""

    seconds: float
    is_cancelled: Callable[[], bool] | None = None
    conversation_root: Path | None = None
    agent_names: set[str] = field(default_factory=set)


@dataclass(frozen=True, kw_only=True)
class EmailContext(HasExternalDependencies):
    """Email service, caller-owned controls, and inbox confirmation policy."""

    service: EmailService
    is_cancelled: Callable[[], bool]
    timeout_s: float
    pipe: EventPipe | None
    prompt_before_inbox_read: bool = False

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report the email service without probing or accessing a mailbox."""
        return self.service.external_dependencies()
