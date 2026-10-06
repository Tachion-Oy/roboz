"""Local execution and the public trusted-script capability contracts."""

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from roboz.deployment import DeployableAgent
from roboz.exceptions import ExternalCallCancelledError
from roboz.llm import MockLLMEndpoint
from roboz.models import Stop
from roboz.runtime.events import ScriptOutputEvent
from roboz.shed.capabilities import SafeScripts
from roboz.shed.sandbox import PermissionPolicy
from roboz.shed.tools.safe_scripts import RunShellScriptInput
from safe_scripts_support import _agent, _script

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="trusted Bash scripts require POSIX and Bash",
)


def test_discovery_and_success_share_one_tool_without_construction_side_effects(
    tmp_path: Path,
) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    capability = SafeScripts(scripts_dir=scripts)
    definition, agent, tool = _agent(workspace, capability)
    assert not workspace.exists() and not scripts.exists()
    assert [dep.dependency_id for dep in definition.external_dependencies()] == [
        "executable:bash"
    ]
    absent = tool(RunShellScriptInput(), [])
    assert absent.status == "listed" and not absent.scripts
    _script(scripts, "nested/hello world.sh", "# Print a greeting.\nprintf 'hello'\n")
    workspace.mkdir()
    listed = tool(RunShellScriptInput(), [])
    assert [(entry.script, entry.description) for entry in listed.scripts] == [
        ("nested/hello world.sh", "Print a greeting.")
    ]
    result = tool(RunShellScriptInput(script="nested/hello world.sh"), [])
    assert (result.status, result.exit_code, result.output) == ("success", 0, "hello")
    assert agent.pipe is not None


def test_each_build_copies_the_script_tool_identity(tmp_path: Path) -> None:
    capability = SafeScripts(scripts_dir=tmp_path / "installed")
    _, _, first_tool = _agent(tmp_path / "workspace", capability)
    _, _, second_tool = _agent(tmp_path / "workspace", capability)
    assert first_tool.name == second_tool.name == "run_shell_script"
    assert first_tool.id != second_tool.id


def test_local_execution_requires_sandbox_instead_of_file_permissions(
    tmp_path: Path,
) -> None:
    definition = DeployableAgent(
        name="script_test",
        system_prompt="Use installed scripts.",
        capabilities=(SafeScripts(scripts_dir=tmp_path / "installed"),),
    )
    definition.set_attributes(permissions=PermissionPolicy.local(tmp_path))
    definition.set_agent_endpoint(MockLLMEndpoint([]))
    with pytest.raises(ValueError, match="sandbox.*Sandbox"):
        definition.build()


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/etc/passwd",
        "../outside.sh",
        "nested/../okay.sh",
        "not-shell.py",
        "C:\\outside.sh",
    ],
)
def test_untrusted_paths_are_refused(tmp_path: Path, name: str) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    _script(scripts, "okay.sh", "echo safe\n")
    workspace.mkdir()
    _, _, tool = _agent(workspace, SafeScripts(scripts_dir=scripts))
    assert tool(RunShellScriptInput(script=name), []).status == "refused"


def test_symlinks_and_non_scripts_never_appear_or_run(tmp_path: Path) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    outside = _script(tmp_path, "outside.sh", "echo escaped\n")
    _script(scripts, "real.sh", "echo okay\n")
    (scripts / "alias.sh").symlink_to(outside)
    (scripts / "alias-dir").symlink_to(tmp_path, target_is_directory=True)
    (scripts / "note.txt").write_text("not a script")
    workspace.mkdir()
    _, _, tool = _agent(workspace, SafeScripts(scripts_dir=scripts))
    assert [entry.script for entry in tool(RunShellScriptInput(), []).scripts] == [
        "real.sh"
    ]
    for name in ("alias.sh", "alias-dir/outside.sh", "note.txt"):
        assert tool(RunShellScriptInput(script=name), []).status == "refused"


def test_output_streaming_exit_failure_and_environment_filtering(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    workspace.mkdir()
    _script(
        scripts,
        "report.sh",
        'printf \'%s|%s|%s\' "$PWD" "${SAFE_SCRIPT_TEST_ALLOWED:-missing}" '
        '"${SAFE_SCRIPT_TEST_HIDDEN:-missing}"\nexit 7\n',
    )
    monkeypatch.setenv("SAFE_SCRIPT_TEST_HIDDEN", "secret")
    observed = []
    _, _, tool = _agent(
        workspace,
        SafeScripts(scripts_dir=scripts, env_allowlist=("SAFE_SCRIPT_TEST_ALLOWED",)),
        sink=observed.append,
    )
    monkeypatch.setenv("SAFE_SCRIPT_TEST_ALLOWED", "visible")
    result = tool(RunShellScriptInput(script="report.sh"), [])
    assert (result.status, result.exit_code) == ("failed", 7)
    assert result.output == f"{workspace}|visible|missing"
    assert (
        "".join(
            event.content for event in observed if isinstance(event, ScriptOutputEvent)
        )
        == result.output
    )


def test_silent_timeout_and_output_limit(tmp_path: Path) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    workspace.mkdir()
    _script(scripts, "quiet.sh", "sleep 5\n")
    _script(scripts, "loud.sh", "printf 'abcdefghijklmnop'\n")
    _, _, timeout_tool = _agent(
        workspace, SafeScripts(scripts_dir=scripts, timeout_s=0.15)
    )
    started = time.monotonic()
    assert timeout_tool(RunShellScriptInput(script="quiet.sh"), []).status == "timeout"
    assert time.monotonic() - started < 3
    _, _, limited_tool = _agent(
        workspace, SafeScripts(scripts_dir=scripts, max_output_bytes=8)
    )
    limited = limited_tool(RunShellScriptInput(script="loud.sh"), [])
    assert (limited.status, limited.output) == ("output_limit", "abcdefgh")


@pytest.mark.parametrize("limit", [4097, 4098])
def test_utf8_output_at_byte_limit(tmp_path: Path, limit: int) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    workspace.mkdir()
    content = "x" * 4095 + "€"
    _script(scripts, "unicode.sh", f"printf '{content}'\n")
    events = []
    _, _, tool = _agent(
        workspace,
        SafeScripts(scripts_dir=scripts, max_output_bytes=limit),
        sink=events.append,
    )

    result = tool(RunShellScriptInput(script="unicode.sh"), [])

    assert result.status == ("success" if limit == 4098 else "output_limit")
    assert result.output == content.encode()[:limit].decode(errors="replace")
    assert (
        "".join(
            event.content for event in events if isinstance(event, ScriptOutputEvent)
        )
        == result.output
    )


def test_startup_failure_releases_execution_gate(tmp_path: Path) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    _script(scripts, "done.sh", "echo done\n")
    _, _, tool = _agent(workspace, SafeScripts(scripts_dir=scripts))

    assert tool(RunShellScriptInput(script="done.sh"), []).status == "failed"
    workspace.mkdir()
    assert tool(RunShellScriptInput(script="done.sh"), []).status == "success"


def test_timeout_stops_background_child(tmp_path: Path) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    workspace.mkdir()
    _script(
        scripts,
        "children.sh",
        "(sleep 0.5; echo leaked > child-marker.txt) &\nwait\n",
    )
    _, _, tool = _agent(workspace, SafeScripts(scripts_dir=scripts, timeout_s=0.1))
    assert tool(RunShellScriptInput(script="children.sh"), []).status == "timeout"
    time.sleep(0.6)
    assert not (workspace / "child-marker.txt").exists()


def test_success_stops_detached_background_child(tmp_path: Path) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    workspace.mkdir()
    _script(
        scripts,
        "children.sh",
        "(sleep 0.5; echo leaked > child-marker.txt) >/dev/null 2>&1 &\necho done\n",
    )
    _, _, tool = _agent(workspace, SafeScripts(scripts_dir=scripts))
    assert tool(RunShellScriptInput(script="children.sh"), []).status == "success"
    time.sleep(0.6)
    assert not (workspace / "child-marker.txt").exists()


def test_cancellation_releases_shared_execution_gate(tmp_path: Path) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    workspace.mkdir()
    _script(scripts, "wait.sh", "echo ready\nsleep 10\n")
    _script(scripts, "done.sh", "echo done\n")
    started = Event()

    def sink(event):
        if isinstance(event, ScriptOutputEvent) and "ready" in event.content:
            started.set()

    capability = SafeScripts(scripts_dir=scripts)
    _, first, first_tool = _agent(workspace, capability, sink=sink)
    _, _, second_tool = _agent(workspace, capability)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first_tool, RunShellScriptInput(script="wait.sh"), [])
        assert started.wait(timeout=2)
        assert second_tool(RunShellScriptInput(script="wait.sh"), []).status == "busy"
        first.pipe.cancel()
        with pytest.raises(ExternalCallCancelledError):
            future.result(timeout=4)
    done = second_tool(RunShellScriptInput(script="done.sh"), [])
    assert done.status == "success"


def test_cancellation_after_script_closes_output(tmp_path: Path) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    workspace.mkdir()
    _script(
        scripts,
        "wait.sh",
        "exec >/dev/null 2>&1\nprintf ready > ready.txt\nexec sleep 10\n",
    )
    _, agent, tool = _agent(workspace, SafeScripts(scripts_dir=scripts, timeout_s=5))

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(tool, RunShellScriptInput(script="wait.sh"), [])
        deadline = time.monotonic() + 2
        while not (workspace / "ready.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (workspace / "ready.txt").exists()
        agent.pipe.cancel()
        with pytest.raises(ExternalCallCancelledError):
            future.result(timeout=2)


def test_script_tool_runs_in_real_agent_with_mock_endpoint(tmp_path: Path) -> None:
    workspace, scripts = tmp_path / "workspace", tmp_path / "installed"
    workspace.mkdir()
    _script(scripts, "greet.sh", "echo hello-from-script\n")
    events = []
    definition, _, _ = _agent(workspace, SafeScripts(scripts_dir=scripts))
    definition.set_agent_endpoint(
        MockLLMEndpoint(
            [
                {"action": "run_shell_script", "rationale": "discover scripts"},
                {
                    "action": "run_shell_script",
                    "rationale": "run greeting",
                    "script": "greet.sh",
                },
                {"action": "stop", "rationale": "finished", "value": "done"},
            ]
        )
    )
    agent, _ = definition.build(event_sinks=(events.append,))
    result, _ = agent.invoke()
    assert isinstance(result, Stop) and result.value == "done"
    assert any(
        isinstance(event, ScriptOutputEvent) and "hello-from-script" in event.content
        for event in events
    )


@pytest.mark.parametrize("during_description", [False, True])
def test_discovery_checks_cancellation_during_traversal_and_description(
    tmp_path, during_description
):
    from threading import Lock
    from roboz.dependencies import ExecutableDependency
    from roboz.shed.tools.safe_scripts.protocol import ExecutionPolicy
    from roboz.shed.tools.safe_scripts.server import Scripts

    _script(
        tmp_path,
        "first.sh",
        ("#" + "x" * 10000 + "\n") if during_description else "echo done\n",
    )
    if during_description:
        # A very long shebang requires several bounded reads before comments.
        (tmp_path / "first.sh").write_text("#!" + "x" * 10000 + "\n# description\n")
    else:
        _script(tmp_path, "second.sh", "echo done\n")
    runner = Scripts(
        tmp_path, tmp_path, ExecutionPolicy(), Lock(), ExecutableDependency("bash")
    )
    entries = []
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if (during_description and checks == 5) or (not during_description and entries):
            raise ExternalCallCancelledError("discovery cancelled")

    with pytest.raises(ExternalCallCancelledError):
        for event in runner.events(RunShellScriptInput(), check):
            entries.append(event)
    assert len(entries) == (0 if during_description else 1)


def test_discovery_deadline_preserves_already_discovered_entries(tmp_path):
    from threading import Lock
    from roboz.dependencies import ExecutableDependency
    from roboz.shed.tools.safe_scripts.protocol import ExecutionPolicy
    from roboz.shed.tools.safe_scripts.server import Scripts

    for index in range(2):
        _script(tmp_path, f"{index}.sh", "echo done\n")
    runner = Scripts(
        tmp_path,
        tmp_path,
        ExecutionPolicy(timeout_s=0.1),
        Lock(),
        ExecutableDependency("bash"),
    )
    entries = []

    from roboz.shed.tools.safe_scripts.protocol import Completed

    for event in runner.events(RunShellScriptInput(), lambda: None):
        if isinstance(event, Completed):
            assert event.status == "timeout" and len(entries) == 1
        else:
            entries.append(event)
            time.sleep(0.15)


@pytest.mark.parametrize("error_type", [ValueError, OSError, TimeoutError])
def test_local_sink_failure_cleans_up_before_releasing_the_execution_gate(
    tmp_path, error_type
):
    _script(tmp_path, "ready.sh", "echo ready\nsleep 10\n")
    _script(tmp_path, "done.sh", "echo done\n")
    capability = SafeScripts(scripts_dir=tmp_path)

    def sink(event):
        if isinstance(event, ScriptOutputEvent):
            raise error_type("sink failed")

    tool = _agent(tmp_path, capability, sink=sink)[2]
    with pytest.raises(error_type, match="sink failed"):
        tool(RunShellScriptInput(script="ready.sh"), [])
    result = _agent(tmp_path, capability)[2](RunShellScriptInput(script="done.sh"), [])
    assert result.status == "success"


@pytest.mark.parametrize(
    "body,expected",
    [
        ("#" + " " * 2000 + "Description\n", "Description"),
        ("#" + " " * 2000 + "\n# Description\n", "Description"),
        ("# " + "x" * 510 + " " * 2000 + "\n", "x" * 510),
        ("# " + "x" * 510 + " " * 2000 + "last\n", "x" * 510 + "  "),
    ],
)
def test_long_description_lines_keep_the_existing_strip_and_truncate_behavior(
    tmp_path, body, expected
):
    _script(tmp_path, "description.sh", body)
    listed = _agent(tmp_path, SafeScripts(scripts_dir=tmp_path))[2](
        RunShellScriptInput(), []
    )
    assert listed.scripts[0].description == expected
