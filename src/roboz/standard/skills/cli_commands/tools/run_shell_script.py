"""Implementation of the ``run_shell_script`` CLI tool.

The agent may only run a script that resolves to a path **inside** a scripts
directory fixed when the tool is built. Two chained tools enforce this:

- ``resolve_shell_script`` (agent-facing, named ``run_shell_script``) resolves the
  ``script`` path with the same rules as the file CLI tools (relative to a configured
  tool base, or absolute if already absolute), then validates that the result lives
  inside the scripts directory and is an existing ``.sh`` file. On success it emits a
  :class:`ScriptReady` payload; on any failure it returns a refusal :class:`Str`.
- ``execute_shell_script`` is chained to the resolver with a ``chain_condition`` that
  only fires for a :class:`ScriptReady`. If the containment check fails the resolver
  returns a ``Str`` instead, the condition is false, the chain breaks, and the
  refusal is the final output.

Safety rests on the consumer pointing the scripts directory at a read-only location
(e.g. the workspace ``readonly/safe-scripts`` folder) the agent has no write tool
for, so it cannot plant its own script; the containment check then keeps it to those
scripts. What a script writes (and where) is the script's own concern - these are
hand-crafted files the agent cannot edit - so the tool does not try to constrain
script output.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from roboz import FactoryCtx
from roboz.models import Empty, Message, Str
from roboz.runtime import EventPipe
from roboz.models import TruncationSpec
from roboz.models import MessageKind, Role
from roboz import factory
from roboz.tooling import ExecutableDependency, ToolDependency
from roboz.tooling import Tool
from pydantic import ConfigDict, Field

from roboz.standard.identifiers import RUN_SHELL_SCRIPT_TOOL_NAME
from roboz.standard.skills.cli_commands.utilities.constants import (
    ERR_COMMAND_NOT_FOUND,
    ERR_TIMEOUT,
    SUCCESS_NO_OUTPUT,
)
from roboz.standard.skills.cli_commands.utilities.formatting import _framed_cli_output
from roboz.standard.skills.cli_commands.runtime.runner import run_cli_argv_streamed
from roboz.standard.skills.cli_commands.runtime.truncation import default_cli_truncation
from roboz.standard.skills.cli_commands.runtime.utils import resolve_tool_base

__all__ = [
    "RunShellScriptInput",
    "ScriptReady",
    "ShellScriptCtx",
    "ShellScriptExecCtx",
    "execute_shell_script",
    "get_run_shell_script",
    "resolve_shell_script",
]

_REFUSE = (
    "refused: {script!r} is not an existing .sh file in the safe scripts directory"
)

_DESCRIPTION = (
    "Run a shell script that the user has placed in the safe scripts directory. "
    "Provide `script` as a path relative to the tool base (for example "
    "'readonly/safe-scripts/deploy.sh' or 'readonly/safe-scripts/ci/lint.sh'), or as "
    "an absolute path. Only existing .sh files inside the safe scripts directory (or "
    "its subfolders) may run; nothing else is executed. Scripts take no arguments."
)


@dataclass(frozen=True, slots=True)
class ShellScriptCtx(FactoryCtx):
    """Immutable context for script resolution and containment checks.

    Frozen + slotted so the bound directories cannot be repointed after the tool is
    built; ``Factory.__call__`` captures this object in a closure, so neither fields
    nor the binding can be swapped by another process.
    """

    base: Path
    scripts_dir: Path
    bash: ToolDependency[ExecutableDependency] = ToolDependency(
        ExecutableDependency("bash")
    )


@dataclass(frozen=True, slots=True)
class ShellScriptExecCtx(FactoryCtx):
    pipe: EventPipe
    truncation: TruncationSpec


class RunShellScriptInput(Empty):
    script: str = Field(...)
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description=(
            "If set, kill the script after this many seconds. Leave unset to let the "
            "script run to completion with no time limit (the default)."
        ),
    )
    model_config = ConfigDict(extra="forbid")


class ScriptReady(Empty):
    """The "containment check passed" payload handed to the execute step."""

    argv: list[str] = Field(...)
    cwd: Path = Field(...)
    display: str = Field(...)
    timeout_seconds: float | None = Field(default=None)
    model_config = ConfigDict(extra="forbid")


@factory
def resolve_shell_script(
    input: RunShellScriptInput, messages: list[Message], ctx: ShellScriptCtx
) -> ScriptReady | Str:
    """Validate that the requested script resolves inside the safe scripts directory."""
    scripts_dir = ctx.scripts_dir.resolve()
    base = ctx.base.resolve()
    raw_script = Path(input.script)
    # Join then resolve so ".." and symlinks are followed to their real location
    # before the containment check.
    target = (
        raw_script.resolve()
        if raw_script.is_absolute()
        else (base / raw_script).resolve()
    )

    if (
        not target.is_relative_to(scripts_dir)
        or not target.is_file()
        or target.suffix != ".sh"
    ):
        return Str(value=_REFUSE.format(script=input.script))
    bash = ctx.bash.resource.require()
    return ScriptReady(
        argv=[str(bash), str(target)],
        cwd=base,
        display=f"bash {input.script}",
        timeout_seconds=input.timeout_seconds,
    )


@factory
def execute_shell_script(
    input: ScriptReady, messages: list[Message], ctx: ShellScriptExecCtx
) -> Str:
    """Run a validated shell script, streaming its output to the user as it appears."""

    message_started = False

    def stream_to_user(chunk: str) -> None:
        nonlocal message_started
        ctx.pipe.emit_script_output(chunk.rstrip("\n"))
        if not message_started:
            ctx.pipe.start_message()
            message_started = True
        ctx.pipe.emit_message_delta(chunk)

    def finalize_for_user(result: Str) -> Str:
        ctx.pipe.emit_message(
            Message(
                role=Role.ASSISTANT,
                content=result.value,
                message_kind=MessageKind.USER_NOTIFICATION,
            )
        )
        return result

    try:
        _ok, out = run_cli_argv_streamed(
            input.argv,
            input.cwd,
            stream_to_user,
            input.display,
            timeout=input.timeout_seconds,
            is_cancelled=lambda: ctx.pipe.cancelled or ctx.pipe.interrupted,
        )
    except subprocess.TimeoutExpired:
        return finalize_for_user(
            Str(
                value=_framed_cli_output(
                    input.display, ERR_TIMEOUT.format(timeout=input.timeout_seconds)
                ).strip(),
                truncation=ctx.truncation,
            )
        )
    except FileNotFoundError:
        return finalize_for_user(
            Str(
                value=_framed_cli_output(
                    input.display, ERR_COMMAND_NOT_FOUND.format(cmd=input.argv[0])
                ).strip(),
                truncation=ctx.truncation,
            )
        )

    body = out if out.strip() else SUCCESS_NO_OUTPUT.format(command=input.display)
    return finalize_for_user(
        Str(
            value=_framed_cli_output(input.display, body).strip(),
            truncation=ctx.truncation,
        )
    )


def get_run_shell_script(
    *,
    base: Path,
    scripts_dir: Path,
    pipe: EventPipe,
    truncation: TruncationSpec = default_cli_truncation(),
) -> list[Tool]:
    """Build the ``run_shell_script`` tool chain confined to ``scripts_dir``.

    The script argument follows the same path resolution rules as file CLI tools:
    relative to ``base`` unless absolute. Scripts may only run if they resolve inside
    ``scripts_dir`` (point this at a read-only location the agent cannot write to).
    What a script does with its output is the script's own concern.

    Returns the agent-facing resolver (named ``run_shell_script``) and its chained
    executor. The executor only fires when the resolver produced a
    :class:`ScriptReady`; any refusal breaks the chain and is returned as-is.
    """
    resolved_base = resolve_tool_base(base)
    resolve_tool = resolve_shell_script(
        ShellScriptCtx(
            base=resolved_base,
            scripts_dir=scripts_dir,
            bash=ToolDependency(ExecutableDependency("bash")),
        )
    ).copy(name=RUN_SHELL_SCRIPT_TOOL_NAME, description=_DESCRIPTION)
    exec_tool = execute_shell_script(
        ShellScriptExecCtx(pipe=pipe, truncation=truncation)
    ).copy(
        chained_to=resolve_tool, chain_condition=lambda x: isinstance(x, ScriptReady)
    )
    return [resolve_tool, exec_tool]
