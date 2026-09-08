"""Tests for chained CLI commands (PIPE and AND).

Supports multiple commands per call via chain=PIPE (output flows to next stdin)
or chain=AND (commands run sequentially, each with fresh stdin).
Cannot mix PIPE and AND in a single chain.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from roboz.agent import Agent
from roboz.tools import stop
from roboz.models import Message, Stop
from roboz.llm import get_truncated_messages_for_context
from roboz.models.truncation import LIGHT_MAX_CHARS, Severity, Truncation
from roboz.models import Role
from roboz.llm import MockLLMEndpoint
from roboshed.models import (
    ActionVerdict,
    Operation,
    PermissionRule,
    RunFileCommand,
    RunFileCommands,
)
from roboshed.tools import get_run_file_command
from roboshed.tools.cli_commands.run_file_command.specs import (
    FILE_COMMANDS_READ,
    FILE_COMMANDS_WRITE,
)
from roboshed.tools.cli_commands.utilities.cmd_spec import CmdSpec
from roboshed.tools.cli_commands.utilities.constants import (
    PIPE_OUTPUT_TO_NEXT_COMMAND,
    PIPE_STDIN_FROM_PREVIOUS_COMMAND,
)

FILE_COMMANDS = FILE_COMMANDS_READ + FILE_COMMANDS_WRITE


def _invoke_payload(input_cmd: RunFileCommands) -> dict[str, Any]:
    return {
        "action": "run_file_command",
        "rationale": "Run scripted CLI chain",
        "chain": input_cmd.chain,
        "file_commands": [cmd.model_dump() for cmd in input_cmd.file_commands],
    }


def _invoke_cli_with_tools(
    tools: list, input_cmd: RunFileCommands
) -> tuple[Stop, list[Message]]:
    endpoint = MockLLMEndpoint(
        responses=[
            _invoke_payload(input_cmd),
            {"action": "stop", "rationale": "done", "value": "ok"},
        ]
    )
    agent = Agent(
        interaction_mode=None,
        name="cli_chain_agent",
        tools=[*tools, stop],
        system_prompt="Run requested commands.",
        agent_endpoint=endpoint,
        initial_messages=None,
    )
    output, messages = agent.invoke()
    assert isinstance(output, Stop)
    return output, messages


def _user_payload_entries(
    messages: list[Message],
) -> list[tuple[Message, dict[str, Any]]]:
    entries: list[tuple[Message, dict[str, Any]]] = []
    for message in messages:
        if message.role != Role.USER:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append((message, payload))
    return entries


def _caller_entries(
    messages: list[Message], *, caller: str
) -> list[tuple[Message, dict[str, Any]]]:
    return [
        (message, payload)
        for message, payload in _user_payload_entries(messages)
        if payload.get("caller") == caller
    ]


def _last_execute_file_command_value(messages: list[Message]) -> str:
    for _, payload in reversed(
        _caller_entries(messages, caller="execute_file_command")
    ):
        value = payload.get("value")
        if isinstance(value, str):
            return value
    raise AssertionError("No execute_file_command value found in messages")


def _assert_framed_output(s: str, *, sections: int = 1) -> None:
    assert s.count("--- begin:") == sections
    assert s.count("--- end:") == sections


def _extract_callers(messages: list) -> list[str]:
    callers: list[str] = []
    for message in messages:
        if message.role != Role.USER:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        caller = payload.get("caller")
        if isinstance(caller, str):
            callers.append(caller)
    return callers


# Custom specs with echo for tests that need it
SPECS_WITH_ECHO = (*FILE_COMMANDS, CmdSpec(name="echo"))


# -----------------------------------------------------------------------------
# PIPE chains (stdout -> next command stdin)
# -----------------------------------------------------------------------------


def test_pipe_two_commands_cat_grep(tmp_path: Path) -> None:
    """cat file | grep pattern: grep filters cat output."""
    (tmp_path / "data.txt").write_text("alpha\nbeta\nalpha\ngamma")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="cat", argv=["data.txt"]),
            RunFileCommand(command="grep", argv=["alpha"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=2)
    assert result.index("--- begin: cat") < result.index("--- begin: grep")
    assert PIPE_OUTPUT_TO_NEXT_COMMAND in result
    assert "beta" not in result and "gamma" not in result
    m = re.search(
        r"--- begin: grep[^\n]*\n(.*)\n--- end: grep[^\n]*",
        result,
        re.DOTALL,
    )
    assert m is not None
    grep_body = m.group(1)
    assert grep_body.startswith(PIPE_STDIN_FROM_PREVIOUS_COMMAND)
    assert "beta" not in grep_body and "gamma" not in grep_body
    assert sum(1 for line in grep_body.splitlines() if line.strip() == "alpha") == 2


def test_pipe_echo_to_cat(tmp_path: Path) -> None:
    """echo produces output, cat reads from stdin (no paths)."""
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        command_specs=SPECS_WITH_ECHO,
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="echo", argv=["hello world"]),
            RunFileCommand(command="cat", argv=[]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=2)
    assert result.index("--- begin: echo") < result.index("--- begin: cat")
    assert PIPE_OUTPUT_TO_NEXT_COMMAND in result
    assert PIPE_STDIN_FROM_PREVIOUS_COMMAND in result
    assert "hello world" in result
    assert "cat" in result


def test_pipe_three_commands_cat_grep_wc(tmp_path: Path) -> None:
    """cat | grep | wc -l: full pipeline."""
    (tmp_path / "log.txt").write_text("info: a\ninfo: b\nerror: x\ninfo: c")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="cat", argv=["log.txt"]),
            RunFileCommand(command="grep", argv=["info:"]),
            RunFileCommand(command="wc", argv=["-l"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=3)
    assert (
        result.index("--- begin: cat")
        < result.index("--- begin: grep")
        < result.index("--- begin: wc")
    )
    assert result.count(PIPE_OUTPUT_TO_NEXT_COMMAND) == 2
    assert result.count(PIPE_STDIN_FROM_PREVIOUS_COMMAND) == 2
    assert "wc" in result
    assert "3" in result


def test_pipe_cat_to_tee(tmp_path: Path) -> None:
    """cat file | tee copy: read and write in one pipeline."""
    (tmp_path / "source.txt").write_text("original content")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(
                pattern="*.txt", operations={Operation.READ, Operation.CREATE}
            ),
            PermissionRule(pattern="**", operations={Operation.READ}),
        ],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="cat", argv=["source.txt"]),
            RunFileCommand(command="tee", argv=["copy.txt"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=2)
    assert result.index("--- begin: cat") < result.index("--- begin: tee")
    assert PIPE_OUTPUT_TO_NEXT_COMMAND in result
    assert PIPE_STDIN_FROM_PREVIOUS_COMMAND in result
    assert "tee" in result
    assert "original content" in result
    assert (tmp_path / "copy.txt").read_text() == "original content"


def test_pipe_grep_no_matches_returns_error(tmp_path: Path) -> None:
    """cat | grep nonexistent: grep exits 1 when no match, chain returns error."""
    (tmp_path / "f.txt").write_text("line1\nline2")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="cat", argv=["f.txt"]),
            RunFileCommand(command="grep", argv=["NOMATCH"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    assert "produced no output" in result
    assert "grep" in result


def test_pipe_huge_output_stops_chain_with_explicit_error(tmp_path: Path) -> None:
    """Huge command output fails closed and does not flow to next pipe step."""
    huge_content = "x\n" * LIGHT_MAX_CHARS * 2
    (tmp_path / "huge.txt").write_text(huge_content)
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="cat", argv=["huge.txt"]),
            RunFileCommand(command="grep", argv=["x"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    execute_entries = _caller_entries(messages, caller="execute_file_command")
    assert len(execute_entries) == 1
    final_message, final_payload = execute_entries[0]
    out = final_payload.get("value")
    assert isinstance(out, str)
    assert "Command output too large" in out
    assert "Chain stopped with no partial output returned" in out
    assert "rg --max-count" in out
    assert "--- begin: cat" in out
    assert "--- begin: grep" not in out
    # The payload itself is small enough that context truncation should not be
    # needed for safety in this path.
    assert len(final_message.content) < LIGHT_MAX_CHARS
    truncated = get_truncated_messages_for_context([final_message])
    assert len(truncated) == 1
    assert truncated[0].content == final_message.content


def test_and_huge_output_stops_chain_before_followup_command(tmp_path: Path) -> None:
    """Huge output in AND chain aborts later commands to preserve semantics."""
    huge_content = "x\n" * LIGHT_MAX_CHARS * 2
    (tmp_path / "huge.txt").write_text(huge_content)
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        command_specs=SPECS_WITH_ECHO,
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="cat", argv=["huge.txt"]),
            RunFileCommand(command="echo", argv=["second_step_ran"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    execute_entries = _caller_entries(messages, caller="execute_file_command")
    assert len(execute_entries) == 1
    out = execute_entries[0][1].get("value")
    assert isinstance(out, str)
    assert "Command output too large" in out
    assert "second_step_ran" not in out


# -----------------------------------------------------------------------------
# AND chains (sequential execution, no stdin passthrough)
# -----------------------------------------------------------------------------


def test_and_two_commands_touch_then_ls(tmp_path: Path) -> None:
    """touch a && ls: create file then list."""
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(pattern="**", operations={Operation.READ, Operation.CREATE})
        ],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="touch", argv=["created.txt"]),
            RunFileCommand(command="ls", argv=["."]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=2)
    assert "touch" in result and "ls" in result
    assert (tmp_path / "created.txt").exists()
    assert "created.txt" in result


def test_and_mkdir_touch_ls(tmp_path: Path) -> None:
    """mkdir sub && touch sub/f && ls sub: three-step sequence."""
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(pattern="**", operations={Operation.READ, Operation.CREATE})
        ],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="mkdir", argv=["sub"]),
            RunFileCommand(command="touch", argv=["sub/f"]),
            RunFileCommand(command="ls", argv=["sub"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=3)
    assert result.index("mkdir") < result.index("touch") < result.index("ls")
    assert (tmp_path / "sub" / "f").exists()
    assert "f" in result


def test_and_cp_copies_file(tmp_path: Path) -> None:
    """cp copies a file when READ(source) and CREATE(destination) are allowed."""
    source = tmp_path / "source.txt"
    source.write_text("payload")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE},
            )
        ],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="cp", argv=["source.txt", "copy.txt"]),
            RunFileCommand(command="cat", argv=["copy.txt"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=2)
    assert source.read_text() == "payload"
    assert (tmp_path / "copy.txt").read_text() == "payload"
    assert "payload" in result


def test_cp_recursive_copies_directory(tmp_path: Path) -> None:
    """cp -r copies a directory tree while preserving the source."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "nested.txt").write_text("nested payload")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE},
            )
        ],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cp", argv=["-r", "source", "copied"])],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result)
    assert (source / "nested.txt").read_text() == "nested payload"
    assert (tmp_path / "copied" / "nested.txt").read_text() == "nested payload"


def test_cp_overwrite_requires_delete_on_destination(tmp_path: Path) -> None:
    """cp src dst is denied when dst exists but DELETE is not permitted on dst."""
    source = tmp_path / "source.txt"
    destination = tmp_path / "target.txt"
    source.write_text("new")
    destination.write_text("old")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(pattern="source.txt", operations={Operation.READ}),
            PermissionRule(
                pattern="target.txt",
                operations={Operation.READ, Operation.CREATE},
            ),
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cp", argv=["source.txt", "target.txt"])],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    callers = _extract_callers(messages)
    assert callers.count("operation_guard") >= 1
    assert callers.count("execute_file_command") == 0
    assert destination.read_text() == "old"


def test_and_mv_moves_file(tmp_path: Path) -> None:
    """mv moves a file when DELETE(source) and CREATE(destination) are allowed."""
    src = tmp_path / "source.txt"
    src.write_text("payload")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="mv", argv=["source.txt", "moved.txt"]),
            RunFileCommand(command="cat", argv=["moved.txt"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=2)
    assert not src.exists()
    assert (tmp_path / "moved.txt").read_text() == "payload"
    assert "payload" in result


def test_mv_overwrite_requires_delete_on_destination(tmp_path: Path) -> None:
    """mv src dst is denied when dst exists but DELETE is not permitted on dst."""
    src = tmp_path / "source.txt"
    dst = tmp_path / "target.txt"
    src.write_text("new")
    dst.write_text("old")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(pattern="source.txt", operations={Operation.DELETE}),
            PermissionRule(pattern="target.txt", operations={Operation.CREATE}),
            PermissionRule(
                pattern="**",
                operations={Operation.READ},
            ),
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="mv", argv=["source.txt", "target.txt"])],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    callers = _extract_callers(messages)
    assert callers.count("operation_guard") >= 1
    assert callers.count("execute_file_command") == 0
    assert src.exists()
    assert dst.read_text() == "old"


def test_and_second_command_does_not_receive_first_stdout(tmp_path: Path) -> None:
    """With AND, cmd2's stdin is not from cmd1 (unlike PIPE). Accumulated output includes both."""
    (tmp_path / "secret.txt").write_text("secret data")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        command_specs=SPECS_WITH_ECHO,
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="cat", argv=["secret.txt"]),
            RunFileCommand(command="echo", argv=["from_echo"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=2)
    assert result.index("cat") < result.index("echo")
    assert "from_echo" in result
    assert "secret" in result


def test_and_accumulates_all_outputs(tmp_path: Path) -> None:
    """AND chain returns each command's output in a framed section, in order."""
    (tmp_path / "a.txt").write_text("file a")
    (tmp_path / "b.txt").write_text("file b")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="cat", argv=["a.txt"]),
            RunFileCommand(command="cat", argv=["b.txt"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=2)
    assert result.index("a.txt") < result.index("b.txt")
    assert "file a" in result
    assert "file b" in result


def test_and_touch_then_tee_allows_when_delete_unmatched_and_default_is_allow(
    tmp_path: Path,
) -> None:
    """Touch then tee with CREATE-only rules falls back to default allow for DELETE."""
    (tmp_path / "plans").mkdir()
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(pattern="**", operations={Operation.READ}),
            PermissionRule(pattern="plans/*.md", operations={Operation.CREATE}),
        ],
        deny_rules=[PermissionRule(pattern="**", operations={Operation.CREATE})],
        takes_precedence=ActionVerdict.allow,
    )
    plan_path = tmp_path / "plans" / "plan_123.md"
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="touch", argv=["plans/plan_123.md"]),
            RunFileCommand(command="tee", argv=["plans/plan_123.md"], stdin="content"),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    callers = _extract_callers(messages)
    assert callers.count("execute_file_command") == 2
    assert callers.count("operation_guard") >= 2
    assert plan_path.exists()
    assert plan_path.read_text() == "content"


def test_and_touch_then_tee_succeeds_when_delete_allowed(tmp_path: Path) -> None:
    """When both CREATE and DELETE are allowed, touch then tee writes content."""
    (tmp_path / "plans").mkdir()
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(
                pattern="plans/*.md",
                operations={Operation.CREATE, Operation.DELETE},
            ),
            PermissionRule(pattern="**", operations={Operation.READ}),
        ],
        deny_rules=[],
    )
    plan_path = tmp_path / "plans" / "plan_123.md"
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="touch", argv=["plans/plan_123.md"]),
            RunFileCommand(
                command="tee", argv=["plans/plan_123.md"], stdin="tee content"
            ),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    _ = _last_execute_file_command_value(messages)
    assert plan_path.read_text() == "tee content"


# -----------------------------------------------------------------------------
# Single command (unchained behavior)
# -----------------------------------------------------------------------------


def test_single_command_unchanged(tmp_path: Path) -> None:
    """Single command result is framed with begin/end markers."""
    (tmp_path / "x.txt").write_text("hello")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["x.txt"])],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=1)
    assert "hello" in result
    assert "cat" in result
    assert "x.txt" in result


def test_run_file_command_default_uses_light_truncation(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("hello")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["x.txt"])],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    final_entry = next(
        entry
        for entry in reversed(_caller_entries(messages, caller="execute_file_command"))
        if "value" in entry[1]
    )
    truncation = final_entry[0].truncation

    # Plain LIGHT at every distance: a fresh command output is capped, not graded
    # (the GRADED policy exists but is intentionally not wired up yet).
    assert truncation == Truncation(threshold=0, severity=Severity.LIGHT)


def test_single_success_no_output_is_explicit(tmp_path: Path) -> None:
    """Commands like mkdir that succeed silently should return explicit success/no-output message."""
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(pattern="**", operations={Operation.READ, Operation.CREATE})
        ],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="mkdir", argv=["subdir"])],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=1)
    assert "[success]" in result
    assert "no output" in result
    assert "mkdir" in result


# -----------------------------------------------------------------------------
# Explicit chain requirement
# -----------------------------------------------------------------------------


def test_chain_must_be_explicit(tmp_path: Path) -> None:
    """RunFileCommands rejects payloads that omit chain."""
    with pytest.raises(ValidationError):
        RunFileCommands.model_validate(
            {"file_commands": [{"command": "cat", "argv": ["data"]}]}
        )


def test_single_command_with_explicit_pipe_chain(tmp_path: Path) -> None:
    """Single command still works when chain is explicitly provided."""
    (tmp_path / "data").write_text("a\nb\na")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="cat", argv=["data"]),
            RunFileCommand(command="grep", argv=["a"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=2)
    assert result.index("--- begin: cat") < result.index("--- begin: grep")
    assert PIPE_STDIN_FROM_PREVIOUS_COMMAND in result
    assert "grep" in result
    assert "a" in result


# -----------------------------------------------------------------------------
# find argument ordering
# -----------------------------------------------------------------------------


def test_find_paths_then_predicates_succeeds(tmp_path: Path) -> None:
    """find argv: start directories first, then predicates."""
    (tmp_path / "needle.txt").write_text("x")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="find", argv=[".", "-name", "needle.txt"])
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=1)
    assert "find" in result
    assert "needle.txt" in result
    assert "paths must precede" not in result.lower()


def test_find_start_dir_first_with_maxdepth_succeeds(tmp_path: Path) -> None:
    """find with root and -maxdepth: root remains the first argv token."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "leaf.md").write_text("x")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(
                command="find",
                argv=[".", "-maxdepth", "3", "-name", "leaf.md"],
            )
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=1)
    assert "find" in result
    assert "leaf.md" in result
    assert "paths must precede" not in result.lower()


def test_non_find_ordering_unchanged(tmp_path: Path) -> None:
    """grep argv: pattern token then file path."""
    (tmp_path / "a.py").write_text("print(1)\n")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="grep", argv=["print", "a.py"])],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    _assert_framed_output(result, sections=1)
    assert "grep" in result
    assert "print" in result


# -----------------------------------------------------------------------------
# Error handling
# -----------------------------------------------------------------------------


def test_pipe_first_command_fails_stops_chain(tmp_path: Path) -> None:
    """If first command fails (exit non-zero), return error, no second command runs."""
    (tmp_path / "f.txt").write_text("line1\nline2")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="grep", argv=["NOMATCH", "f.txt"]),
            RunFileCommand(command="cat", argv=[]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    assert "exit" in result.lower() or "non-zero" in result.lower() or "1" in result


def test_and_first_command_fails_continues_chain(tmp_path: Path) -> None:
    """If first command fails in AND chain, accumulate error and run the next command."""
    (tmp_path / "f.txt").write_text("x")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        command_specs=SPECS_WITH_ECHO,
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="grep", argv=["NOMATCH", "f.txt"]),
            RunFileCommand(command="echo", argv=["second_step_ran"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    assert "exit" in result.lower() or "non-zero" in result.lower() or "1" in result
    assert "second_step_ran" in result
    _assert_framed_output(result, sections=2)


def test_and_success_then_second_fails_returns_combined(tmp_path: Path) -> None:
    """Last failure in AND chain includes prior framed successes."""
    (tmp_path / "f.txt").write_text("hello")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="cat", argv=["f.txt"]),
            RunFileCommand(command="grep", argv=["NOMATCH", "f.txt"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    result = _last_execute_file_command_value(messages)
    assert "hello" in result
    assert "exit" in result.lower() or "non-zero" in result.lower() or "1" in result
    _assert_framed_output(result, sections=2)


def test_and_touch_then_tee_runs_through_agent_routing(tmp_path: Path) -> None:
    """Regression: execute -> passive -> guard -> execute must run for remaining command."""
    (tmp_path / "plans").mkdir()

    endpoint = MockLLMEndpoint(
        responses=[
            {
                "action": "run_file_command",
                "rationale": "Write plan file in two AND steps",
                "chain": "and",
                "file_commands": [
                    {
                        "command": "touch",
                        "argv": ["plans/plan_123.md"],
                    },
                    {
                        "command": "tee",
                        "argv": ["plans/plan_123.md"],
                        "stdin": "final plan body",
                    },
                ],
            },
            {"action": "stop", "rationale": "done", "value": "ok"},
        ]
    )

    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(
                pattern="plans/*.md",
                operations={Operation.CREATE, Operation.DELETE},
            ),
            PermissionRule(pattern="**", operations={Operation.READ}),
        ],
        deny_rules=[],
    )

    agent = Agent(
        interaction_mode=None,
        name="cli_chain_agent_plans",
        tools=[*tools, stop],
        system_prompt="Run requested commands.",
        agent_endpoint=endpoint,
        initial_messages=None,
    )

    output, messages = agent.invoke()
    callers = _extract_callers(messages)

    assert isinstance(output, Stop)
    assert (tmp_path / "plans" / "plan_123.md").read_text() == "final plan body"

    # Command 1 path: run_file_command -> operation_guard -> execute_file_command
    # Command 2 path: run_file_command_passive -> operation_guard -> execute_file_command
    assert "run_file_command_passive" in callers
    assert callers.count("operation_guard") >= 2
    assert callers.count("execute_file_command") >= 2


def test_pipe_three_commands_run_full_guard_cycle(tmp_path: Path) -> None:
    """Each piped command is independently resolved, guarded, then executed.

    Pins the shared cycle wired by ``build_guarded_tool_chain`` plus the CLI
    continuation loop: one active resolve, two passive resolves, and a guard +
    execute for every command. Stdout flows to the next command only via stdin.
    """
    (tmp_path / "log.txt").write_text("info: a\ninfo: b\nerror: x\ninfo: c")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="cat", argv=["log.txt"]),
            RunFileCommand(command="grep", argv=["info:"]),
            RunFileCommand(command="wc", argv=["-l"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    callers = _extract_callers(messages)

    assert callers.count("run_file_command") == 1
    assert callers.count("run_file_command_passive") == 2
    assert callers.count("operation_guard") == 3
    assert callers.count("execute_file_command") == 3

    result = _last_execute_file_command_value(messages)
    assert result.count(PIPE_STDIN_FROM_PREVIOUS_COMMAND) == 2


def test_and_denied_later_command_breaks_before_execution(tmp_path: Path) -> None:
    """A later AND command denied by policy stops the cycle before it executes.

    The first command resolves, guards, and executes; the second resolves and
    guards but is denied, so the guard result never reaches execute.
    """
    (tmp_path / "a.txt").write_text("file a")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="a.txt", operations={Operation.READ})],
        deny_rules=[],
    )
    input_cmd = RunFileCommands(
        chain="and",
        file_commands=[
            RunFileCommand(command="cat", argv=["a.txt"]),
            RunFileCommand(command="cat", argv=["b.txt"]),
        ],
    )
    _, messages = _invoke_cli_with_tools(tools, input_cmd)
    callers = _extract_callers(messages)

    # Both commands resolve and guard, but only the first (allowed) executes.
    assert callers.count("operation_guard") == 2
    assert callers.count("execute_file_command") == 1

    denied_entries = _caller_entries(messages, caller="operation_guard")
    _, last_guard_payload = denied_entries[-1]
    assert last_guard_payload.get("status") == "denied"


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("cat", ["--", "-private"]),
        ("cat", ["private-link", "-n"]),
        ("grep", ["needle", "--", "-private"]),
        ("rg", ["needle", "--", "-private"]),
        ("grep", ["--max-count", "1", "needle", "private-link"]),
        ("rg", ["-e", "needle", "private-link"]),
        ("rg", ["--files", "private-link"]),
        ("tee", ["private-link", "-a"]),
        ("touch", ["private-link", "-c"]),
    ],
)
def test_cli_parser_guards_outside_symlinks_before_execution(
    tmp_path: Path, command: str, argv: list[str]
) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    content = "needle OUTSIDE_SENTINEL\n"
    outside.write_text(content)
    original_stat = outside.stat()
    (base / "-private").symlink_to(outside)
    (base / "private-link").symlink_to(outside)
    tools = get_run_file_command(
        base=base,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule("**", {Operation.READ, Operation.CREATE, Operation.DELETE})
        ],
    )
    _, messages = _invoke_cli_with_tools(
        tools,
        RunFileCommands(
            chain="and",
            file_commands=[
                RunFileCommand(command=command, argv=argv, stdin="new content\n")
            ],
        ),
    )
    assert (
        _caller_entries(messages, caller="operation_guard")[-1][1]["status"] == "denied"
    )
    assert "execute_file_command" not in _extract_callers(messages)
    assert all("OUTSIDE_SENTINEL" not in message.content for message in messages)
    assert outside.read_text() == content
    assert outside.stat().st_mtime_ns == original_stat.st_mtime_ns


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("grep", ["-fprivate-link", "allowed.txt"]),
        ("grep", ["--file", "private-link", "allowed.txt"]),
        ("grep", ["--exclude-from=private-link", "needle", "allowed.txt"]),
        ("rg", ["--ignore-file", "private-link", "needle", "allowed.txt"]),
        ("rg", ["--pre=private-link", "needle", "allowed.txt"]),
        ("rg", ["--pre-glob=*.txt", "needle", "allowed.txt"]),
        ("wc", ["--files0-from=private-link"]),
        ("diff", ["-Xprivate-link", "a", "b"]),
        ("diff", ["-uXprivate-link", "a", "b"]),
        ("touch", ["--reference=private-link", "allowed.txt"]),
        ("find", ["-files0-from", "private-link"]),
        ("find", [".", "-newer", "private-link"]),
        ("head", ["--lin", "1", "private-link"]),
        ("cat", ["--number=yes", "private-link"]),
        ("head", ["allowed.txt", "--lines"]),
        ("rg", ["needle", "missing-*.txt"]),
    ],
)
def test_cli_parser_rejects_unaccounted_arguments_before_guard_or_executor(
    tmp_path: Path, command: str, argv: list[str]
) -> None:
    tools = get_run_file_command(base=tmp_path, default_verdict=ActionVerdict.allow)
    _, messages = _invoke_cli_with_tools(
        tools,
        RunFileCommands(
            chain="and",
            file_commands=[RunFileCommand(command=command, argv=argv)],
        ),
    )
    result = _caller_entries(messages, caller="run_file_command")[-1][1]
    assert result["kind"] == "parse_error"
    assert "Available CLI commands" in result["message"]
    assert "operation_guard" not in _extract_callers(messages)
    assert "execute_file_command" not in _extract_callers(messages)


@pytest.mark.parametrize(
    ("command", "argv", "stdin", "expected"),
    [
        ("cat", ["--", "-public"], None, "needle PUBLIC_SENTINEL"),
        ("cat", ["./-public", "-n"], None, "needle PUBLIC_SENTINEL"),
        (
            "grep",
            ["--max-count", "1", "needle", "--", "-public"],
            None,
            "needle PUBLIC_SENTINEL",
        ),
        (
            "rg",
            ["-g", "*.txt", "-e", "needle", "allowed.txt"],
            None,
            "needle PUBLIC_SENTINEL",
        ),
        ("rg", ["--files", "allowed.txt"], None, "allowed.txt"),
        ("cat", ["--", "-"], "STDIN_SENTINEL\n", "STDIN_SENTINEL"),
        ("head", ["--lines=1", "allowed.txt"], None, "needle PUBLIC_SENTINEL"),
    ],
)
def test_cli_parser_executes_supported_inputs(
    tmp_path: Path, command: str, argv: list[str], stdin: str | None, expected: str
) -> None:
    import shutil

    if shutil.which(command) is None:
        pytest.skip(f"{command} is not installed")
    (tmp_path / "-public").write_text("needle PUBLIC_SENTINEL\n")
    (tmp_path / "allowed.txt").write_text("needle PUBLIC_SENTINEL\n")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule("**", {Operation.READ})],
    )
    _, messages = _invoke_cli_with_tools(
        tools,
        RunFileCommands(
            chain="and",
            file_commands=[RunFileCommand(command=command, argv=argv, stdin=stdin)],
        ),
    )
    result = _last_execute_file_command_value(messages)
    assert expected in result
    assert "exited with code" not in result
