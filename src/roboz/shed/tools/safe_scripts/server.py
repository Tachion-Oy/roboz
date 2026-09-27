"""Discover and run trusted scripts, directly or through a Unix socket service."""

import codecs
import logging
import os
import select
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import time
from _thread import LockType
from collections.abc import Callable, Generator
from contextlib import ExitStack, closing, suppress
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path, PureWindowsPath
from threading import BoundedSemaphore, Event, Lock, current_thread, main_thread
from typing import TYPE_CHECKING, cast

from roboz.dependencies import ExecutableDependency
from roboz.exceptions import ExternalCallCancelledError

from .protocol import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_S,
    HANDSHAKE_TIMEOUT_S,
    MAX_CONNECTIONS,
    PROTOCOL_VERSION,
    STALLED_DELIVERY_TIMEOUT_S,
    Completed,
    ExecutionPolicy,
    FrameReader,
    Greeting,
    OutputChunk,
    Request,
    Response,
    RunShellScriptInput,
    ScriptEntryMessage,
    encode,
    require_linux_transport,
)

logger = logging.getLogger(__name__)
PROCESS_CLEANUP_TIMEOUT_S = 2.0
POLL_INTERVAL_S = 0.1
OUTPUT_CHUNK_BYTES = 4096
DESCRIPTION_CHARS = 512

# Unix serving is only constructed on Linux; local imports remain portable.
if TYPE_CHECKING or hasattr(socket, "AF_UNIX"):
    from socketserver import ThreadingUnixStreamServer as _StreamServer
else:
    from socketserver import ThreadingTCPServer as _StreamServer


def _valid_script(root: Path, name: str) -> Path | None:
    relative = Path(name)
    if (
        not name
        or relative.is_absolute()
        or PureWindowsPath(name).is_absolute()
        or ".." in relative.parts
        or relative.suffix != ".sh"
        or root.is_symlink()
    ):
        return None
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            return None
    return (
        path
        if path.is_file() and path.resolve().is_relative_to(root.resolve())
        else None
    )


def _lines(path: Path, check: Callable[[], None]) -> Generator[str]:
    """Read logical lines with cancellation checks between bounded reads."""
    with path.open(encoding="utf-8", errors="replace") as stream:
        parts: list[str] = []
        while True:
            check()
            part = stream.readline(OUTPUT_CHUNK_BYTES)
            parts.append(part)
            if not part or part.endswith("\n"):
                yield "".join(parts)
                parts.clear()
            if not part:
                return


def _description(path: Path, check: Callable[[], None]) -> str:
    with closing(_lines(path, check)) as lines:
        for index, line in enumerate(lines):
            if index == 0 and line.startswith("#!"):
                continue
            if line.startswith("#"):
                if description := line[1:].strip():
                    return description[:DESCRIPTION_CHARS]
            elif line.strip():
                break
    return ""


def _discover(
    root: Path, check: Callable[[], None]
) -> Generator[Response, None, Completed]:
    if root.is_symlink():
        return Completed.outcome("refused", diagnostic="script directory is a symlink")
    if not root.is_dir():
        return Completed.outcome(
            "listed", diagnostic="script directory is absent; install trusted .sh files"
        )
    directories = [root]
    while directories:
        check()
        with os.scandir(directories.pop()) as items:
            for item in items:
                check()
                if item.is_dir(follow_symlinks=False):
                    directories.append(Path(item.path))
                elif item.is_file(follow_symlinks=False):
                    name = Path(item.path).relative_to(root).as_posix()
                    if path := _valid_script(root, name):
                        yield ScriptEntryMessage(
                            script=name, description=_description(path, check)
                        )
    check()
    return Completed.outcome("listed")


def _terminate(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + PROCESS_CLEANUP_TIMEOUT_S
        while time.monotonic() < deadline:
            process.poll()  # Reap the leader before checking for remaining children.
            os.killpg(process.pid, 0)
            time.sleep(0.05)
        os.killpg(process.pid, signal.SIGKILL)


class _DeadlineExpired(Exception):
    pass


@dataclass(frozen=True)
class Scripts:
    """Share immutable settings and a gate; keep all operation state in its stream."""

    scripts_dir: Path
    cwd: Path
    policy: ExecutionPolicy
    execution_lock: LockType
    bash: ExecutableDependency

    def events(
        self, request: RunShellScriptInput, check_cancelled: Callable[[], None]
    ) -> Generator[Response]:
        """Yield completion only after process cleanup and gate release."""
        deadline = time.monotonic() + self.policy.timeout_s

        def check() -> None:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise _DeadlineExpired

        try:
            check()
            root = self.scripts_dir.absolute()
            if request.script is None:
                completion = yield from _discover(root, check)
            elif (path := _valid_script(root, request.script)) is None:
                completion = Completed.outcome(
                    "refused", diagnostic="only installed .sh files may run"
                )
            else:
                completion = yield from self._execute(path, check)
        except _DeadlineExpired:
            completion = Completed.outcome("timeout")
        except (OSError, ValueError) as error:
            completion = Completed.outcome("failed", diagnostic=str(error))
        yield completion

    def _execute(
        self, path: Path, check: Callable[[], None]
    ) -> Generator[Response, None, Completed]:
        environment = {
            "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
            "LANG": "C.UTF-8",
            **{
                key: os.environ[key]
                for key in self.policy.env_allowlist
                if key in os.environ
            },
        }
        if not self.execution_lock.acquire(blocking=False):
            return Completed.outcome("busy", diagnostic="another script is running")
        with ExitStack() as cleanup:
            cleanup.callback(self.execution_lock.release)
            check()
            process = cleanup.enter_context(
                subprocess.Popen(
                    [str(self.bash.require()), "--noprofile", "--norc", str(path)],
                    cwd=self.cwd,
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
            streams, remaining = [process.stdout], self.policy.max_output_bytes
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
            except _DeadlineExpired:
                completion = Completed.outcome("timeout")
            except OSError as error:
                completion = Completed.outcome("failed", diagnostic=str(error))
            if tail := decoder.decode(b"", final=True):
                yield OutputChunk(output=tail)
            return completion or Completed.outcome(
                "success" if process.returncode == 0 else "failed", process.returncode
            )


class _Session(socketserver.BaseRequestHandler):
    def setup(self) -> None:
        self.service = cast(_ScriptServer, self.server)

    def _check(self) -> None:
        self.service.check_running()
        if select.select([self.request], [], [], 0)[0]:
            if self.request.recv(1):
                logger.warning("Additional script client input")
            raise ExternalCallCancelledError("script client disconnected or sent input")

    def _handshake(self) -> Request | None:
        self.request.settimeout(HANDSHAKE_TIMEOUT_S)
        policy = self.service.scripts.policy
        self.request.sendall(
            encode(
                Greeting(
                    protocol=PROTOCOL_VERSION,
                    roboz=version("roboz"),
                    timeout_s=policy.timeout_s,
                    max_output_bytes=policy.max_output_bytes,
                )
            )
        )
        reader = FrameReader(self.request)
        try:
            request = reader.read(
                self.service.check_running, time.monotonic() + HANDSHAKE_TIMEOUT_S
            )
        except ConnectionError:
            if reader.pending:
                raise ValueError("incomplete script request")
            return None  # Greeting-only health check.
        if not isinstance(request, Request) or reader.pending:
            raise ValueError("expected one script request")
        return request

    def handle(self) -> None:
        try:
            if (request := self._handshake()) is None:
                return
            self._check()
            self.request.settimeout(STALLED_DELIVERY_TIMEOUT_S)
            with closing(
                self.service.scripts.events(
                    RunShellScriptInput(script=request.script), self._check
                )
            ) as events:
                for event in events:
                    self._check()
                    # A failed send closes the generator and socket; no later frame
                    # can follow a partially delivered one.
                    self.request.sendall(encode(event))
        except ExternalCallCancelledError:
            pass
        except (OSError, ValueError) as error:
            logger.warning("Script protocol or delivery failure: %s", error)
        except Exception:
            logger.exception("Unexpected script session failure")


class _ScriptServer(_StreamServer):
    """Own the listener, execution policy, connection limit, and shutdown."""

    timeout = 0.1
    request_queue_size = MAX_CONNECTIONS
    daemon_threads = False

    def __init__(
        self, socket_path: Path, scripts_dir: Path, cwd: Path, policy: ExecutionPolicy
    ) -> None:
        require_linux_transport()
        if current_thread() is not main_thread():
            raise ValueError("serve_scripts requires a dedicated process's main thread")
        self.socket_path = socket_path.absolute()
        self.stopping = Event()
        self.slots = BoundedSemaphore(MAX_CONNECTIONS)
        self.scripts = Scripts(
            scripts_dir, cwd.absolute(), policy, Lock(), ExecutableDependency("bash")
        )
        self._prepare_socket_directory()
        super().__init__(str(self.socket_path), _Session)
        self.identity = self.socket_path.stat()

    def _prepare_socket_directory(self) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = self.socket_path.parent.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
        ):
            raise ValueError(
                "socket directory must be owned by the serving user and mode 0700"
            )
        if os.path.lexists(self.socket_path):
            raise FileExistsError(
                f"script socket path already exists: {self.socket_path}"
            )

    def verify_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: object,
    ) -> bool:
        return not self.stopping.is_set() and self.slots.acquire(blocking=False)

    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: object,
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def check_running(self) -> None:
        """Interrupt handshakes and operations during shutdown."""
        if self.stopping.is_set():
            raise ExternalCallCancelledError("script service stopping")

    def _stop(self, signum: int, frame: object) -> None:
        self.stopping.set()

    def _remove_socket(self) -> None:
        with suppress(FileNotFoundError):
            current = self.socket_path.lstat()
            if (current.st_dev, current.st_ino) == (
                self.identity.st_dev,
                self.identity.st_ino,
            ):
                self.socket_path.unlink()

    def serve(self) -> None:
        """Serve connections until interrupted, then release owned resources."""
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            self.socket_path.chmod(0o600)
            for signum in previous:
                signal.signal(signum, self._stop)
            while not self.stopping.is_set():
                self.handle_request()
        finally:
            self.stopping.set()
            self.server_close()  # Close the listener and join connection threads.
            for signum, old_handler in previous.items():
                signal.signal(signum, old_handler)
            self._remove_socket()


def serve_scripts(
    *,
    socket_path: Path,
    scripts_dir: Path,
    cwd: Path,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    env_allowlist: tuple[str, ...] = (),
) -> None:
    """Serve trusted scripts in a dedicated Linux process until SIGINT or SIGTERM.

    The socket directory must be owned by the serving user with mode 0700; an
    absent directory is created. The socket is 0600 and existing paths are never
    replaced. Scripts retain the serving user's privileges. Shutdown cancels
    execution, joins connection threads, and removes only this service's socket.
    """
    policy = ExecutionPolicy(timeout_s, max_output_bytes, env_allowlist)
    _ScriptServer(socket_path, scripts_dir, cwd, policy).serve()
