"""Discover and run operator-installed Bash scripts locally or on a Linux host."""

from _thread import LockType
from dataclasses import dataclass, field
from pathlib import Path

from roboz.dependencies import ExecutableDependency, ExternalDependency
from roboz.models import Message
from roboz.runtime import EventPipe
from roboz.tooling.context import HasExternalDependencies
from roboz.tooling.decorators import factory

from .client import (
    ScriptSocketDependency as ScriptSocketDependency,
    collect,
    request_script,
)
from .protocol import (
    ExecutionPolicy,
    ReservedScriptEnv as ReservedScriptEnv,
    RunShellScriptInput as RunShellScriptInput,
    ScriptEntry as ScriptEntry,
    ShellScriptResult as ShellScriptResult,
)
from .server import Scripts, serve_scripts as serve_scripts


@dataclass(frozen=True)
class SafeScriptContext(HasExternalDependencies):
    """Caller-owned script boundary, execution policy, and current event pipe."""

    scripts_dir: Path
    cwd: Path
    pipe: EventPipe
    timeout_s: float
    max_output_bytes: int
    env_allowlist: tuple[str, ...]
    execution_lock: LockType = field(repr=False, compare=False)
    bash: ExecutableDependency = field(
        default_factory=lambda: ExecutableDependency("bash")
    )

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report Bash without resolving or launching it."""
        return (self.bash,)


@dataclass(frozen=True)
class RemoteScriptContext(HasExternalDependencies):
    """Bind the host helper to this run's event and cancellation pipe."""

    helper: ScriptSocketDependency
    pipe: EventPipe

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report the helper without contacting it."""
        return (self.helper,)


@factory
def run_shell_script(
    input: RunShellScriptInput,
    messages: list[Message],
    ctx: SafeScriptContext | RemoteScriptContext,
) -> ShellScriptResult:
    """List trusted shell scripts when `script` is omitted, or run one by relative path.

    Local scripts run with this application's privileges; remote scripts run
    with the serving process's privileges. They receive no arguments or
    interactive input; their output is streamed to the user.
    """
    del messages
    if isinstance(ctx, RemoteScriptContext):
        return request_script(
            input, ctx.helper, ctx.pipe.emit_script_output, ctx.pipe.raise_if_cancelled
        )
    runner = Scripts(
        ctx.scripts_dir,
        ctx.cwd,
        ExecutionPolicy(ctx.timeout_s, ctx.max_output_bytes, ctx.env_allowlist),
        ctx.execution_lock,
        ctx.bash,
    )
    return collect(
        runner.events(input, ctx.pipe.raise_if_cancelled), ctx.pipe.emit_script_output
    )
