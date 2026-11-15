"""Run Bash under an execution policy and stream its output and outcome.

Trusted scripts and user-approved shell commands both run through this module.
"""

import codecs
import os
import select
import signal
import subprocess
import time
from collections.abc import Callable, Generator
from contextlib import ExitStack, closing, suppress
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.dataclasses import dataclass as validated_dataclass

from roboz.dependencies import ExecutableDependency

DEFAULT_TIMEOUT_S = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 65_536
MAX_DIAGNOSTIC_CHARS = 4096  # Bounds terminal diagnostics.
PROCESS_CLEANUP_TIMEOUT_S = 2.0
POLL_INTERVAL_S = 0.1
OUTPUT_CHUNK_BYTES = 4096


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


@validated_dataclass(frozen=True, config=ConfigDict(strict=True))
class ExecutionPolicy:
    """Validate immutable execution settings using the same rules at both entrypoints."""

    timeout_s: Annotated[float, Field(gt=0, allow_inf_nan=False)] = DEFAULT_TIMEOUT_S
    max_output_bytes: Annotated[int, Field(gt=0)] = DEFAULT_MAX_OUTPUT_BYTES
    env_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Exclude invalid names and variables that control shell startup."""
        if any(
            not name or "=" in name or "\x00" in name or name in ReservedScriptEnv
            for name in self.env_allowlist
        ):
            raise ValueError("env_allowlist contains an invalid or reserved name")


ScriptStatus = Literal[
    "listed", "success", "refused", "busy", "failed", "timeout", "output_limit"
]


class OutputChunk(BaseModel):
    """Carry only decoded script transcript."""

    kind: Literal["output"] = "output"
    output: str
    model_config = ConfigDict(extra="forbid", strict=True)


class Completed(BaseModel):
    """Carry terminal metadata separately from streamed data."""

    kind: Literal["completion"] = "completion"
    status: ScriptStatus
    exit_code: int | None
    diagnostic: str = Field(max_length=MAX_DIAGNOSTIC_CHARS)
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_outcome(self) -> "Completed":
        """Reject exit codes inconsistent with the terminal status."""
        match self.status:
            case "success" if self.exit_code != 0:
                raise ValueError("successful completion requires exit code zero")
            case "failed" if self.exit_code == 0:
                raise ValueError("failed completion cannot have exit code zero")
            case "success" | "failed":
                pass
            case _ if self.exit_code is not None:
                raise ValueError("non-execution completion cannot have an exit code")
        return self

    @classmethod
    def outcome(
        cls, status: ScriptStatus, exit_code: int | None = None, diagnostic: str = ""
    ) -> "Completed":
        """Bound locally produced diagnostics; wire decoding remains strict."""
        return cls(
            status=status,
            exit_code=exit_code,
            diagnostic=diagnostic[:MAX_DIAGNOSTIC_CHARS],
        )


def _terminate(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + PROCESS_CLEANUP_TIMEOUT_S
        while time.monotonic() < deadline:
            process.poll()  # Reap the leader before checking for remaining children.
            os.killpg(process.pid, 0)
            time.sleep(0.05)
        os.killpg(process.pid, signal.SIGKILL)


class DeadlineExpired(Exception):
    """Raised by a deadline check once the time limit has passed."""


def deadline_check(
    timeout_s: float, check_cancelled: Callable[[], None]
) -> Callable[[], None]:
    """Return a check that raises on cancellation or once `timeout_s` has passed."""
    deadline = time.monotonic() + timeout_s

    def check() -> None:
        check_cancelled()
        if time.monotonic() >= deadline:
            raise DeadlineExpired

    return check


def run_bash(
    bash: ExecutableDependency,
    arguments: list[str],
    cwd: Path,
    search_path: str,
    policy: ExecutionPolicy,
    check: Callable[[], None],
) -> Generator[OutputChunk, None, Completed]:
    """Yield Bash's output and return its completion after ending its session.

    `check` is polled while the process runs and may raise to stop it.
    """
    environment = {
        "PATH": search_path,
        "LANG": "C.UTF-8",
        **{
            key: os.environ[key]
            for key in policy.env_allowlist
            if key in os.environ
        },
    }
    with ExitStack() as cleanup:
        check()
        process = cleanup.enter_context(
            subprocess.Popen(
                [str(bash.require()), *arguments],
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        )
        cleanup.callback(_terminate, process)
        assert process.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        streams, remaining = [process.stdout], policy.max_output_bytes
        completion = None
        try:
            while streams or process.poll() is None:
                check()
                if not select.select(streams, [], [], POLL_INTERVAL_S)[0]:
                    continue
                chunk = os.read(process.stdout.fileno(), OUTPUT_CHUNK_BYTES)
                if not chunk:
                    streams.clear()
                    continue
                if text := decoder.decode(chunk[:remaining]):
                    yield OutputChunk(output=text)
                remaining -= len(chunk)
                if remaining < 0:
                    completion = Completed.outcome("output_limit")
                    break
        except DeadlineExpired:
            completion = Completed.outcome("timeout")
        except OSError as error:
            completion = Completed.outcome("failed", diagnostic=str(error))
        if tail := decoder.decode(b"", final=True):
            yield OutputChunk(output=tail)
        return completion or Completed.outcome(
            "success" if process.returncode == 0 else "failed", process.returncode
        )


def bash_events(
    bash: ExecutableDependency,
    arguments: list[str],
    cwd: Path,
    search_path: str,
    policy: ExecutionPolicy,
    check_cancelled: Callable[[], None],
) -> Generator[OutputChunk | Completed]:
    """Run Bash once under the policy; yield completion only after process cleanup.

    The process gets `search_path` as its `PATH` and, besides that, only the
    policy's allowed variables. It runs in its own session, and the whole
    session ends with it, at the time limit, or on cancellation.
    """
    check = deadline_check(policy.timeout_s, check_cancelled)
    try:
        completion = yield from run_bash(
            bash, arguments, cwd, search_path, policy, check
        )
    except DeadlineExpired:
        completion = Completed.outcome("timeout")
    except (OSError, ValueError) as error:
        completion = Completed.outcome("failed", diagnostic=str(error))
    yield completion


def collect_output[Other](
    events: Generator[OutputChunk | Completed | Other],
    output: Callable[[str], None],
    other: Callable[[Other], None] | None = None,
) -> tuple[Completed, str]:
    """Consume a stream, closing the source if a sink raises.

    Output goes to `output` as it arrives and any other event to `other`.
    Returns the completion and the transcript with its diagnostic appended.
    """
    chunks: list[str] = []
    with closing(events):
        for event in events:
            match event:
                case Completed():
                    break
                case OutputChunk():
                    chunks.append(event.output)
                    output(event.output)
                case _:
                    if other is None:
                        raise RuntimeError("unexpected event in an output stream")
                    other(event)
        else:
            raise RuntimeError("script stream ended without completion")
    transcript = "".join(chunks)
    if event.diagnostic:
        transcript += "\n" if transcript and not transcript.endswith("\n") else ""
        transcript += event.diagnostic
    return event, transcript
