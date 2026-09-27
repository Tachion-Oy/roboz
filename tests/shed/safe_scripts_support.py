"""The trusted-script capability's real subprocess and agent contracts."""

# Socket tests use real dedicated helper processes, with disposable private sockets.
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from roboz.deployment import Capability, DeployableAgent
from roboz.llm import MockLLMEndpoint
from roboz.shed.capabilities import SafeScripts
from roboz.shed.sandbox import Sandbox
from roboz.shed.tools.safe_scripts import (
    ScriptSocketDependency,
)
from roboz.tools import stop

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="trusted Bash scripts require POSIX and Bash",
)


def _script(root: Path, name: str, body: str) -> Path:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    return target


def _agent(workspace: Path, capability: SafeScripts, *, sink=None):
    definition = DeployableAgent(
        name="script_test",
        system_prompt="Use installed scripts.",
        default_capabilities=(Capability(tools=(stop,)), capability),
    )
    definition.set_attributes(sandbox=Sandbox(workspace))
    definition.set_agent_endpoint(MockLLMEndpoint([]))
    agent, _ = definition.build(event_sinks=(sink,) if sink else ())
    tool = next(tool for tool in agent.tools if tool.name == "run_shell_script")
    return definition, agent, tool


def _wait_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.fixture
def host(tmp_path):
    if sys.platform != "linux":
        pytest.skip("host transport requires Linux")
    scripts, workspace = tmp_path / "scripts", tmp_path / "host-workspace"
    scripts.mkdir()
    workspace.mkdir()

    @contextmanager
    def start(**settings):
        with TemporaryDirectory(prefix="roboz-socket-") as directory:
            path = Path(directory) / "private" / "scripts.sock"
            code = (
                "from pathlib import Path; "
                "from roboz.shed.tools.safe_scripts import serve_scripts; "
                f"serve_scripts(socket_path=Path({str(path)!r}), "
                f"scripts_dir=Path({str(scripts)!r}), cwd=Path({str(workspace)!r}), "
                + ", ".join(f"{key}={value!r}" for key, value in settings.items())
                + ")"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                _wait_until(lambda: path.exists() or process.poll() is not None)
                assert process.poll() is None, process.communicate()[1].decode()
                assert ScriptSocketDependency(path).check()
                yield path, process
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    _, errors = process.communicate(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    raise
                assert process.returncode == 0, errors.decode()
                assert not path.exists()

    return scripts, workspace, start


def _connect(path):
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(3)
    connection.connect(str(path))
    # Consume exactly the greeting, leaving subsequent bytes for each test.
    greeting = bytearray()
    while not greeting.endswith(b"\n"):
        greeting.extend(connection.recv(1))
    return connection, json.loads(greeting)


def _remote_tool(path, sink=None):
    # Remote execution has no dependency on the caller's filesystem policy.
    definition = DeployableAgent(
        name="remote_scripts",
        system_prompt="Use scripts.",
        default_capabilities=(SafeScripts(socket_path=path),),
    )
    definition.set_agent_endpoint(MockLLMEndpoint([]))
    agent, _ = definition.build(event_sinks=(sink,) if sink else ())
    return (
        definition,
        agent,
        next(t for t in agent.tools if t.name == "run_shell_script"),
    )


@contextmanager
def fake_helper(messages, *, package_version="different-release", fragmented=False):
    """Expose a real socket peer that sends controlled protocol responses."""
    from threading import Thread

    from roboz.shed.tools.safe_scripts.protocol import Greeting, encode

    with TemporaryDirectory(prefix="roboz-wire-") as directory:
        path = Path(directory) / "service.sock"
        received = []
        errors = []
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            listener.listen()
            listener.settimeout(3)

            def serve():
                try:
                    with listener.accept()[0] as connection:
                        connection.settimeout(3)
                        hello = encode(
                            Greeting(
                                protocol=2,
                                roboz=package_version,
                                timeout_s=1,
                                max_output_bytes=65536,
                            )
                        )
                        connection.sendall(hello)
                        request = bytearray()
                        while not request.endswith(b"\n"):
                            chunk = connection.recv(4096)
                            if not chunk:
                                break
                            request.extend(chunk)
                        received.append(bytes(request))
                        frames = b"".join(
                            message if isinstance(message, bytes) else encode(message)
                            for message in messages
                        )
                        if fragmented:
                            for byte in frames:
                                connection.sendall(bytes([byte]))
                        elif frames:
                            connection.sendall(frames)
                except Exception as error:
                    errors.append(error)

            helper = Thread(target=serve)
            helper.start()
            try:
                yield path, received
            finally:
                helper.join(timeout=5)
                assert not helper.is_alive()
                assert not errors, errors
