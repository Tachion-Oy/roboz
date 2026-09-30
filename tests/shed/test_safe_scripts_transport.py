"""Real host sessions, remote results, cancellation, and portable imports."""

import asyncio
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

import pytest
from safe_scripts_support import _agent, _connect, _remote_tool, _script, _wait_until
from safe_scripts_support import host as host

from roboz.exceptions import ExternalCallCancelledError
from roboz.runtime.events import ScriptOutputEvent
from roboz.shed.capabilities import SafeScripts
from roboz.shed.dependency_health import (
    DependencyHealthMonitor,
    DependencyReasonCode,
    DependencyStatus,
)
from roboz.shed.tools.safe_scripts import (
    RunShellScriptInput,
    ScriptSocketDependency,
    ShellScriptResult,
    serve_scripts,
)


def test_remote_discovery_execution_streaming_environment_and_schema(host, monkeypatch):
    scripts, workspace, start = host
    _script(
        scripts,
        "report.sh",
        "# Host report.\nprintf '%s|%s|%s' \"$PWD\" "
        '"${SCRIPT_ALLOWED:-missing}" "${SCRIPT_HIDDEN:-missing}"\nexit 7\n',
    )
    monkeypatch.setenv("SCRIPT_ALLOWED", "allowed")
    monkeypatch.setenv("SCRIPT_HIDDEN", "hidden")
    with start(env_allowlist=("SCRIPT_ALLOWED",)) as (path, _):
        events = []
        definition, agent, tool = _remote_tool(path, events.append)
        _, _, second = _remote_tool(path)
        assert tool.id != second.id
        local = _agent(workspace, SafeScripts(scripts))[2]
        assert tool.InputModel == local.InputModel == RunShellScriptInput
        assert tool.OutputModel == local.OutputModel == ShellScriptResult
        dependencies = definition.external_dependencies()
        assert [dep.dependency_id for dep in dependencies] == [f"script_socket:{path}"]
        assert dependencies[0].check()
        listed = tool(RunShellScriptInput(), [])
        assert [(entry.script, entry.description) for entry in listed.scripts] == [
            ("report.sh", "Host report.")
        ]
        result = tool(RunShellScriptInput(script="report.sh"), [])
        assert (result.status, result.exit_code, result.output) == (
            "failed",
            7,
            f"{workspace}|allowed|missing",
        )
        assert (
            "".join(
                event.content
                for event in events
                if isinstance(event, ScriptOutputEvent)
            )
            == result.output
        )
        assert agent.pipe is not None
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700


def test_remote_refuses_traversal_symlinks_and_extra_fields(host):
    scripts, workspace, start = host
    outside = _script(workspace, "outside.sh", "touch escaped\n")
    (scripts / "alias.sh").symlink_to(outside)
    _script(scripts, "safe.sh", "touch invoked\n")
    with start() as (path, _):
        _, _, tool = _remote_tool(path)
        for name in ("../host-workspace/outside.sh", "alias.sh"):
            assert tool(RunShellScriptInput(script=name), []).status == "refused"
        assert [e.script for e in tool(RunShellScriptInput(), []).scripts] == [
            "safe.sh"
        ]
        with _connect(path)[0] as connection:
            connection.sendall(
                b'{"kind":"request","script":"safe.sh","args":["unsafe"]}\n'
            )
            assert connection.recv(1) == b""
        assert not (workspace / "escaped").exists()
        assert not (workspace / "invoked").exists()


@pytest.mark.parametrize(
    "settings,script,status,output",
    [
        ({"timeout_s": 0.1}, "sleep 10\n", "timeout", ""),
        ({"max_output_bytes": 8}, "printf abcdefghijkl\n", "output_limit", "abcdefgh"),
    ],
)
def test_remote_uses_host_timeout_and_output_limit(
    host, settings, script, status, output
):
    scripts, _, start = host
    _script(scripts, "bounded.sh", script)
    with start(**settings) as (path, _):
        _, _, tool = _remote_tool(path)
        result = tool(RunShellScriptInput(script="bounded.sh"), [])
        assert (result.status, result.output) == (status, output)


def test_remote_large_output_is_streamed_with_bounded_frames(host):
    scripts, _, start = host
    _script(scripts, "large.sh", "head -c 1048576 /dev/zero | tr '\\0' x\n")
    with start(max_output_bytes=1048576) as (path, _):
        _, _, tool = _remote_tool(path)
        result = tool(RunShellScriptInput(script="large.sh"), [])
        assert result.status == "success"
        assert result.output == "x" * 1048576


def test_remote_cancellation_releases_host_gate_and_stops_children(host):
    scripts, workspace, start = host
    _script(scripts, "wait.sh", "echo ready\n(sleep 1; touch leaked) &\nwait\n")
    _script(scripts, "done.sh", "echo done\n")
    ready = Event()
    with start() as (path, _):
        _, agent, first = _remote_tool(
            path,
            lambda event: ready.set() if isinstance(event, ScriptOutputEvent) else None,
        )
        _, _, second = _remote_tool(path)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(first, RunShellScriptInput(script="wait.sh"), [])
            assert ready.wait(3)
            assert second(RunShellScriptInput(script="done.sh"), []).status == "busy"
            agent.pipe.cancel()
            with pytest.raises(ExternalCallCancelledError):
                future.result(timeout=4)
        _wait_until(
            lambda: (
                second(RunShellScriptInput(script="done.sh"), []).status == "success"
            )
        )
        time.sleep(1.1)
        assert not (workspace / "leaked").exists()


@pytest.mark.parametrize("action", ["disconnect", "sigterm"])
def test_host_disconnect_and_shutdown_stop_process_group(host, action):
    scripts, workspace, start = host
    _script(scripts, "wait.sh", "echo ready\n(sleep 1; touch leaked) &\nwait\n")
    _script(scripts, "done.sh", "echo done\n")
    with start() as (path, process):
        connection, _ = _connect(path)
        try:
            connection.sendall(b'{"kind":"request","script":"wait.sh"}\n')
            assert b"ready" in connection.recv(4096)
            if action == "disconnect":
                connection.close()
                _, _, tool = _remote_tool(path)
                _wait_until(
                    lambda: (
                        tool(RunShellScriptInput(script="done.sh"), []).status
                        == "success"
                    ),
                    timeout=4,
                )
            else:
                process.send_signal(signal.SIGTERM)
                assert process.wait(timeout=4) == 0
                assert not path.exists()
        finally:
            connection.close()
        time.sleep(1.1)
        assert not (workspace / "leaked").exists()


def test_helper_disappearance_returns_transport_failure(host):
    _, _, start = host
    with start() as (path, process):
        definition, _, tool = _remote_tool(path)
        process.terminate()
        process.wait(timeout=4)
        assert not ScriptSocketDependency(path).check()
        monitor = DependencyHealthMonitor(definition.external_dependencies())
        asyncio.run(monitor.run_once())
        (record,) = monitor.records()
        assert record.status is DependencyStatus.UNAVAILABLE
        assert record.reason_code is DependencyReasonCode.NOT_FOUND
        result = tool(RunShellScriptInput(), [])
        assert result.status == "failed" and "transport failure" in result.output


def test_health_handshake_runs_no_scripts_or_discovery(host):
    scripts, workspace, start = host
    _script(scripts, "not-readable.sh", "touch invoked\n")
    with start() as (path, _):
        # Even an unreadable entrypoint cannot affect handshake health.
        (scripts / "not-readable.sh").chmod(0)
        assert ScriptSocketDependency(path).check()
        assert not (workspace / "invoked").exists()


def test_host_binding_copying_and_inspection_perform_no_external_work(
    tmp_path, monkeypatch
):
    if sys.platform != "linux":
        pytest.skip("host transport requires Linux")

    def unexpected(*args, **kwargs):
        raise AssertionError("unexpected external work")

    monkeypatch.setattr(socket, "socket", unexpected)
    monkeypatch.setattr(shutil, "which", unexpected)
    path = tmp_path / "absent.sock"
    definition, _, tool = _remote_tool(path)
    dependencies = definition.external_dependencies()
    assert dependencies == (ScriptSocketDependency(path),)
    assert tool.copy().external_dependencies() == dependencies
    assert not path.exists()


def test_manually_started_host_helper_works_without_process_compose(
    host, tmp_path, monkeypatch
):
    scripts, _, start = host
    _script(scripts, "report.sh", "# Host report.\nprintf 'host report\\n'\n")
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    (host_bin / "bash").symlink_to(shutil.which("bash"))
    host_env = {**os.environ, "PATH": str(host_bin)}
    assert shutil.which("process-compose", path=host_env["PATH"]) is None
    monkeypatch.setenv("PATH", "")
    assert shutil.which("process-compose") is None

    with start(env=host_env) as (path, _):
        events = []
        definition, _, tool = _remote_tool(path, events.append)
        dependencies = definition.external_dependencies()
        assert dependencies == (ScriptSocketDependency(path),)
        monitor = DependencyHealthMonitor(dependencies)
        (record,) = monitor.records()
        assert record.status is DependencyStatus.PENDING
        asyncio.run(monitor.run_once())
        (record,) = monitor.records()
        assert record.status is DependencyStatus.AVAILABLE
        assert record.reason_code is None
        assert not events
        listed = tool(RunShellScriptInput(), [])
        assert [(entry.script, entry.description) for entry in listed.scripts] == [
            ("report.sh", "Host report.")
        ]
        result = tool(RunShellScriptInput(script="report.sh"), [])
        assert (result.status, result.exit_code, result.output) == (
            "success",
            0,
            "host report\n",
        )
        assert [
            event.content for event in events if isinstance(event, ScriptOutputEvent)
        ] == ["host report\n"]


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"scripts_dir": Path("scripts"), "socket_path": Path("socket")},
        {"socket_path": Path("socket"), "timeout_s": 2},
        {"socket_path": Path("socket"), "max_output_bytes": 8},
        {"socket_path": Path("socket"), "env_allowlist": ("HOME",)},
    ],
)
def test_constructor_requires_one_target_and_host_owned_policy(settings):
    with pytest.raises(ValueError):
        SafeScripts(**settings)


def test_imports_and_local_construction_remain_portable(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    SafeScripts(tmp_path)
    with pytest.raises(ValueError, match="Linux"):
        SafeScripts(socket_path=tmp_path / "socket")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket, os; del socket.AF_UNIX; del os.killpg; "
            "import roboz.shed.capabilities; import roboz.shed.tools.safe_scripts",
        ],
        check=True,
    )


@pytest.mark.skipif(sys.platform != "linux", reason="host transport requires Linux")
def test_host_rejects_public_directory_and_existing_socket_path(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "scripts.sock"
    path.write_text("existing service")
    with pytest.raises(FileExistsError):
        serve_scripts(socket_path=path, scripts_dir=tmp_path, cwd=tmp_path)
    assert path.read_text() == "existing service"
    path.unlink()
    private.chmod(0o755)
    with pytest.raises(ValueError, match="0700"):
        serve_scripts(socket_path=path, scripts_dir=tmp_path, cwd=tmp_path)


def _process_stopped(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
    except FileNotFoundError:
        return True


def test_stalled_reader_cancels_native_execution_and_releases_gate(host):
    scripts, workspace, start = host
    _script(
        scripts, "flood.sh", "echo $$ > pid\nhead -c 67108864 /dev/zero\nsleep 30\n"
    )
    _script(scripts, "done.sh", "echo done\n")
    with start(max_output_bytes=67108864) as (path, _):
        connection, _ = _connect(path)
        try:
            connection.sendall(b'{"kind":"request","script":"flood.sh"}\n')
            _wait_until(lambda: (workspace / "pid").exists())
            pid = int((workspace / "pid").read_text())
            # Leave the socket open without consuming script output.
            _wait_until(lambda: _process_stopped(pid), timeout=8)
            _, _, tool = _remote_tool(path)
            _wait_until(
                lambda: (
                    tool(RunShellScriptInput(script="done.sh"), []).status == "success"
                )
            )
        finally:
            connection.close()


@pytest.mark.parametrize(
    "override",
    [
        {"protocol": 1},
        {"protocol": 3},
        {"protocol": 999},
        {"timeout_s": float("nan")},
    ],
)
def test_protocol_mismatch_fails_without_sending_execution(tmp_path, override):
    if sys.platform != "linux":
        pytest.skip("host transport requires Linux")
    from importlib.metadata import version
    from threading import Thread

    with TemporaryDirectory(prefix="roboz-protocol-") as directory:
        path = Path(directory) / "service.sock"
        received = []
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            listener.listen()

            def fake_helper():
                for _ in range(2):
                    with listener.accept()[0] as connection:
                        connection.settimeout(3)
                        hello = {
                            "kind": "greeting",
                            "protocol": 2,
                            "roboz": version("roboz"),
                            "timeout_s": 1,
                            "max_output_bytes": 8,
                            **override,
                        }
                        connection.sendall((json.dumps(hello) + "\n").encode())
                        received.append(connection.recv(4096))

            helper = Thread(target=fake_helper)
            helper.start()
            try:
                assert not ScriptSocketDependency(path).check()
                _, _, tool = _remote_tool(path)
                result = tool(RunShellScriptInput(script="effect.sh"), [])
                assert (
                    result.status == "failed" and "transport failure" in result.output
                )
            finally:
                helper.join(timeout=5)
            assert not helper.is_alive()
            assert received == [b"", b""]


@pytest.mark.skipif(sys.platform != "linux", reason="host transport requires Linux")
def test_host_rejects_socket_directory_symlink_and_wrong_owner(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError, match="owned"):
        serve_scripts(
            socket_path=alias / "service.sock", scripts_dir=tmp_path, cwd=tmp_path
        )
    monkeypatch.setattr(os, "getuid", lambda: private.stat().st_uid + 1)
    with pytest.raises(ValueError, match="owned"):
        serve_scripts(
            socket_path=private / "service.sock", scripts_dir=tmp_path, cwd=tmp_path
        )


@pytest.mark.skipif(sys.platform != "linux", reason="host transport requires Linux")
def test_health_check_times_out_when_helper_never_sends_handshake():
    with TemporaryDirectory(prefix="roboz-health-") as directory:
        path = Path(directory) / "service.sock"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            listener.listen()
            started = time.monotonic()
            assert not ScriptSocketDependency(path).check()
            assert time.monotonic() - started < 4
            # The peer sees no script request, even after a failed handshake.
            with listener.accept()[0] as connection:
                assert connection.recv(1) == b""


def test_oversized_remote_request_fails_before_execution(host):
    _, _, start = host
    with start() as (path, _):
        _, _, tool = _remote_tool(path)
        result = tool(RunShellScriptInput(script="x" * 524_288 + ".sh"), [])
        assert result.status == "failed"
        assert "transport limit" in result.output
        assert ScriptSocketDependency(path).check()


def test_thousand_script_catalogue_matches_local_and_remote_results(host):
    scripts, workspace, start = host
    for index in range(1000):
        _script(
            scripts, f"nested/script-{index:04}.sh", "# " + "x" * 512 + "\necho okay\n"
        )
    local = _agent(workspace, SafeScripts(scripts))[2](RunShellScriptInput(), [])
    assert len(local.scripts) == 1000
    with start() as (path, _):
        remote = _remote_tool(path)[2](RunShellScriptInput(), [])
    assert remote == local


def test_additional_client_input_cancels_execution_and_releases_gate(host):
    scripts, workspace, start = host
    _script(scripts, "wait.sh", "echo ready\n(sleep 1; touch leaked) &\nwait\n")
    _script(scripts, "done.sh", "echo done\n")
    with start() as (path, _):
        with _connect(path)[0] as connection:
            connection.sendall(b'{"kind":"request","script":"wait.sh"}\n')
            assert b"ready" in connection.recv(4096)
            connection.sendall(b"additional input")
            try:
                assert connection.recv(1) == b""
            except ConnectionResetError:
                pass  # Closing a socket with unread input may reset the peer.
        tool = _remote_tool(path)[2]
        _wait_until(
            lambda: tool(RunShellScriptInput(script="done.sh"), []).status == "success"
        )
        time.sleep(1.1)
        assert not (workspace / "leaked").exists()


@pytest.mark.parametrize("action", ["disconnect", "sigterm"])
def test_discovery_delivery_cancellation_joins_connection_handlers(host, action):
    scripts, _, start = host
    for index in range(1000):
        _script(scripts, f"{index:04}.sh", "# " + "x" * 512 + "\necho okay\n")
    with start() as (path, process):
        with _connect(path)[0] as connection:
            connection.sendall(b'{"kind":"request","script":null}\n')
            assert b'"kind":"entry"' in connection.recv(4096)
            if action == "disconnect":
                connection.close()
                # Only the host's main thread remains after the connection closes.
                # Inspect the real OS thread boundary.
                _wait_until(
                    lambda: len(list(Path(f"/proc/{process.pid}/task").iterdir())) == 1
                )
                assert ScriptSocketDependency(path).check()
            else:
                process.send_signal(signal.SIGTERM)
                assert process.wait(timeout=4) == 0
                assert not path.exists()
