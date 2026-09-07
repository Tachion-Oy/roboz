"""Configured tool and skill capabilities for Shed agent definitions."""

from dataclasses import dataclass

from roboshed.skills import cli_skill, file_editing
from roboshed.tools import (
    get_apply_patch,
    get_compactify_messages_when_needed_tool,
    get_run_file_command,
)
from roboshed.tools.cli_commands.run_file_command import FILE_COMMANDS_READ
from roboshed.tools.compactification import DEFAULT_THRESHOLD_PERCENT
from roboshed.workspace import WorkspacePermissions
from roboz.deployment import Capability
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe


@dataclass(frozen=True)
class FileCommands:
    """Guarded read commands, optionally accompanied by their orientation skill."""

    permissions: WorkspacePermissions
    auto_load_skill: bool = True

    def build(self, pipe: EventPipe, agent_endpoint: EndpointLike | None) -> Capability:
        """Build a read-tool chain using this agent's pipe and selected policy."""
        return Capability(
            tools=(
                get_run_file_command(
                    **self.permissions.tool_options(pipe),
                    command_specs=FILE_COMMANDS_READ,
                ),
            ),
            auto_loaded_skills=(cli_skill,) if self.auto_load_skill else (),
        )


@dataclass(frozen=True)
class FileEditing:
    """Literal patch editing with caller-selected file permissions."""

    permissions: WorkspacePermissions
    auto_load_skill: bool = True

    def build(self, pipe: EventPipe, agent_endpoint: EndpointLike | None) -> Capability:
        """Build patch editing and optional orientation against the owning pipe."""
        return Capability(
            tools=(get_apply_patch(**self.permissions.tool_options(pipe)),),
            auto_loaded_skills=(file_editing,) if self.auto_load_skill else (),
        )


@dataclass(frozen=True)
class Compactification:
    """Automatic context compaction, bound to the selected or owning endpoint."""

    endpoint: EndpointLike | None = None
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT
    timeout_s: float | None = None

    def build(self, pipe: EventPipe, agent_endpoint: EndpointLike | None) -> Capability:
        """Build a fresh compaction tool sharing the agent's runtime controls."""
        endpoint = self.endpoint if self.endpoint is not None else agent_endpoint
        if endpoint is None:
            raise ValueError("compactification requires an endpoint")
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
