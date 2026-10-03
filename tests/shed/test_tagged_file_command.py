"""The tagged cp/mv prototype resolves effects before guarded execution."""

import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from roboz import Agent, runtime
from roboz.agent import AgentMode
from roboz.llm import MockLLMEndpoint
from roboz.models import Role
from roboz.runtime import EventPipe
from roboz.shed.models import (
    ActionVerdict,
    CommandReady,
    GuardStatus,
    Operation,
    ParseError,
    PermissionRule,
)
from roboz.shed.tools import get_run_file_command
from roboz.shed.tools import runner
from roboz.shed.tools.cli_commands.tagged_transfer import (
    TaggedFileCommand,
    TaggedToken,
    get_run_tagged_file_command,
)
from roboz.shed.tools.cli_commands.tagged_transfer.command import resolve_tagged_command
from roboz.shed.tools.cli_commands.tagged_transfer.contracts import TokenRule
from roboz.shed.tools.cli_commands.tagged_transfer.helpers import validate_tokens
from roboz.shed.tools.cli_commands.tagged_transfer.specs import CP
from roboz.shed.tools.cli_commands.utilities.constants import MAX_COMMAND_OUTPUT_CHARS
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.tools import stop


def _resolve(tmp_path: Path, value: list[TaggedToken]):
    return resolve_tagged_command(tmp_path)(TaggedFileCommand(value=value), [])


def _invoke(
    tools: list, calls: list[dict], *, mode: AgentMode = AgentMode.STEERABLE
) -> list[dict]:
    agent = Agent(
        name="tagged_transfer_test",
        mode=mode,
        system_prompt="Perform the requested file transfer.",
        tools=[*tools, stop],
        agent_endpoint=MockLLMEndpoint(
            responses=[*calls, {"action": "stop", "rationale": "Done", "value": "ok"}]
        ),
        initial_messages=None,
    )
    _, messages = agent.invoke()
    return [json.loads(m.content) for m in messages if m.role == Role.USER]


def _call(value: list[TaggedToken]) -> dict:
    # JSON arrays, rather than Python tuples, exercise the agent's wire input.
    return {
        "action": "run_tagged_file_command",
        "rationale": "Transfer the requested file.",
        "value": [list(token) for token in value],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"tokens": [["cp", "CMD"]]},
        {"value": [["cp", "CMD", "extra"]]},
        {"value": [["cp", "UNKNOWN"]]},
    ],
)
def test_input_rejects_wrong_contract(payload: dict) -> None:
    with pytest.raises(ValidationError):
        TaggedFileCommand.model_validate(payload)


def test_token_rules_define_consumed_values_and_post_option_operands() -> None:
    spec = replace(
        CP,
        command=("example", "CMD"),
        allowed=(
            TokenRule(
                tag="FLG", pattern=re.compile(r"-n"), option="count", takes="ARG"
            ),
            TokenRule(
                tag="FLG", pattern=re.compile(r"--"), option="end", ends_options=True
            ),
            TokenRule(tag="ARG", pattern=re.compile(r"\d+")),
        ),
        forbidden_pairs=(),
    )
    parsed = validate_tokens(
        [("example", "CMD"), ("-n", "FLG"), ("2", "ARG"), ("--", "FLG"), ("3", "ARG")],
        spec,
    )
    assert parsed.options == {"count": 1, "end": 3}
    assert [parsed.argv[index] for index in parsed.operands] == ["3"]


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ([("source", "ARG"), ("target", "PTH")], "Unsupported ARG"),
        ([("mv", "CMD")], "Unsupported CMD"),
        ([("-n", "FLG")], "Unsupported FLG"),
        ([("-r", "FLG"), ("--recursive", "FLG")], "Repeated option"),
        ([("-vt", "FLG")], "Unsupported FLG"),
        ([("--target-directory=out", "FLG")], "Unsupported FLG"),
        ([("-t", "FLG"), ("out", "ARG")], "following PTH"),
        ([("-v", "FLG"), ("--verbose", "FLG")], "Repeated option"),
        (
            [
                ("--target-directory", "FLG"),
                ("out", "PTH"),
                ("-v", "FLG"),
                ("-T", "FLG"),
            ],
            "cannot be used together",
        ),
        ([("source", "PTH"), ("-v", "FLG"), ("target", "PTH")], "Place flags before"),
        ([("*.txt", "PTH"), ("out", "PTH")], "Unsupported PTH"),
    ],
)
def test_unsupported_tokens_stop_preparation(
    tmp_path: Path, arguments: list[TaggedToken], error: str
) -> None:
    result = _resolve(tmp_path, [("cp", "CMD"), *arguments])
    assert isinstance(result, ParseError)
    assert error in result.message


@pytest.mark.parametrize(
    ("command", "source_operation"), [("cp", Operation.READ), ("mv", Operation.DELETE)]
)
def test_destination_option_resolves_each_actual_output(
    tmp_path: Path, command: str, source_operation: Operation
) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "out").mkdir()
    result = _resolve(
        tmp_path,
        [
            (command, "CMD"),
            ("-t", "FLG"),
            ("out", "PTH"),
            ("a.txt", "PTH"),
            ("b.txt", "PTH"),
        ],
    )
    assert isinstance(result, ResolvedFileCommand)
    assert [(item.operation, item.location) for item in result.items] == [
        (source_operation, tmp_path / "a.txt"),
        (Operation.CREATE, tmp_path / "out/a.txt"),
        (source_operation, tmp_path / "b.txt"),
        (Operation.CREATE, tmp_path / "out/b.txt"),
    ]
    ready = result.items[0].value
    assert isinstance(ready, CommandReady)
    assert ready.argv == [
        command,
        "-t",
        str(tmp_path / "out"),
        str(tmp_path / "a.txt"),
        str(tmp_path / "b.txt"),
    ]


def test_exact_file_destination_rejects_directory(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("data")
    (tmp_path / "out").mkdir()
    result = _resolve(
        tmp_path, [("cp", "CMD"), ("-T", "FLG"), ("source", "PTH"), ("out", "PTH")]
    )
    assert isinstance(result, ParseError)
    assert "Destination must be a regular file" in result.message


@pytest.mark.parametrize(
    "kind",
    [
        "directory",
        "source_symlink",
        "parent_symlink",
        "destination_symlink",
        "collision",
    ],
)
def test_unsupported_filesystem_shapes_are_rejected(tmp_path: Path, kind: str) -> None:
    source = tmp_path / "source"
    source.write_text("data")
    (tmp_path / "out").mkdir()
    source_name = "source"
    destination_name = "out"
    if kind == "directory":
        source_name = "out"
        destination_name = "copy"
    elif kind == "source_symlink":
        (tmp_path / "link").symlink_to(source)
        source_name = "link"
    elif kind == "parent_symlink":
        (tmp_path / "link").symlink_to(tmp_path / "out", target_is_directory=True)
        destination_name = "link/../target"
    elif kind == "destination_symlink":
        (tmp_path / "out/source").symlink_to(source)
    else:
        destination_name = "source"
    result = _resolve(
        tmp_path, [("cp", "CMD"), (source_name, "PTH"), (destination_name, "PTH")]
    )
    assert isinstance(result, ParseError)
    assert source.read_text() == "data"


@pytest.mark.parametrize("directory", [False, True])
def test_cross_filesystem_move_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    if directory:
        (tmp_path / "source/nested").mkdir(parents=True)
        (tmp_path / "source/nested/file").write_text("data")
    else:
        (tmp_path / "source").write_text("data")
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    original_stat = Path.stat

    def stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        result = original_stat(path, follow_symlinks=follow_symlinks)
        if path == target_dir:
            fields = list(result)
            fields[2] = result.st_dev + 1
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, "stat", stat)
    result = _resolve(tmp_path, [("mv", "CMD"), ("source", "PTH"), ("out", "PTH")])
    assert isinstance(result, ParseError)
    assert "Cross-filesystem" in result.message


@pytest.mark.skipif(
    not shutil.which("cp") or not shutil.which("mv"), reason="Requires cp and mv"
)
def test_agent_executes_copy_and_move_through_existing_chain(tmp_path: Path) -> None:
    (tmp_path / "-source").write_text("payload")
    (tmp_path / "out").mkdir()
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(pattern="-source", operations={Operation.READ}),
            PermissionRule(
                pattern="out/*", operations={Operation.CREATE, Operation.DELETE}
            ),
        ],
    )
    calls = [
        _call([("cp", "CMD"), ("--", "FLG"), ("-source", "PTH"), ("out", "PTH")]),
        _call(
            [
                ("mv", "CMD"),
                ("-T", "FLG"),
                ("out/-source", "PTH"),
                ("out/renamed", "PTH"),
            ]
        ),
    ]
    # Both tool families can be installed without colliding chain names.
    legacy_tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.deny
    )
    responses = _invoke([*tools, *legacy_tools], calls)
    callers = [response.get("caller") for response in responses]
    assert callers == [
        "run_tagged_file_command",
        "guard_tagged_file_command",
        "execute_tagged_file_command",
        "run_tagged_file_command",
        "guard_tagged_file_command",
        "execute_tagged_file_command",
        "stop",
    ]
    assert (tmp_path / "out/renamed").read_text() == "payload"
    assert (tmp_path / "-source").read_text() == "payload"
    assert not (tmp_path / "out/-source").exists()
    execution_results = [
        response["value"]
        for response in responses
        if response.get("caller") == "execute_tagged_file_command"
    ]
    assert all(
        value.startswith("Overall: success (exit 0)") for value in execution_results
    )


@pytest.mark.parametrize(
    ("outcome", "detail"),
    [
        ("nonzero", "exit 2"),
        ("timeout", "timeout"),
        ("error", "execution error"),
        ("oversized", "output too large; exit 0"),
    ],
)
def test_single_command_execution_reports_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, detail: str
) -> None:
    (tmp_path / "source").write_text("data")

    def run(
        argv: list[str], cwd: Path, stdin: str | None
    ) -> subprocess.CompletedProcess[str]:
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        if outcome == "error":
            raise OSError("Cannot execute command")
        if outcome == "oversized":
            return subprocess.CompletedProcess(
                argv, 0, "x" * (MAX_COMMAND_OUTPUT_CHARS + 1), ""
            )
        return subprocess.CompletedProcess(argv, 2, "", "Copy failed")

    monkeypatch.setattr(runner, "run_cli_argv", run)
    tools = get_run_tagged_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    responses = _invoke(
        tools, [_call([("cp", "CMD"), ("source", "PTH"), ("target", "PTH")])]
    )
    result = next(
        response
        for response in responses
        if response.get("caller") == "execute_tagged_file_command"
    )
    assert result["value"].startswith(f"Overall: failure ({detail})")


@pytest.mark.parametrize(
    "failure",
    ["invalid_tag", "source_denied", "destination_denied", "overwrite_denied"],
)
def test_chain_stops_before_execution_on_invalid_or_denied_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    (tmp_path / "source").write_text("source content")
    (tmp_path / "out").mkdir()
    if failure == "overwrite_denied":
        (tmp_path / "out/source").write_text("keep this")

    def unexpected_execution(*args, **kwargs):
        pytest.fail("An invalid or denied call reached subprocess execution")

    monkeypatch.setattr(runner, "run_cli_argv", unexpected_execution)
    allow_rules = []
    if failure != "source_denied":
        allow_rules.append(
            PermissionRule(pattern="source", operations={Operation.READ})
        )
    if failure != "destination_denied":
        allow_rules.append(
            PermissionRule(pattern="out/*", operations={Operation.CREATE})
        )
    tools = get_run_tagged_file_command(
        base=tmp_path, default_verdict=ActionVerdict.deny, allow_rules=allow_rules
    )
    source_tag = "ARG" if failure == "invalid_tag" else "PTH"
    responses = _invoke(
        tools, [_call([("cp", "CMD"), ("source", source_tag), ("out", "PTH")])]
    )
    assert "execute_tagged_file_command" not in [
        response.get("caller") for response in responses
    ]
    if failure == "invalid_tag":
        assert responses[0]["kind"] == "parse_error"
    else:
        assert (
            next(
                response
                for response in responses
                if response.get("caller") == "guard_tagged_file_command"
            )["status"]
            == GuardStatus.DENIED
        )
    assert (tmp_path / "source").read_text() == "source content"
    if failure == "overwrite_denied":
        assert (tmp_path / "out/source").read_text() == "keep this"
    else:
        assert not (tmp_path / "out/source").exists()


def test_overwrite_permissions_are_explicit_and_preserve_input(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("new")
    (tmp_path / "target").write_text("old")
    entry, guard, execute = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(pattern="source", operations={Operation.READ}),
            PermissionRule(
                pattern="target",
                operations={Operation.CREATE, Operation.READ, Operation.DELETE},
            ),
        ],
    )
    command = TaggedFileCommand(
        value=[("cp", "CMD"), ("source", "PTH"), ("target", "PTH")]
    )
    prepared = entry(command, [])
    assert isinstance(prepared, ResolvedFileCommand)
    assert prepared.original_input == command
    assert [(item.operation, item.location) for item in prepared.items] == [
        (Operation.READ, tmp_path / "source"),
        (Operation.CREATE, tmp_path / "target"),
        (Operation.READ, tmp_path / "target"),
        (Operation.DELETE, tmp_path / "target"),
    ]
    result = guard(prepared, [])
    assert result.original_input == command
    assert result.status == GuardStatus.ALLOWED
    assert execute.chain_condition(result)


@pytest.mark.parametrize(
    "reply", ["no", "yikes, don't do that", "yes, but don't execute", "", None]
)
def test_user_decline_stops_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str | None
) -> None:
    (tmp_path / "source").write_text("keep")
    monkeypatch.setattr(runtime, "interact_with_user", lambda *args, **kwargs: reply)
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule(pattern="source", operations={Operation.DELETE})],
        pipe=EventPipe(),
    )
    responses = _invoke(
        tools, [_call([("mv", "CMD"), ("source", "PTH"), ("target", "PTH")])]
    )
    assert "execute_tagged_file_command" not in [
        response.get("caller") for response in responses
    ]
    denied = next(
        response
        for response in responses
        if response.get("caller") == "guard_tagged_file_command"
    )
    assert denied["deny_reason"] == "user_declined"
    assert (tmp_path / "source").read_text() == "keep"


@pytest.mark.parametrize("command", ["cp", "mv"])
@pytest.mark.parametrize("role", ["source", "destination"])
def test_hard_linked_files_stop_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, role: str
) -> None:
    (tmp_path / "source").write_text("replacement")
    (tmp_path / "allowed").mkdir()
    (tmp_path / "private").mkdir()
    secret = tmp_path / "private/secret"
    secret.write_text("protected")
    linked = tmp_path / ("alias" if role == "source" else "allowed/output")
    linked.hardlink_to(secret)

    def unexpected_execution(*args, **kwargs):
        pytest.fail("A hard-linked file reached execution")

    monkeypatch.setattr(runner, "run_cli_argv", unexpected_execution)
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(
                pattern="source", operations={Operation.READ, Operation.DELETE}
            ),
            PermissionRule(
                pattern="alias", operations={Operation.READ, Operation.DELETE}
            ),
            PermissionRule(pattern="allowed/*", operations=set(Operation)),
        ],
        deny_rules=[PermissionRule(pattern="private/**", operations=set(Operation))],
    )
    source = "alias" if role == "source" else "source"
    responses = _invoke(
        tools, [_call([(command, "CMD"), (source, "PTH"), ("allowed/output", "PTH")])]
    )
    assert responses[0]["kind"] == "parse_error"
    assert "Hard-linked" in responses[0]["message"]
    assert str(linked) in responses[0]["message"]
    assert secret.read_text() == linked.read_text() == "protected"
    assert (tmp_path / "source").read_text() == "replacement"


@pytest.mark.parametrize(
    ("name", "pattern", "allowed"),
    [
        ("source ", "source", False),
        (r"source\child", "source/child", False),
        ("source ", "source ", True),
        (r"source\child", r"source\child", True),
    ],
)
def test_filename_rules_preserve_spaces_and_backslashes(
    tmp_path: Path, name: str, pattern: str, allowed: bool
) -> None:
    (tmp_path / name).write_text("data")
    entry, guard, execute = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(pattern=pattern, operations={Operation.READ}),
            PermissionRule(pattern="target", operations={Operation.CREATE}),
        ],
    )
    prepared = entry(
        TaggedFileCommand(value=[("cp", "CMD"), (name, "PTH"), ("target", "PTH")]), []
    )
    result = guard(prepared, [])
    assert (result.status == GuardStatus.ALLOWED) is allowed
    assert execute.chain_condition(result) is allowed


@pytest.mark.parametrize("command", ["cp", "mv"])
@pytest.mark.parametrize(
    "operation", [Operation.CREATE, Operation.READ, Operation.DELETE]
)
@pytest.mark.parametrize("accepted", [True, False])
def test_each_overwrite_approval_controls_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    operation: Operation,
    accepted: bool,
) -> None:
    (tmp_path / "source").write_text("new")
    (tmp_path / "target").write_text("old")
    prompts: list[str] = []

    def approve(message: str, *, with_reply: bool) -> str:
        prompts.append(message)
        if not accepted:
            return "no"
        return " YES\n" if command == "cp" else " y "

    monkeypatch.setattr(runtime, "interact_with_user", approve)
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule(pattern="target", operations={operation})],
        pipe=EventPipe(),
    )
    responses = _invoke(
        tools, [_call([(command, "CMD"), ("source", "PTH"), ("target", "PTH")])]
    )
    assert len(prompts) == 1
    assert operation.value in prompts[0] and str(tmp_path / "target") in prompts[0]
    executed = "execute_tagged_file_command" in [r.get("caller") for r in responses]
    assert executed is accepted
    assert (tmp_path / "target").read_text() == ("new" if accepted else "old")
    if accepted and command == "mv":
        assert not (tmp_path / "source").exists()
    else:
        assert (tmp_path / "source").read_text() == "new"


@pytest.mark.parametrize("operation", [Operation.READ, Operation.DELETE])
def test_later_policy_denial_prevents_all_approval_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: Operation
) -> None:
    (tmp_path / "source").write_text("new")
    (tmp_path / "target").write_text("old")

    def unexpected_prompt(*args, **kwargs):
        pytest.fail("Approval requested for a command with a policy denial")

    monkeypatch.setattr(runtime, "interact_with_user", unexpected_prompt)
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule(pattern="source", operations={Operation.READ})],
        deny_rules=[PermissionRule(pattern="target", operations={operation})],
        pipe=EventPipe(),
    )
    responses = _invoke(
        tools, [_call([("cp", "CMD"), ("source", "PTH"), ("target", "PTH")])]
    )
    assert responses[1]["deny_reason"] == "policy_denied"
    assert "execute_tagged_file_command" not in [r.get("caller") for r in responses]
    assert (tmp_path / "source").read_text() == "new"
    assert (tmp_path / "target").read_text() == "old"


def test_every_required_approval_must_succeed_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "source").write_text("new")
    (tmp_path / "target").write_text("old")
    prompts: list[str] = []

    def approve_first_only(message: str, *, with_reply: bool) -> str:
        prompts.append(message)
        return "yes" if len(prompts) == 1 else "no"

    monkeypatch.setattr(runtime, "interact_with_user", approve_first_only)
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[
            PermissionRule(
                pattern="target", operations={Operation.READ, Operation.DELETE}
            ),
            PermissionRule(pattern="target", operations={Operation.READ}),
        ],
        pipe=EventPipe(),
    )
    responses = _invoke(
        tools, [_call([("cp", "CMD"), ("source", "PTH"), ("target", "PTH")])]
    )
    assert len(prompts) == 2
    assert "read" in prompts[0] and "delete" in prompts[1]
    assert responses[1]["deny_reason"] == "user_declined"
    assert "execute_tagged_file_command" not in [r.get("caller") for r in responses]
    assert (tmp_path / "source").read_text() == "new"
    assert (tmp_path / "target").read_text() == "old"


@pytest.mark.parametrize("precedence", [ActionVerdict.allow, ActionVerdict.deny])
def test_policy_precedence_does_not_bypass_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, precedence: ActionVerdict
) -> None:
    (tmp_path / "source").write_text("keep")
    prompts: list[str] = []

    def decline(message: str, *, with_reply: bool) -> str:
        prompts.append(message)
        return "no"

    monkeypatch.setattr(runtime, "interact_with_user", decline)
    rules = [PermissionRule(pattern="source", operations={Operation.READ})]
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=rules,
        deny_rules=rules,
        ask_rules=rules,
        takes_precedence=precedence,
        pipe=EventPipe(),
    )
    responses = _invoke(
        tools, [_call([("cp", "CMD"), ("source", "PTH"), ("target", "PTH")])]
    )
    expected_reason = (
        "user_declined" if precedence == ActionVerdict.allow else "policy_denied"
    )
    assert responses[1]["deny_reason"] == expected_reason
    assert len(prompts) == (1 if precedence == ActionVerdict.allow else 0)
    assert "execute_tagged_file_command" not in [r.get("caller") for r in responses]
    assert not (tmp_path / "target").exists()


def test_missing_interaction_channel_denies_and_agent_continues(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("new")
    (tmp_path / "target").write_text("old")
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule(pattern="target", operations={Operation.DELETE})],
        pipe=EventPipe(),
    )
    responses = _invoke(
        tools,
        [_call([("cp", "CMD"), ("source", "PTH"), ("target", "PTH")])],
        mode=AgentMode.AUTONOMOUS,
    )

    assert responses[1]["status"] == GuardStatus.DENIED
    assert "Approval unavailable" in responses[1]["message"]
    assert "execute_tagged_file_command" not in [r.get("caller") for r in responses]
    assert responses[-1]["caller"] == "stop"
    assert (tmp_path / "source").read_text() == "new"
    assert (tmp_path / "target").read_text() == "old"


@pytest.mark.parametrize("failure", ["no_context", "runtime_error", "value_error"])
def test_unavailable_approval_denies_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    (tmp_path / "source").write_text("keep")

    def unavailable(*args, **kwargs):
        if failure == "no_context":
            pytest.fail("Prompt attempted without an interaction context")
        error = RuntimeError if failure == "runtime_error" else ValueError
        raise error("Interaction unavailable")

    monkeypatch.setattr(runtime, "interact_with_user", unavailable)
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule(pattern="source", operations={Operation.READ})],
        pipe=None if failure == "no_context" else EventPipe(),
    )
    responses = _invoke(
        tools, [_call([("cp", "CMD"), ("source", "PTH"), ("target", "PTH")])]
    )
    assert responses[1]["status"] == GuardStatus.DENIED
    assert "Approval unavailable" in responses[1]["message"]
    assert "execute_tagged_file_command" not in [r.get("caller") for r in responses]
    assert (tmp_path / "source").read_text() == "keep"
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize("absolute", [True, False])
def test_only_absolute_rules_can_authorize_a_source_outside_base(
    tmp_path: Path, absolute: bool
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    source = tmp_path / "source"
    source.write_text("data")
    entry, guard, execute = get_run_tagged_file_command(
        base=work,
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(
                pattern=lambda: str(source) if absolute else "**",
                operations={Operation.READ},
            ),
            PermissionRule(pattern="target", operations={Operation.CREATE}),
        ],
    )
    prepared = entry(
        TaggedFileCommand(
            value=[("cp", "CMD"), ("../source", "PTH"), ("target", "PTH")]
        ),
        [],
    )
    result = guard(prepared, [])
    assert (result.status == GuardStatus.ALLOWED) is absolute
    assert execute.chain_condition(result) is absolute


def _tree_contents(root: Path) -> dict[str, tuple[int, bytes | None]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_mode & 0o777,
            path.read_bytes() if path.is_file() else None,
        )
        for path in root.rglob("*")
    }


@pytest.mark.skipif(
    not shutil.which("cp") or not shutil.which("mv"), reason="Requires cp and mv"
)
@pytest.mark.parametrize(
    ("command", "arguments", "existing"),
    [
        ("cp", "-r src new/", None),
        ("cp", "-R src out", "out/src"),
        ("cp", "--recursive -T src out", "out"),
        ("cp", "-R src/. out", "out"),
        ("cp", "-R src/. new/", None),
        ("cp", "-R src/nested/.. out", "out"),
        ("cp", "-R --strip-trailing-slashes src/// out", None),
        ("cp", "-R -t out src file", None),
        ("cp", "-R src new/.", None),
        ("cp", "-f file readonly", None),
        ("mv", "src new/", None),
        ("mv", "src/. out", None),
        ("mv", "-v src out", None),
        ("mv", "-T src out", None),
        ("mv", "--force --strip-trailing-slashes src/// out", None),
        ("mv", "-t out src file", None),
        ("mv", "-f file readonly", None),
    ],
)
def test_directory_transfers_match_native_commands(
    tmp_path: Path, command: str, arguments: str, existing: str | None
) -> None:
    native = tmp_path / "native"
    guarded = tmp_path / "guarded"
    for root in (native, guarded):
        (root / "src/nested/empty").mkdir(parents=True)
        (root / "src/nested/file").write_text("nested")
        (root / "src/.hidden").write_text("hidden")
        (root / "out").mkdir()
        (root / "file").write_text("replacement")
        (root / "readonly").write_text("old")
        (root / "readonly").chmod(0o444)
        if existing is not None:
            (root / existing / "nested").mkdir(parents=True, exist_ok=True)
            (root / existing / "nested/file").write_text("old")
            (root / existing / "unrelated").write_text("keep")
    argv = arguments.split()
    expected = subprocess.run([command, *argv], cwd=native, capture_output=True)
    tools = get_run_tagged_file_command(
        base=guarded, default_verdict=ActionVerdict.allow
    )
    tokens: list[TaggedToken] = [(command, "CMD")]
    tokens.extend((value, "FLG" if value.startswith("-") else "PTH") for value in argv)
    responses = _invoke(tools, [_call(tokens)])
    execution = next(
        response
        for response in responses
        if response.get("caller") == "execute_tagged_file_command"
    )
    status = "success" if expected.returncode == 0 else "failure"
    assert execution["value"].startswith(
        f"Overall: {status} (exit {expected.returncode})"
    )
    assert _tree_contents(guarded) == _tree_contents(native)


def test_copy_merge_permissions_include_empty_directories_and_overwrites(
    tmp_path: Path,
) -> None:
    (tmp_path / "src/empty").mkdir(parents=True)
    (tmp_path / "src/.hidden").write_text("new")
    (tmp_path / "out").mkdir()
    (tmp_path / "out/.hidden").write_text("old")
    # An unrelated destination link is not traversed or modified by a merge.
    (tmp_path / "out/unrelated").symlink_to(tmp_path / "missing")
    result = _resolve(
        tmp_path,
        [("cp", "CMD"), ("-R", "FLG"), ("src/.", "PTH"), ("out", "PTH")],
    )
    assert isinstance(result, ResolvedFileCommand)
    assert {
        (item.operation, item.location.relative_to(tmp_path).as_posix())
        for item in result.items
    } == {
        (Operation.READ, "src"),
        (Operation.READ, "src/empty"),
        (Operation.READ, "src/.hidden"),
        (Operation.CREATE, "out"),
        (Operation.CREATE, "out/empty"),
        (Operation.CREATE, "out/.hidden"),
        (Operation.READ, "out/.hidden"),
        (Operation.DELETE, "out/.hidden"),
    }
    assert result.items[0].value.argv[-2] == f"{tmp_path}/src/."


@pytest.mark.parametrize(
    ("command", "denied_path", "operation"),
    [
        ("cp", "src/nested/file", Operation.READ),
        ("cp", "out/empty", Operation.CREATE),
        ("cp", "out/nested/file", Operation.READ),
        ("cp", "out/nested/file", Operation.DELETE),
        ("mv", "src/nested/file", Operation.DELETE),
        ("mv", "out/nested/file", Operation.CREATE),
        ("mv", "out", Operation.DELETE),
    ],
)
def test_recursive_policy_denial_prevents_every_write_and_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    denied_path: str,
    operation: Operation,
) -> None:
    (tmp_path / "src/nested").mkdir(parents=True)
    (tmp_path / "src/empty").mkdir()
    (tmp_path / "src/nested/file").write_text("new")
    (tmp_path / "out").mkdir()
    if command == "cp":
        (tmp_path / "out/nested").mkdir()
        (tmp_path / "out/nested/file").write_text("old")
    before = _tree_contents(tmp_path)

    def unexpected_prompt(*args, **kwargs):
        pytest.fail("A denied recursive transfer requested approval")

    monkeypatch.setattr(runtime, "interact_with_user", unexpected_prompt)
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        deny_rules=[PermissionRule(pattern=denied_path, operations={operation})],
        ask_rules=[PermissionRule(pattern="src", operations=set(Operation))],
        pipe=EventPipe(),
    )
    tokens: list[TaggedToken] = [(command, "CMD")]
    if command == "cp":
        tokens.append(("-R", "FLG"))
    tokens.extend([("-f", "FLG"), ("-T", "FLG"), ("src", "PTH"), ("out", "PTH")])
    responses = _invoke(tools, [_call(tokens)])
    assert responses[1]["deny_reason"] == "policy_denied"
    assert "execute_tagged_file_command" not in [r.get("caller") for r in responses]
    assert _tree_contents(tmp_path) == before


@pytest.mark.parametrize("accepted", [True, False])
def test_recursive_force_copy_respects_nested_overwrite_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accepted: bool
) -> None:
    (tmp_path / "src/nested").mkdir(parents=True)
    (tmp_path / "src/nested/file").write_text("new")
    (tmp_path / "out/nested").mkdir(parents=True)
    (tmp_path / "out/nested/file").write_text("old")
    prompts: list[str] = []

    def approve(message: str, *, with_reply: bool) -> str:
        prompts.append(message)
        return "yes" if accepted else "no"

    monkeypatch.setattr(runtime, "interact_with_user", approve)
    tools = get_run_tagged_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[
            PermissionRule(pattern="out/nested/file", operations={Operation.DELETE})
        ],
        pipe=EventPipe(),
    )
    responses = _invoke(
        tools,
        [
            _call(
                [
                    ("cp", "CMD"),
                    ("-R", "FLG"),
                    ("--force", "FLG"),
                    ("-T", "FLG"),
                    ("src", "PTH"),
                    ("out", "PTH"),
                ]
            )
        ],
    )
    assert len(prompts) == 1 and str(tmp_path / "out/nested/file") in prompts[0]
    assert (
        "execute_tagged_file_command" in [r.get("caller") for r in responses]
    ) is accepted
    assert (tmp_path / "out/nested/file").read_text() == ("new" if accepted else "old")


@pytest.mark.parametrize(
    "kind", ["source_symlink", "destination_symlink", "hardlink", "fifo"]
)
def test_recursive_unsupported_entries_reject_the_whole_transfer(
    tmp_path: Path, kind: str
) -> None:
    (tmp_path / "src/nested").mkdir(parents=True)
    (tmp_path / "src/nested/file").write_text("new")
    (tmp_path / "out/src").mkdir(parents=True)
    (tmp_path / "private").write_text("protected")
    if kind == "source_symlink":
        (tmp_path / "src/nested/link").symlink_to(tmp_path / "private")
    elif kind == "destination_symlink":
        (tmp_path / "out/src/nested").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "hardlink":
        (tmp_path / "src/nested/link").hardlink_to(tmp_path / "private")
    else:
        os.mkfifo(tmp_path / "src/nested/pipe")
    tools = get_run_tagged_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    responses = _invoke(
        tools,
        [
            _call(
                [
                    ("cp", "CMD"),
                    ("-R", "FLG"),
                    ("src", "PTH"),
                    ("out", "PTH"),
                ]
            )
        ],
    )
    assert responses[0]["kind"] == "parse_error"
    assert "execute_tagged_file_command" not in [r.get("caller") for r in responses]
    assert (tmp_path / "private").read_text() == "protected"
    assert not (tmp_path / "out/src/nested/file").exists()


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("cp", "src new"),
        ("mv", "-r src new"),
        ("mv", "-T src out"),
        ("cp", "-R -T src file"),
        ("cp", "-R -T file out"),
        ("cp", "-R src src/nested"),
        ("cp", "-R src src/nested out"),
        ("cp", "-R src other/src out"),
        ("cp", "-R src/. other/src out"),
    ],
)
def test_recursive_conflicts_fail_before_execution(
    tmp_path: Path, command: str, arguments: str
) -> None:
    (tmp_path / "src/nested").mkdir(parents=True)
    (tmp_path / "src/nested/file").write_text("new")
    (tmp_path / "other/src").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    (tmp_path / "out/keep").write_text("old")
    (tmp_path / "file").write_text("file")
    before = _tree_contents(tmp_path)
    tokens: list[TaggedToken] = [(command, "CMD")]
    tokens.extend(
        (value, "FLG" if value.startswith("-") else "PTH")
        for value in arguments.split()
    )
    tools = get_run_tagged_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    responses = _invoke(tools, [_call(tokens)])
    assert responses[0]["kind"] == "parse_error"
    assert "execute_tagged_file_command" not in [r.get("caller") for r in responses]
    assert _tree_contents(tmp_path) == before


def test_recursive_traversal_error_stops_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src/nested").mkdir(parents=True)
    original_iterdir = Path.iterdir

    def iterdir(path: Path):
        if path == tmp_path / "src/nested":
            raise PermissionError("Cannot list nested directory")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    result = _resolve(
        tmp_path,
        [
            ("cp", "CMD"),
            ("-R", "FLG"),
            ("src", "PTH"),
            ("out", "PTH"),
        ],
    )
    assert isinstance(result, ParseError)
    assert "Cannot list nested directory" in result.message
    assert not (tmp_path / "out").exists()
