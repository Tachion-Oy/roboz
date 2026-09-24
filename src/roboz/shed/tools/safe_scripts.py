"""Discover and run operator-installed Bash scripts from a protected directory."""

import codecs
import os
import select
import signal
import subprocess
import sys
import time
from _thread import LockType
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from roboz.dependencies import ExecutableDependency, ExternalDependency
from roboz.models import Empty, Message
from roboz.runtime import EventPipe
from roboz.tooling.context import HasExternalDependencies
from roboz.tooling.decorators import factory


class ReservedScriptEnv(StrEnum):
    """Process-control variables excluded from the script environment allowlist."""

    PATH = "PATH"
    BASH_ENV = "BASH_ENV"
    ENV = "ENV"
    SHELLOPTS = "SHELLOPTS"
    BASHOPTS = "BASHOPTS"
    LD_PRELOAD = "LD_PRELOAD"
    LD_LIBRARY_PATH = "LD_LIBRARY_PATH"
    PYTHONPATH = "PYTHONPATH"


class RunShellScriptInput(Empty):
    """Omit ``script`` to discover entrypoints, or select one relative path."""

    script: str | None = Field(default=None)
    model_config = ConfigDict(extra="forbid")


class ScriptEntry(BaseModel):
    """One discoverable entrypoint and its optional leading comment."""

    script: str
    description: str = ""
    model_config = ConfigDict(extra="forbid")


class ShellScriptResult(Empty):
    """Bounded outcome of discovery or a trusted script invocation."""

    status: Literal[
        "listed", "success", "refused", "busy", "failed", "timeout", "output_limit"
    ]
    scripts: list[ScriptEntry] = Field(default_factory=list)
    exit_code: int | None = None
    output: str = ""
    model_config = ConfigDict(extra="forbid")


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
    bash: ExecutableDependency = field(default_factory=lambda: ExecutableDependency("bash"))

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report Bash without resolving or launching it."""
        return (self.bash,)


def _valid_script(root: Path, name: str) -> Path | None:
    candidate_name = Path(name)
    if (
        not name
        or candidate_name.is_absolute()
        or PureWindowsPath(name).is_absolute()
        or ".." in candidate_name.parts
        or candidate_name.suffix != ".sh"
        or root.is_symlink()
    ):
        return None
    candidate = root / candidate_name
    current = root
    for part in candidate_name.parts:
        current /= part
        if current.is_symlink():
            return None
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root.resolve()):
        return None
    return candidate


def _description(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        if not stream.readline().startswith("#!"):
            stream.seek(0)
        for line in stream:
            if line.startswith("#"):
                description = line[1:].strip()
                if description:
                    return description[:512]
            elif line.strip():
                break
    return ""


def _list_scripts(root: Path) -> ShellScriptResult:
    if root.is_symlink():
        return ShellScriptResult(status="refused", output="script directory is a symlink")
    if not root.is_dir():
        return ShellScriptResult(
            status="listed", output="script directory is absent; install trusted .sh files"
        )
    entries = []
    for path in sorted(root.rglob("*.sh")):
        name = path.relative_to(root).as_posix()
        if _valid_script(root, name) is not None:
            entries.append(ScriptEntry(script=name, description=_description(path)))
    return ShellScriptResult(status="listed", scripts=entries)


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Give the script and its children two seconds to exit before killing them."""
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            process.poll()  # Reap the leader so it cannot keep the group alive.
            os.killpg(process.pid, 0)
            time.sleep(0.05)
        os.killpg(process.pid, signal.SIGKILL)


def _read_chunks(
    process: subprocess.Popen[bytes], pipe: EventPipe, timeout_s: float
) -> Iterator[bytes]:
    """Yield stdout chunks while polling for cancellation and the deadline."""
    assert process.stdout is not None
    deadline = time.monotonic() + timeout_s
    output_open = True
    while output_open or process.poll() is None:
        pipe.raise_if_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_s)
        readable, _, _ = select.select(
            [process.stdout] if output_open else [], [], [], min(0.1, remaining)
        )
        if not readable:
            continue
        chunk = os.read(process.stdout.fileno(), 4096)
        if not chunk:
            output_open = False
            continue
        yield chunk


def _monitor_script(
    process: subprocess.Popen[bytes], ctx: SafeScriptContext
) -> ShellScriptResult:
    """Bound, decode, and emit script output before reporting its result."""
    output: list[str] = []
    total = 0
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    status: Literal["timeout", "output_limit"] | None = None

    try:
        for chunk in _read_chunks(process, ctx.pipe, ctx.timeout_s):
            available = ctx.max_output_bytes - total
            accepted = chunk[:available]
            total += len(accepted)
            rendered = decoder.decode(accepted)
            if rendered:
                output.append(rendered)
                ctx.pipe.emit_script_output(rendered)
            if len(chunk) > available:
                status = "output_limit"
                break
    except subprocess.TimeoutExpired:
        status = "timeout"

    tail = decoder.decode(b"", final=True)
    if tail:
        output.append(tail)
        ctx.pipe.emit_script_output(tail)
    exit_code = process.returncode if status is None else None
    return ShellScriptResult(
        status=status or ("success" if exit_code == 0 else "failed"),
        exit_code=exit_code,
        output="".join(output),
    )


def _run_script(path: Path, ctx: SafeScriptContext) -> ShellScriptResult:
    """Own the process lifetime, including cleanup on cancellation or failure."""
    environment = {
        ReservedScriptEnv.PATH: os.pathsep.join(
            (str(Path(sys.executable).parent), os.defpath)
        ),
        "LANG": "C.UTF-8",
        **{name: os.environ[name] for name in ctx.env_allowlist if name in os.environ},
    }
    try:
        bash = ctx.bash.require()
        process = subprocess.Popen(
            [str(bash), "--noprofile", "--norc", str(path)],
            cwd=ctx.cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except (OSError, ValueError) as error:
        return ShellScriptResult(status="failed", output=str(error))

    with process:
        try:
            return _monitor_script(process, ctx)
        finally:
            _terminate_process_group(process)


@factory
def run_shell_script(
    input: RunShellScriptInput, messages: list[Message], ctx: SafeScriptContext
) -> ShellScriptResult:
    """List trusted shell scripts when `script` is omitted, or run one by relative path.

    Installed scripts run with this application's privileges. They receive no
    arguments or interactive input; their output is streamed to the user.
    """
    del messages
    root = ctx.scripts_dir.absolute()
    if input.script is None:
        return _list_scripts(root)
    target = _valid_script(root, input.script)
    if target is None:
        return ShellScriptResult(
            status="refused", output="only installed .sh files may run"
        )
    if not ctx.execution_lock.acquire(blocking=False):
        return ShellScriptResult(status="busy", output="another script is running")
    try:
        return _run_script(target, ctx)
    finally:
        ctx.execution_lock.release()
