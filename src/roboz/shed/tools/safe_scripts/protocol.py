"""Script models, execution policy, and the strict version-2 socket protocol."""

import select
import socket
import sys
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic.dataclasses import dataclass as validated_dataclass

from roboz.models import Empty

DEFAULT_TIMEOUT_S = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 65_536
MAX_DIAGNOSTIC_CHARS = 4096  # Protocol bounds terminal diagnostics.


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


ScriptStatus = Literal[
    "listed", "success", "refused", "busy", "failed", "timeout", "output_limit"
]


class ShellScriptResult(Empty):
    """Bounded outcome of discovery or a trusted script invocation."""

    status: ScriptStatus
    scripts: list[ScriptEntry] = Field(default_factory=list)
    exit_code: int | None = None
    output: str = ""
    model_config = ConfigDict(extra="forbid")


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


PROTOCOL_VERSION = 2
MAX_FRAME_BYTES = 524_288  # Encoder/decoder include the newline in this limit.
HANDSHAKE_TIMEOUT_S = 3.0  # Client/session bound greeting and request exchange.
CLEANUP_DELIVERY_ALLOWANCE_S = 10.0  # Transport allows cleanup and delivery.
STALLED_DELIVERY_TIMEOUT_S = 5.0  # Session bounds each socket send.
MAX_CONNECTIONS = 16  # Service limits simultaneous sessions.


def require_linux_transport() -> None:
    """Keep portable imports while restricting Unix transport use to Linux."""
    if sys.platform != "linux":
        raise ValueError("SafeScripts socket transport requires Linux")


class WireMessage(BaseModel):
    """Reject extra fields and coercion at the transport boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class Greeting(WireMessage):
    """Advertise compatibility and host-owned limits."""

    kind: Literal["greeting"] = "greeting"
    protocol: int
    roboz: str
    timeout_s: float
    max_output_bytes: int

    def policy(self) -> ExecutionPolicy:
        """Validate protocol independently of informational package version."""
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("script helper protocol mismatch")
        return ExecutionPolicy(self.timeout_s, self.max_output_bytes)


class Request(WireMessage):
    """Select discovery or a single relative entrypoint."""

    kind: Literal["request"] = "request"
    script: str | None = None


class OutputChunk(WireMessage):
    """Carry only decoded script transcript."""

    kind: Literal["output"] = "output"
    output: str


class ScriptEntryMessage(WireMessage):
    """Deliver one entry without a whole-catalogue frame ceiling."""

    kind: Literal["entry"] = "entry"
    script: str
    description: str = Field(max_length=512)


class Completed(WireMessage):
    """Carry terminal metadata separately from streamed data."""

    kind: Literal["completion"] = "completion"
    status: ScriptStatus
    exit_code: int | None
    diagnostic: str = Field(max_length=MAX_DIAGNOSTIC_CHARS)

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


Response = OutputChunk | ScriptEntryMessage | Completed
Message = Greeting | Request | Response
_MESSAGE = TypeAdapter(Annotated[Message, Field(discriminator="kind")])


def encode(message: Message) -> bytes:
    """Bound every encoded frame, including its newline delimiter."""
    frame = message.model_dump_json().encode() + b"\n"
    if len(frame) > MAX_FRAME_BYTES:
        raise ValueError("script socket frame exceeds the transport limit")
    return frame


class FrameReader:
    """Retain coalesced bytes and reject oversized partial frames."""

    def __init__(self, connection: socket.socket) -> None:
        """Bind a socket whose lifetime remains owned by the caller."""
        self.connection = connection
        self.pending = bytearray()

    def read(self, check: Callable[[], None], deadline: float) -> Message:
        """Decode one strict message while polling cancellation and deadline."""
        while True:
            check()
            if time.monotonic() >= deadline:
                raise TimeoutError("script socket deadline expired")
            newline = self.pending.find(b"\n")
            if newline >= 0:
                if newline + 1 > MAX_FRAME_BYTES:
                    raise ValueError("script socket frame exceeds the transport limit")
                line = bytes(self.pending[:newline])
                del self.pending[: newline + 1]
                return _MESSAGE.validate_json(line)
            if len(self.pending) >= MAX_FRAME_BYTES:
                raise ValueError("script socket frame exceeds the transport limit")
            if not select.select([self.connection], [], [], 0.1)[0]:
                continue
            chunk = self.connection.recv(65536)
            if not chunk:
                raise ConnectionError("script helper disconnected")
            self.pending.extend(chunk)
