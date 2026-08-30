"""Tests for the ``run_shell_script`` tool chain.

Real, harmless scripts run in a tmp_path safe directory so the actual code path
(including run_cli_argv/subprocess) executes; nothing is patched.
"""

import dataclasses
from pathlib import Path

import pytest
from roboz.exceptions import ExternalCallCancelledError
from roboz.models import Empty, Str
from roboz.runtime import (
    MessageDeltaEvent,
    MessageEvent,
    ScriptOutputEvent,
)
from roboz.runtime import EventPipe
from roboz.models import MessageKind, Role

from roboz.standard.identifiers import RUN_SHELL_SCRIPT_TOOL_NAME
from roboz.standard.skills.cli_commands.tools.run_shell_script import (
    RunShellScriptInput,
    ScriptReady,
    ShellScriptCtx,
    get_run_shell_script,
)
from roboz.standard.skills.cli_commands.runtime.runner import run_cli_argv_streamed


def _write_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")


def test_streamed_runner_honors_cancellation_without_timeout(tmp_path: Path) -> None:
    with pytest.raises(ExternalCallCancelledError):
        run_cli_argv_streamed(
            ["bash", "-c", "sleep 5"],
            tmp_path,
            lambda _: None,
            "sleep 5",
            timeout=None,
            is_cancelled=lambda: True,
        )


def _tools(
    scripts_dir: Path, *, base: Path | None = None, pipe: EventPipe | None = None
):
    resolve_tool, exec_tool = get_run_shell_script(
        base=base or scripts_dir,
        scripts_dir=scripts_dir,
        pipe=pipe or EventPipe(),
    )
    return resolve_tool, exec_tool


def _run(
    safe_dir: Path,
    script: str,
    *,
    base: Path | None = None,
    timeout_seconds: float | None = None,
    pipe: EventPipe | None = None,
) -> Str:
    """Drive the resolve -> execute chain by hand and return the final Str."""
    resolve_tool, exec_tool = _tools(safe_dir, base=base, pipe=pipe)
    resolved = resolve_tool(
        input=RunShellScriptInput(script=script, timeout_seconds=timeout_seconds),
        messages=[],
    )
    if exec_tool.chain_condition(resolved):
        out = exec_tool(input=resolved, messages=[])
        assert isinstance(out, Str)
        return out
    assert isinstance(resolved, Str)
    return resolved


def test_happy_path_runs_script(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    _write_script(safe / "hello.sh", "echo hi")

    out = _run(safe, "hello.sh")

    assert "hi" in out.value
    assert "bash hello.sh" in out.value  # framed with the display command


def test_subfolder_script_allowed(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    _write_script(safe / "ci" / "run.sh", "echo nested")

    out = _run(safe, "ci/run.sh")

    assert "nested" in out.value


def test_script_path_is_resolved_relative_to_base(tmp_path: Path) -> None:
    base = tmp_path / "sandbox"
    safe = base / "readonly" / "safe-scripts"
    _write_script(safe / "dump.sh", "echo from_safe")

    out = _run(safe, "readonly/safe-scripts/dump.sh", base=base)

    assert "from_safe" in out.value
    assert "bash readonly/safe-scripts/dump.sh" in out.value


def test_base_relative_script_outside_safe_dir_is_refused(tmp_path: Path) -> None:
    base = tmp_path / "sandbox"
    safe = base / "readonly" / "safe-scripts"
    _write_script(safe / "dump.sh", "echo from_safe")

    out = _run(safe, "dump.sh", base=base)

    assert "refused" in out.value


def test_absolute_script_path_inside_safe_dir_is_allowed(tmp_path: Path) -> None:
    base = tmp_path / "sandbox"
    safe = base / "readonly" / "safe-scripts"
    script_path = safe / "abs.sh"
    _write_script(script_path, "echo absolute_ok")

    out = _run(safe, str(script_path), base=base)

    assert "absolute_ok" in out.value


def test_script_executes_with_base_as_cwd(tmp_path: Path) -> None:
    base = tmp_path / "sandbox"
    safe = base / "readonly" / "safe-scripts"
    _write_script(safe / "cwd.sh", "pwd")

    out = _run(safe, "readonly/safe-scripts/cwd.sh", base=base)

    assert str(base.resolve()) in out.value


def test_script_outside_safe_dir_is_refused_and_not_executed(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    safe.mkdir()
    sentinel = tmp_path / "pwned.txt"
    _write_script(tmp_path / "other" / "evil.sh", f"touch {sentinel}")

    out = _run(safe, "../other/evil.sh")

    assert "refused" in out.value
    assert not sentinel.exists()  # the chain broke before execution


def test_symlink_escape_is_refused(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    safe.mkdir()
    real = tmp_path / "outside.sh"
    _write_script(real, "echo escaped")
    link = safe / "link.sh"
    link.symlink_to(real)

    out = _run(safe, "link.sh")

    assert "refused" in out.value


def test_non_sh_file_is_refused(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    _write_script(safe / "note.txt", "echo nope")

    out = _run(safe, "note.txt")

    assert "refused" in out.value


def test_missing_file_is_refused(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    safe.mkdir()

    out = _run(safe, "does_not_exist.sh")

    assert "refused" in out.value


def test_chain_condition_gates_on_script_ready(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    safe.mkdir()
    _, exec_tool = _tools(safe)

    assert exec_tool.chain_condition(
        ScriptReady(argv=["bash", "x"], cwd=safe, display="bash x")
    )
    assert not exec_tool.chain_condition(Str(value="refused: nope"))
    assert not exec_tool.chain_condition(Empty())


def test_nonzero_exit_is_reported_without_raising(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    _write_script(safe / "fail.sh", "exit 3")

    out = _run(safe, "fail.sh")

    assert "3" in out.value  # exit code surfaced via run_cli_argv's error format


def test_streams_each_line_to_user_as_it_runs(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    _write_script(safe / "multi.sh", "echo one\necho two\necho three")
    events: list[object] = []
    pipe = EventPipe(event_sinks=[events.append])

    out = _run(safe, "multi.sh", pipe=pipe)

    # Each line was emitted as runtime telemetry through the agent event pipe.
    script_events = [event for event in events if isinstance(event, ScriptOutputEvent)]
    assert [event.content for event in script_events] == ["one", "two", "three"]
    # ...and the agent still receives the full framed output at the end.
    assert "one" in out.value and "two" in out.value and "three" in out.value


def test_streamed_lines_finalize_as_one_user_notification(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    _write_script(safe / "multi.sh", "echo one\necho two\necho three")
    events: list[object] = []
    pipe = EventPipe(event_sinks=[events.append])

    out = _run(safe, "multi.sh", pipe=pipe)

    deltas = [event for event in events if isinstance(event, MessageDeltaEvent)]
    notifications = [event for event in events if isinstance(event, MessageEvent)]
    assert "".join(event.delta for event in deltas) == "one\ntwo\nthree\n"
    assert len({event.message_id for event in deltas}) == 1
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.message_id == deltas[0].message_id
    assert notification.message.role is Role.ASSISTANT
    assert notification.message.message_kind is MessageKind.USER_NOTIFICATION
    assert notification.message.content == out.value


def test_silent_script_emits_completed_notification_without_deltas(
    tmp_path: Path,
) -> None:
    safe = tmp_path / "safe"
    _write_script(safe / "silent.sh", ":")
    events: list[object] = []
    pipe = EventPipe(event_sinks=[events.append])

    out = _run(safe, "silent.sh", pipe=pipe)

    assert not any(isinstance(event, MessageDeltaEvent) for event in events)
    notifications = [event for event in events if isinstance(event, MessageEvent)]
    assert len(notifications) == 1
    assert notifications[0].message_id is None
    assert notifications[0].message.content == out.value
    assert "completed with no output" in notifications[0].message.content


def test_no_timeout_runs_to_completion(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    _write_script(safe / "sleeper.sh", "sleep 0.3\necho finished")

    out = _run(safe, "sleeper.sh")  # timeout_seconds defaults to None (unbounded)

    assert "finished" in out.value


def test_agent_timeout_kills_long_script(tmp_path: Path) -> None:
    safe = tmp_path / "safe"
    _write_script(safe / "slow.sh", "sleep 5\necho should_not_appear")
    events: list[object] = []
    pipe = EventPipe(event_sinks=[events.append])

    out = _run(safe, "slow.sh", timeout_seconds=0.5, pipe=pipe)

    assert "timed out" in out.value
    assert "0.5" in out.value  # the agent-supplied value, not a fixed constant
    assert "should_not_appear" not in out.value
    notifications = [event for event in events if isinstance(event, MessageEvent)]
    assert len(notifications) == 1
    assert notifications[0].message.content == out.value


def test_agent_facing_tool_is_named_run_shell_script(tmp_path: Path) -> None:
    resolve_tool, _ = _tools(tmp_path)
    assert resolve_tool.name == RUN_SHELL_SCRIPT_TOOL_NAME


def test_context_directory_is_immutable() -> None:
    ctx = ShellScriptCtx(base=Path("/tmp/base"), scripts_dir=Path("/tmp/safe"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.base = Path("/tmp/other-base")  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.scripts_dir = Path("/tmp/evil")  # type: ignore[misc]
