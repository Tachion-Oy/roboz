"""The packaged host command serves real scripts and reports startup failures."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from roboz.shed.tools.safe_scripts import (
    RunShellScriptInput,
    ScriptSocketDependency,
    request_script,
)

COMMAND = (sys.executable, "-m", "roboz.shed.tools.safe_scripts")


def test_command_help_and_argument_errors(tmp_path: Path) -> None:
    help_result = subprocess.run([*COMMAND, "--help"], capture_output=True, text=True)
    assert help_result.returncode == 0
    assert "serve" in help_result.stdout and "check" in help_result.stdout

    socket = tmp_path / "scripts.sock"
    for extra in (("--timeout-s", "0"), ("--allow-env", "PATH")):
        result = subprocess.run(
            [
                *COMMAND,
                "serve",
                "--socket",
                str(socket),
                "--scripts",
                str(tmp_path),
                "--cwd",
                str(tmp_path),
                *extra,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
        assert not socket.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="socket service requires Linux")
def test_command_check_reports_unavailable_service(tmp_path: Path) -> None:
    socket = tmp_path / "missing.sock"
    result = subprocess.run(
        [*COMMAND, "check", "--socket", str(socket)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "unavailable or incompatible" in result.stderr
    assert not socket.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="socket service requires Linux")
def test_command_serves_policy_and_removes_socket_on_shutdown(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "allowed.sh").write_text('printf %s "${CLI_ALLOWED:-missing}"\n')
    (scripts / "hidden.sh").write_text('printf %s "${CLI_HIDDEN:-gone}"\n')
    (scripts / "large.sh").write_text('printf 0123456789\n')
    (scripts / "slow.sh").write_text("sleep 1\n")
    (scripts / "write.sh").write_text("printf marker > marker.txt\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    socket_dir = tmp_path / "private"
    socket_dir.mkdir(mode=0o700)
    socket = socket_dir / "scripts.sock"
    host_env = {**os.environ, "CLI_ALLOWED": "yes", "CLI_HIDDEN": "secret"}
    process = subprocess.Popen(
        [
            *COMMAND,
            "serve",
            "--socket",
            str(socket),
            "--scripts",
            str(scripts),
            "--cwd",
            str(workspace),
            "--timeout-s",
            "0.15",
            "--max-output-bytes",
            "6",
            "--allow-env",
            "CLI_ALLOWED",
        ],
        env=host_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        dependency = ScriptSocketDependency(socket)
        while process.poll() is None and time.monotonic() < deadline:
            if dependency.check():
                break
            time.sleep(0.02)
        assert dependency.check(), (
            process.communicate(timeout=3)[1] if process.poll() is not None else ""
        )
        checked = subprocess.run(
            [*COMMAND, "check", "--socket", str(socket)],
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 0, checked.stderr

        def run(name: str):
            return request_script(
                RunShellScriptInput(script=name), dependency, lambda _: None, lambda: None
            )

        allowed = run("allowed.sh")
        assert (allowed.status, allowed.output) == ("success", "yes")
        hidden = run("hidden.sh")
        assert (hidden.status, hidden.output) == ("success", "gone")
        assert run("large.sh").status == "output_limit"
        assert run("slow.sh").status == "timeout"
        assert run("write.sh").status == "success"
        assert (workspace / "marker.txt").read_text() == "marker"
    finally:
        if process.poll() is None:
            process.terminate()
        _, errors = process.communicate(timeout=8)
    assert process.returncode == 0, errors
    assert not socket.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="socket service requires Linux")
def test_command_refuses_existing_socket_path(tmp_path: Path) -> None:
    socket_dir = tmp_path / "private"
    socket_dir.mkdir(mode=0o700)
    socket = socket_dir / "scripts.sock"
    socket.write_text("keep")
    result = subprocess.run(
        [
            *COMMAND,
            "serve",
            "--socket",
            str(socket),
            "--scripts",
            str(tmp_path),
            "--cwd",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert "Traceback" not in result.stderr
    assert socket.read_text() == "keep"
