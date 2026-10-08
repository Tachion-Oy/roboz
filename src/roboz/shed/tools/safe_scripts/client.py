"""Connect to a helper and collect its event stream without catching sink errors."""

import socket
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from roboz.dependencies import DependencyFailure, ExternalDependency, ExternalDependencyKind

from .protocol import (
    CLEANUP_DELIVERY_ALLOWANCE_S,
    HANDSHAKE_TIMEOUT_S,
    Completed,
    FrameReader,
    Greeting,
    OutputChunk,
    Request,
    Response,
    RunShellScriptInput,
    ScriptEntry,
    ScriptEntryMessage,
    ShellScriptResult,
    encode,
    require_linux_transport,
)


def _greeting(reader: FrameReader, check: Callable[[], None]) -> Greeting:
    hello = reader.read(check, time.monotonic() + HANDSHAKE_TIMEOUT_S)
    if not isinstance(hello, Greeting):
        raise ValueError("expected script helper greeting")
    hello.policy()
    return hello


@dataclass(frozen=True)
class ScriptSocketDependency(ExternalDependency):
    """A Linux helper whose health check only exchanges a greeting."""

    socket_path: Path

    @property
    def dependency_id(self) -> str:
        """Identify the helper by its absolute socket path."""
        return f"script_socket:{self.socket_path.absolute()}"

    @property
    def kind(self) -> ExternalDependencyKind:
        """Report an external service."""
        return ExternalDependencyKind.NETWORK_SERVICE

    def redacted_metadata(self) -> Mapping[str, str]:
        """Report the configured socket location."""
        return {"socket_path": str(self.socket_path)}

    def check(self) -> DependencyFailure | None:
        """Return connection or handshake diagnostics without executing scripts."""
        try:
            require_linux_transport()
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(HANDSHAKE_TIMEOUT_S)
                connection.connect(str(self.socket_path))
                _greeting(FrameReader(connection), lambda: None)
            return None
        except Exception as error:
            return DependencyFailure.from_exception(error)


def _events(
    request: RunShellScriptInput,
    helper: ScriptSocketDependency,
    check: Callable[[], None],
) -> Generator[Response]:
    attempted = False
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            check()
            connection.settimeout(HANDSHAKE_TIMEOUT_S)
            connection.connect(str(helper.socket_path))
            reader = FrameReader(connection)
            hello = _greeting(reader, check)
            frame = encode(Request(script=request.script))
            attempted = True  # A failed send may still deliver the whole request.
            connection.sendall(frame)
            deadline = time.monotonic() + hello.timeout_s + CLEANUP_DELIVERY_ALLOWANCE_S
            yield from _responses(
                reader, request.script is None, hello.max_output_bytes, check, deadline
            )
    except (OSError, ValueError) as error:
        prefix = "Script helper transport failure"
        if attempted:
            prefix += "; execution outcome is unknown"
        yield Completed.outcome("failed", diagnostic=f"{prefix}: {error}")


def request_script(
    request: RunShellScriptInput,
    helper: ScriptSocketDependency,
    output: Callable[[str], None],
    check: Callable[[], None],
) -> ShellScriptResult:
    """Consume outside transport error handling; close the socket if a sink raises."""
    require_linux_transport()
    return collect(_events(request, helper, check), output)


def _responses(
    reader: FrameReader,
    discovery: bool,
    max_output_bytes: int,
    check: Callable[[], None],
    deadline: float,
) -> Generator[Response]:
    total = 0
    while True:
        message = reader.read(check, deadline)
        match message:
            case OutputChunk() if not discovery:
                total += len(message.output.encode())
                if total > max_output_bytes * 3:  # UTF-8 replacement expansion.
                    raise ValueError("script helper output exceeds advertised limit")
            case ScriptEntryMessage() if discovery:
                pass
            case Completed():
                if discovery and (
                    message.status not in {"listed", "refused", "failed", "timeout"}
                    or message.exit_code is not None
                ):
                    raise ValueError("invalid discovery completion")
                if not discovery and message.status == "listed":
                    raise ValueError("invalid execution completion")
                if reader.pending:
                    raise ValueError("data after script helper completion")
                yield message
                return
            case _:
                raise ValueError("invalid script helper response")
        yield message


def collect(
    events: Generator[Response], output: Callable[[str], None]
) -> ShellScriptResult:
    """Consume local or socket responses, closing the source if a sink raises."""
    chunks: list[str] = []
    entries: list[ScriptEntry] = []
    with closing(events):
        for event in events:
            match event:
                case Completed():
                    break
                case ScriptEntryMessage():
                    entries.append(
                        ScriptEntry(script=event.script, description=event.description)
                    )
                case OutputChunk():
                    chunks.append(event.output)
                    output(event.output)
        else:
            raise RuntimeError("script stream ended without completion")
    transcript = "".join(chunks)
    if event.diagnostic:
        transcript += "\n" if transcript and not transcript.endswith("\n") else ""
        transcript += event.diagnostic
    return ShellScriptResult(
        status=event.status,
        exit_code=event.exit_code,
        scripts=sorted(entries, key=lambda entry: entry.script),
        output=transcript,
    )
