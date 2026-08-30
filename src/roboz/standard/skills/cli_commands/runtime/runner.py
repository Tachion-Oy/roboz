"""Run guarded command payloads."""

import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from select import select
from types import MappingProxyType
from typing import Callable, Protocol

from roboz import FactoryCtx
from roboz.exceptions import ExternalCallCancelledError
from roboz.tooling import (
    ExecutableDependency,
    ExternalDependency,
    ExternalDependencySource,
    ToolDependency,
)
from roboz.models import Message, Str
from roboz.models import Severity, Truncation, TruncationSpec
from roboz import factory

from roboz.standard.models.core import CommandReady, GuardFilesResult, GuardStatus
from roboz.standard.skills.cli_commands.inputs import RunFileCommands
from roboz.standard.skills.cli_commands.utilities.constants import (
    ERR_COMMAND_NOT_FOUND,
    ERR_EXIT_NONZERO,
    ERR_EXIT_NONZERO_NO_OUTPUT,
    ERR_OUTPUT_TOO_LARGE,
    ERR_TIMEOUT,
    MAX_COMMAND_OUTPUT_CHARS,
    PIPE_OUTPUT_TO_NEXT_COMMAND,
    PIPE_STDIN_FROM_PREVIOUS_COMMAND,
    SUBPROCESS_TIMEOUT_SECONDS,
    SUCCESS_NO_OUTPUT,
)
from roboz.standard.skills.cli_commands.utilities.formatting import _framed_cli_output


_STREAM_POLL_INTERVAL_SECONDS = 0.1
_TERMINATE_GRACE_SECONDS = 1.0


class StreamedProcessRunner(Protocol):
    """Callable boundary for a streamed, cancellation-aware subprocess."""

    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path | None,
        stdin: str | None,
        timeout: float | None,
        poll_interval_seconds: float,
        terminate_grace_seconds: float,
        is_cancelled: Callable[[], bool],
        on_output: Callable[[str], None],
    ) -> subprocess.CompletedProcess[str]: ...


class PopenStreamedProcessRunner(StreamedProcessRunner):
    """Stream and control one subprocess created through ``Popen``."""

    __slots__ = ()

    def __call__(
        self,
        *,
        argv: list[str],
        cwd: Path | None,
        stdin: str | None,
        timeout: float | None,
        poll_interval_seconds: float,
        terminate_grace_seconds: float,
        is_cancelled: Callable[[], bool],
        on_output: Callable[[str], None],
    ) -> subprocess.CompletedProcess[str]:
        deadline = None if timeout is None else time.monotonic() + timeout
        parts: list[str] = []
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE if stdin is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if process.stdout is None:
                raise OSError("subprocess did not expose stdout")
            self._write_stdin(process, stdin)
            self._stream_output(
                process=process,
                argv=argv,
                timeout=timeout,
                deadline=deadline,
                poll_interval_seconds=poll_interval_seconds,
                is_cancelled=is_cancelled,
                parts=parts,
                on_output=on_output,
            )
            returncode = process.wait()
            return subprocess.CompletedProcess(
                args=argv,
                returncode=returncode,
                stdout="".join(parts),
                stderr=None,
            )
        finally:
            self._close(process, terminate_grace_seconds)

    @staticmethod
    def _write_stdin(process: subprocess.Popen[str], stdin: str | None) -> None:
        if stdin is None:
            return
        if process.stdin is None:
            raise OSError("subprocess did not expose stdin")
        process.stdin.write(stdin)
        process.stdin.close()

    def _stream_output(
        self,
        *,
        process: subprocess.Popen[str],
        argv: list[str],
        timeout: float | None,
        deadline: float | None,
        poll_interval_seconds: float,
        is_cancelled: Callable[[], bool],
        parts: list[str],
        on_output: Callable[[str], None],
    ) -> None:
        assert process.stdout is not None
        stdout_open = True
        while process.poll() is None or stdout_open:
            if is_cancelled():
                raise ExternalCallCancelledError("subprocess execution was cancelled")
            wait_seconds = self._next_poll_delay(
                argv=argv,
                timeout=timeout,
                deadline=deadline,
                poll_interval_seconds=poll_interval_seconds,
            )
            if not stdout_open:
                time.sleep(wait_seconds)
                continue
            ready, _, _ = select([process.stdout], [], [], wait_seconds)
            if not ready:
                continue
            line = process.stdout.readline()
            if line == "":
                stdout_open = False
                continue
            parts.append(line)
            on_output(line)

    @staticmethod
    def _next_poll_delay(
        *,
        argv: list[str],
        timeout: float | None,
        deadline: float | None,
        poll_interval_seconds: float,
    ) -> float:
        if deadline is None:
            return poll_interval_seconds
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            assert timeout is not None
            raise subprocess.TimeoutExpired(argv, timeout)
        return min(poll_interval_seconds, remaining)

    @staticmethod
    def _close(
        process: subprocess.Popen[str] | None, terminate_grace_seconds: float
    ) -> None:
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()


_STREAMED_PROCESS_RUNNER = PopenStreamedProcessRunner()


def run_cli_argv(
    argv: list[str], cwd: Path, stdin: str | None, command_line: str
) -> tuple[bool, str]:
    """Run argv. Return (True, output) on success or (False, formatted error)."""
    result = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        input=stdin,
    )
    out = (result.stdout or "") + (result.stderr or "")
    return _format_run_result(result.returncode, out, command_line)


def _format_run_result(
    returncode: int, out: str, command_line: str
) -> tuple[bool, str]:
    """Shared (ok, output|formatted-error) shaping for argv runners."""
    if returncode == 0:
        return True, out
    msg = (
        ERR_EXIT_NONZERO.format(code=returncode, out=out).strip()
        if out.strip()
        else ERR_EXIT_NONZERO_NO_OUTPUT.format(code=returncode, command=command_line)
    )
    return False, msg


def run_cli_argv_streamed(
    argv: list[str],
    cwd: Path,
    on_output: Callable[[str], None],
    command_line: str,
    *,
    timeout: float | None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """Run argv, streaming merged stdout/stderr line-by-line to ``on_output``.

    ``timeout=None`` runs to completion with no cap; otherwise the script is killed
    once ``timeout`` seconds elapse (even while it is silent) and
    :class:`subprocess.TimeoutExpired` is raised. The return value matches
    :func:`run_cli_argv`: ``(True, raw_output)`` or ``(False, formatted_error)``.

    POSIX-only: uses ``select`` on the child's stdout, like
    ``roboz.runtime.io._read_line_with_timeout``.
    """
    result = _STREAMED_PROCESS_RUNNER(
        argv=argv,
        cwd=cwd,
        stdin=None,
        timeout=timeout,
        poll_interval_seconds=_STREAM_POLL_INTERVAL_SECONDS,
        terminate_grace_seconds=_TERMINATE_GRACE_SECONDS,
        is_cancelled=is_cancelled or (lambda: False),
        on_output=on_output,
    )
    return _format_run_result(result.returncode, result.stdout or "", command_line)


def argv_from_guard_result(
    input: GuardFilesResult,
) -> tuple[list[str], Path, str | None, str]:
    """Build subprocess argv, cwd, stdin, and label from a guard result."""
    ready_value = command_ready_from_guard_result(input)
    return (
        ready_value.argv,
        ready_value.base_workdir,
        ready_value.stdin,
        ready_value.display_command,
    )


def command_ready_from_guard_result(input: GuardFilesResult) -> CommandReady:
    """Return the resolver-validated command payload carried through the guard."""

    ready_value = input.items[0].value
    if not isinstance(ready_value, CommandReady):
        raise ValueError("expected CommandReady guard payload")
    return ready_value


def _accumulate_error(
    new_input: RunFileCommands,
    command_line: str,
    message: str,
    truncation: TruncationSpec,
) -> Str | RunFileCommands:
    """Stop the chain on failure, retaining prior AND-chain output."""
    if new_input.chain != "and":
        return Str(value=message, truncation=truncation)
    framed = _framed_cli_output(command_line, message)
    combined = (new_input.accumulated_output or "") + framed + "\n"
    return Str(value=combined.strip(), truncation=truncation)


def _format_step_body(
    *,
    chain: str,
    is_last_step: bool,
    has_previous_step: bool,
    body: str,
) -> str:
    if chain != "pipe":
        return body
    parts: list[str] = []
    if has_previous_step:
        parts.append(PIPE_STDIN_FROM_PREVIOUS_COMMAND)
    parts.append(body if is_last_step else PIPE_OUTPUT_TO_NEXT_COMMAND)
    return "\n".join(parts)


def _continue_chain(
    *,
    new_input: RunFileCommands,
    combined: str,
    out: str,
) -> RunFileCommands:
    """Advance to the next file command in the chain."""
    new_input.file_commands = new_input.file_commands[1:]
    if new_input.chain == "pipe":
        new_input.file_commands[0].stdin = out
    new_input.accumulated_output = combined
    new_input.truncation = Truncation(threshold=0, severity=Severity.REMOVE)
    return new_input


def _oversized_output_result(
    *, command_line: str, actual_chars: int, truncation: TruncationSpec
) -> Str:
    """Fail closed when command output is too large for safe chaining/persistence."""
    message = ERR_OUTPUT_TOO_LARGE.format(
        actual_chars=actual_chars, max_chars=MAX_COMMAND_OUTPUT_CHARS
    )
    body = _framed_cli_output(command_line, message)
    return Str(value=body.strip(), truncation=truncation)


@dataclass(frozen=True)
class ExecutableCommandCatalog(ExternalDependencySource):
    """Immutable command implementations and their inspectable dependencies."""

    bindings: Mapping[str, ToolDependency[ExecutableDependency]]

    def __post_init__(self) -> None:
        bindings = dict(self.bindings)
        for command_name, binding in bindings.items():
            if command_name != binding.resource.executable:
                raise ValueError(
                    "command binding must use its executable name: "
                    f"{command_name!r} != {binding.resource.executable!r}"
                )
        object.__setattr__(self, "bindings", MappingProxyType(bindings))

    @classmethod
    def from_names(cls, names: Iterable[str]) -> "ExecutableCommandCatalog":
        bindings: dict[str, ToolDependency[ExecutableDependency]] = {}
        for name in names:
            if name in bindings:
                raise ValueError(f"duplicate executable command: {name}")
            bindings[name] = ToolDependency(ExecutableDependency(name))
        return cls(bindings)

    def binding_for(self, command_name: str) -> ToolDependency[ExecutableDependency]:
        try:
            return self.bindings[command_name]
        except KeyError as error:
            raise RuntimeError(
                f"resolved command has no implementation: {command_name}"
            ) from error

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        return tuple(binding.resource for binding in self.bindings.values())


@dataclass(frozen=True)
class ExecuteFileCommandCtx(FactoryCtx):
    truncation: TruncationSpec
    commands: ExecutableCommandCatalog


@factory
def execute_file_command(
    input: GuardFilesResult,
    messages: list[Message],
    ctx: ExecuteFileCommandCtx,
) -> Str | RunFileCommands:
    """Run a guarded command subprocess after path guards pass."""
    truncation = ctx.truncation
    if input.status != GuardStatus.ALLOWED:
        return Str(
            value=input.message
            or f"internal error: unexpected guard status {input.status}",
            truncation=truncation,
        )
    if not isinstance(input.original_input, RunFileCommands):
        return Str(
            value="internal error: expected RunFileCommands original_input",
            truncation=truncation,
        )

    ready = command_ready_from_guard_result(input)
    argv, cwd, stdin, command_line = argv_from_guard_result(input)
    new_input = input.original_input

    try:
        executable = ctx.commands.binding_for(ready.command_name).resource
        argv = [str(executable.require()), *argv[1:]]
        ok, out = run_cli_argv(argv, cwd, stdin, command_line)
        if len(out) > MAX_COMMAND_OUTPUT_CHARS:
            return _oversized_output_result(
                command_line=command_line, actual_chars=len(out), truncation=truncation
            )
        if not ok:
            return _accumulate_error(new_input, command_line, out, truncation)

        body = out if out.strip() else SUCCESS_NO_OUTPUT.format(command=command_line)
        is_last_step = len(new_input.file_commands) == 1
        has_previous_step = bool((new_input.accumulated_output or "").strip())
        body = _format_step_body(
            chain=new_input.chain,
            is_last_step=is_last_step,
            has_previous_step=has_previous_step,
            body=body,
        )
        framed = _framed_cli_output(command_line, body)
        combined = (new_input.accumulated_output or "") + framed + "\n"

        if is_last_step:
            return Str(value=combined.strip(), truncation=truncation)
        return _continue_chain(new_input=new_input, combined=combined, out=out)

    except Exception as e:
        match e:
            case subprocess.TimeoutExpired():
                msg = ERR_TIMEOUT.format(timeout=SUBPROCESS_TIMEOUT_SECONDS)
            case FileNotFoundError():
                msg = ERR_COMMAND_NOT_FOUND.format(cmd=argv[0])
            case _:
                msg = str(e)
        return _accumulate_error(new_input, command_line, msg, truncation)
