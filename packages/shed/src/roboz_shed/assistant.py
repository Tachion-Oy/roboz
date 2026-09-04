"""Small assistant composition, with capabilities supplied by the caller."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from roboz import Agent, Skill, Tool, stop
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe, EventSink, Output

from .models import ActionVerdict, Operation, PermissionRule
from .skills import cli_skill, file_editing
from .tools import get_apply_patch, get_run_file_command
from .tools.cli_commands.run_file_command import FILE_COMMANDS_READ


class _ToolOptions(TypedDict):
    base: Path
    default_verdict: ActionVerdict
    takes_precedence: ActionVerdict
    allow_rules: list[PermissionRule]
    deny_rules: list[PermissionRule]
    ask_rules: list[PermissionRule]
    pipe: EventPipe


@dataclass(frozen=True)
class WorkspacePermissions:
    """An explicit base and permission policy; no application directory layout."""

    base: Path
    allow: tuple[PermissionRule, ...] = ()
    deny: tuple[PermissionRule, ...] = ()
    ask: tuple[PermissionRule, ...] = ()
    default_verdict: ActionVerdict = ActionVerdict.deny
    takes_precedence: ActionVerdict = ActionVerdict.deny

    @classmethod
    def local(cls, base: Path) -> "WorkspacePermissions":
        """Allow operations inside this root; deny paths outside it.

        These tool guards are not an operating-system sandbox. The demo uses
        read commands and the Python patch tool, without arbitrary shell access.
        """
        root = base.resolve()

        return cls(
            base=root,
            allow=(
                PermissionRule(
                    "**", {Operation.READ, Operation.CREATE, Operation.DELETE}
                ),
            ),
        )


def build_assistant(
    *,
    endpoint: EndpointLike,
    workspace: WorkspacePermissions,
    tools: Sequence[Tool | Sequence[Tool]] = (),
    tool_builders: Sequence[Callable[[EventPipe], Sequence[Tool]]] = (),
    skills: Sequence[Skill] = (),
    event_sinks: Sequence[EventSink] = (),
    name: str = "assistant",
    system_prompt: str = "Complete the user's task using the available tools, then call stop.",
    initial_messages: Sequence[Path | str] = (),
    interaction_mode: Output = Output.CLI,
) -> Agent:
    """Build a file assistant with explicitly supplied additional capabilities."""
    pipe = EventPipe(event_sinks=list(event_sinks))
    common: _ToolOptions = {
        "base": workspace.base,
        "default_verdict": workspace.default_verdict,
        "takes_precedence": workspace.takes_precedence,
        "allow_rules": list(workspace.allow),
        "deny_rules": list(workspace.deny),
        "ask_rules": list(workspace.ask),
        "pipe": pipe,
    }
    return Agent(
        name=name,
        agent_endpoint=endpoint,
        system_prompt=f"{system_prompt}\nFile tool base: {workspace.base.resolve()}",
        initial_messages=initial_messages,
        interaction_mode=interaction_mode,
        event_pipe=pipe,
        tools=[
            get_run_file_command(**common, command_specs=FILE_COMMANDS_READ),
            get_apply_patch(**common),
            *tools,
            *(builder(pipe) for builder in tool_builders),
            stop,
        ],
        skills=skills,
        auto_loaded_skills=[cli_skill, file_editing],
    )


__all__ = ["WorkspacePermissions", "build_assistant"]
