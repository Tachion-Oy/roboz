"""Regression tests for `cli_tools` prompt examples.

Focus: validate the exact argv passed to subprocess and key output semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from roboz.agent import Agent
from roboz.tools import stop
from roboz.models import Message, Stop
from roboz.models import Role
from roboz.dependencies import ExecutableDependency
from roboz.llm import MockLLMEndpoint

from roboshed.models import (
    ActionVerdict,
    Help,
    Operation,
    PermissionRule,
    RunFileCommand,
    RunFileCommands,
)
from roboshed.skills.cli_tools.prompts import INSTRUCTIONS
from roboshed.tools import get_run_file_command
from roboshed.tools import runner as command_runner


def test_instructions_json_examples_are_argv_only() -> None:
    assert '"args":' not in INSTRUCTIONS
    assert '"paths":' not in INSTRUCTIONS


def test_instructions_describe_fail_closed_large_output_behavior() -> None:
    assert "fail closed" in INSTRUCTIONS
    assert "no partial output returned" in INSTRUCTIONS
    assert "rg --max-count" in INSTRUCTIONS
    assert "wc -l" in INSTRUCTIONS
    assert "head" in INSTRUCTIONS
    assert "tail" in INSTRUCTIONS
    assert "cp" in INSTRUCTIONS
    assert "mv" in INSTRUCTIONS
    assert "no-clobber" not in INSTRUCTIONS


def _allow_all() -> list[PermissionRule]:
    return [
        PermissionRule(
            pattern="**",
            operations={Operation.READ, Operation.CREATE, Operation.DELETE},
        )
    ]


def _tools(tmp_path: Path):
    return get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=_allow_all(),
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
    )


def _invoke_payload(input_cmd: RunFileCommands) -> dict[str, object]:
    return {
        "action": "run_file_command",
        "rationale": "prompt example",
        "chain": input_cmd.chain,
        "file_commands": [cmd.model_dump() for cmd in input_cmd.file_commands],
    }


def _run_chain(
    tmp_path: Path, input_cmd: RunFileCommands
) -> tuple[Stop, list[Message]]:
    endpoint = MockLLMEndpoint(
        responses=[
            _invoke_payload(input_cmd),
            {"action": "stop", "rationale": "done", "value": "ok"},
        ]
    )
    agent = Agent(
        name="prompt_examples_agent",
        tools=[*_tools(tmp_path), stop],
        system_prompt="x",
        agent_endpoint=endpoint,
        initial_messages=None,
    )
    output, messages = agent.invoke()
    assert isinstance(output, Stop)
    return output, messages


def _last_execute_value(messages: list[Message]) -> str:
    for message in reversed(messages):
        if message.role != Role.USER:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if payload.get("caller") == "execute_file_command":
            value = payload.get("value")
            if isinstance(value, str):
                return value
    raise AssertionError("no execute_file_command payload")


def _caller_payloads(messages: list[Message], caller: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for message in messages:
        if message.role != Role.USER:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("caller") == caller:
            payloads.append(payload)
    return payloads


def _abs(base: Path, rel: str) -> str:
    return str((base / rel).resolve())


def _install_subprocess_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    # Keep argv tests independent of optional host executables such as ripgrep.
    monkeypatch.setattr(
        ExecutableDependency,
        "require",
        lambda dependency: Path("/test-bin") / dependency.executable,
    )

    def fake_run(
        argv: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: float,
        input: str | None = None,
    ) -> CompletedProcess[str]:
        calls.append({"argv": list(argv), "cwd": Path(cwd), "stdin": input})
        if Path(argv[0]).name == "diff":
            return CompletedProcess(argv, 1, "1c1\n< a\n---\n> b\n", "")
        return CompletedProcess(argv, 0, f"ok {' '.join(argv)}\n", "")

    monkeypatch.setattr(command_runner.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def prompt_workspace(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(
        'def main():\n    print("sample")\n\n# TODO: refine\ndef helper():\n    pass\n'
    )
    (tmp_path / "big_file.py").write_text("".join(f"line {i}\n" for i in range(1, 501)))
    (tmp_path / "file.txt").write_text("alpha\nbeta\npattern line\n")
    (tmp_path / "old.py").write_text("a\n")
    (tmp_path / "new.py").write_text("b\n")
    (tmp_path / "sample").mkdir()
    (tmp_path / "site-packages").mkdir()
    deep = (
        tmp_path
        / "sample_solutions"
        / "game_player"
        / "game_player_data"
        / "conversations"
    )
    deep.mkdir(parents=True)
    (deep / "log.txt").write_text("needle here\n")
    (src / "sample.py").write_text("def foo():\n    pass\n")
    return tmp_path


def test_prompt_help_returns_cli_help(prompt_workspace: Path) -> None:
    cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="help", argv=[])],  # type: ignore
    )
    result = _tools(prompt_workspace)[0](input=cmd, messages=[])
    assert isinstance(result, Help)
    assert "Available CLI commands" in result.message


def test_pipe_passes_previous_stdout_to_next_stdin(
    prompt_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_subprocess_spy(monkeypatch)
    cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="cat", argv=["file.txt"]),
            RunFileCommand(command="grep", argv=["pattern"]),
        ],
    )
    _run_chain(prompt_workspace, cmd)
    assert len(calls) == 2
    cat = str(ExecutableDependency("cat").require())
    grep = str(ExecutableDependency("grep").require())
    assert calls[0]["argv"] == [cat, _abs(prompt_workspace, "file.txt")]
    assert calls[1]["argv"] == [grep, "pattern"]
    assert calls[1]["stdin"] == f"ok {cat} {_abs(prompt_workspace, 'file.txt')}\n"


@pytest.mark.parametrize(
    ("cmd", "expected_argvs"),
    [
        (
            RunFileCommands(
                chain="and",
                file_commands=[
                    RunFileCommand(command="wc", argv=["-l", "big_file.py"]),
                    RunFileCommand(command="head", argv=["-n", "80", "big_file.py"]),
                    RunFileCommand(command="tail", argv=["-n", "80", "big_file.py"]),
                ],
            ),
            [
                ["wc", "-l", "{BASE}/big_file.py"],
                ["head", "-n", "80", "{BASE}/big_file.py"],
                ["tail", "-n", "80", "{BASE}/big_file.py"],
            ],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="tail", argv=["-n", "+400", "big_file.py"])
                ],
            ),
            [["tail", "-n", "+400", "{BASE}/big_file.py"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="head", argv=["-n", "520", "big_file.py"]),
                    RunFileCommand(command="tail", argv=["-n", "80"]),
                ],
            ),
            [["head", "-n", "520", "{BASE}/big_file.py"], ["tail", "-n", "80"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(
                        command="rg", argv=["-n", "sample|site-packages", "."]
                    )
                ],
            ),
            [["rg", "-n", "sample|site-packages", "{BASE}"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(
                        command="rg", argv=["-n", "-i", "todo|fixme|bug", "src/"]
                    )
                ],
            ),
            [["rg", "-n", "-i", "todo|fixme|bug", "{BASE}/src"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(
                        command="rg",
                        argv=["-n", r"^def\s+[A-Za-z_][A-Za-z0-9_]*\(", "src/"],
                    )
                ],
            ),
            [["rg", "-n", r"^def\s+[A-Za-z_][A-Za-z0-9_]*\(", "{BASE}/src"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(
                        command="rg",
                        argv=[
                            "-n",
                            "needle",
                            "sample_solutions/game_player/game_player_data/conversations/",
                        ],
                    )
                ],
            ),
            [
                [
                    "rg",
                    "-n",
                    "needle",
                    "{BASE}/sample_solutions/game_player/game_player_data/conversations",
                ]
            ],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="rg", argv=["-uu", "-n", "needle", "."])
                ],
            ),
            [["rg", "-uu", "-n", "needle", "{BASE}"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="rg", argv=["-n", "def main", "src/"])
                ],
            ),
            [["rg", "-n", "def main", "{BASE}/src"]],
        ),
        (
            RunFileCommands(
                chain="and",
                file_commands=[
                    RunFileCommand(
                        command="find", argv=[".", "-type", "d", "-name", "sample"]
                    ),
                    RunFileCommand(
                        command="find",
                        argv=[".", "-type", "d", "-name", "site-packages"],
                    ),
                ],
            ),
            [
                ["find", "{BASE}", "-type", "d", "-name", "sample"],
                ["find", "{BASE}", "-type", "d", "-name", "site-packages"],
            ],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="find", argv=[".", "-name", "*.py"])
                ],
            ),
            [["find", "{BASE}", "-name", "*.py"]],
        ),
        (
            RunFileCommands(
                chain="and",
                file_commands=[
                    RunFileCommand(command="find", argv=[".", "-name", "*.py"]),
                    RunFileCommand(command="find", argv=[".", "-type", "d"]),
                ],
            ),
            [["find", "{BASE}", "-name", "*.py"], ["find", "{BASE}", "-type", "d"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[RunFileCommand(command="cat", argv=["src/main.py"])],
            ),
            [["cat", "{BASE}/src/main.py"]],
        ),
        (
            RunFileCommands(
                chain="and",
                file_commands=[
                    RunFileCommand(command="mkdir", argv=["sub"]),
                    RunFileCommand(command="touch", argv=["sub/file"]),
                ],
            ),
            [["mkdir", "{BASE}/sub"], ["touch", "{BASE}/sub/file"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(
                        command="tee", argv=["sub/file.txt"], stdin="line 1\nline 2\n"
                    )
                ],
            ),
            [["tee", "{BASE}/sub/file.txt"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="grep", argv=["-n", "-R", "TODO", "src/"])
                ],
            ),
            [["grep", "-n", "-R", "TODO", "{BASE}/src"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="diff", argv=["old.py", "new.py"])
                ],
            ),
            [["diff", "{BASE}/old.py", "{BASE}/new.py"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="cp", argv=["file.txt", "copy.txt"])
                ],
            ),
            [["cp", "{BASE}/file.txt", "{BASE}/copy.txt"]],
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="cp", argv=["-r", "src", "src-copy"])
                ],
            ),
            [["cp", "-r", "{BASE}/src", "{BASE}/src-copy"]],
        ),
    ],
)
def test_prompt_examples_pass_expected_argv_to_subprocess(
    prompt_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    cmd: RunFileCommands,
    expected_argvs: list[list[str]],
) -> None:
    calls = _install_subprocess_spy(monkeypatch)
    _, messages = _run_chain(prompt_workspace, cmd)
    _ = _last_execute_value(messages)

    base = str(prompt_workspace.resolve())
    realized = [
        [tok.replace("{BASE}", base) for tok in argv] for argv in expected_argvs
    ]
    for argv in realized:
        argv[0] = str(ExecutableDependency(argv[0]).require())
    actual = [call["argv"] for call in calls]
    assert actual == realized


def test_prompt_examples_real_subprocess_output_semantics(
    prompt_workspace: Path,
) -> None:
    checks = [
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[RunFileCommand(command="cat", argv=["src/main.py"])],
            ),
            "def main",
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="cat", argv=["file.txt"]),
                    RunFileCommand(command="grep", argv=["pattern"]),
                ],
            ),
            "pattern",
        ),
        (
            RunFileCommands(
                chain="pipe",
                file_commands=[
                    RunFileCommand(command="grep", argv=["-n", "-R", "TODO", "src/"])
                ],
            ),
            "TODO",
        ),
    ]
    for cmd, expected in checks:
        _, messages = _run_chain(prompt_workspace, cmd)
        assert expected in _last_execute_value(messages)


def test_prompt_diff_output_semantics(prompt_workspace: Path) -> None:
    cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="diff", argv=["old.py", "new.py"])],
    )
    _, messages = _run_chain(prompt_workspace, cmd)
    out = _last_execute_value(messages)
    assert "[error]" in out
    assert "1c1" in out


def test_negative_denies_relative_path_outside_allowed_scope(
    prompt_workspace: Path,
) -> None:
    tools = get_run_file_command(
        base=prompt_workspace,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="src/**", operations={Operation.READ})],
        deny_rules=[],
        takes_precedence=ActionVerdict.deny,
    )
    cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["file.txt"])],
    )
    endpoint = MockLLMEndpoint(
        responses=[
            _invoke_payload(cmd),
            {"action": "stop", "rationale": "done", "value": "ok"},
        ]
    )
    agent = Agent(
        name="negative_scope_agent",
        tools=[*tools, stop],
        system_prompt="x",
        agent_endpoint=endpoint,
        initial_messages=None,
    )
    _output, messages = agent.invoke()
    guard_payloads = _caller_payloads(messages, "operation_guard")
    exec_payloads = _caller_payloads(messages, "execute_file_command")
    assert guard_payloads
    assert not exec_payloads
    assert "denied" in str(guard_payloads[-1].get("message", "")).lower()


def test_negative_denies_absolute_path_outside_allowed_scope(
    prompt_workspace: Path,
) -> None:
    outside = prompt_workspace.parent / "outside_example.txt"
    outside.write_text("x")
    tools = get_run_file_command(
        base=prompt_workspace,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="src/**", operations={Operation.READ})],
        deny_rules=[],
        takes_precedence=ActionVerdict.deny,
    )
    cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=[str(outside)])],
    )
    endpoint = MockLLMEndpoint(
        responses=[
            _invoke_payload(cmd),
            {"action": "stop", "rationale": "done", "value": "ok"},
        ]
    )
    agent = Agent(
        name="negative_abs_scope_agent",
        tools=[*tools, stop],
        system_prompt="x",
        agent_endpoint=endpoint,
        initial_messages=None,
    )
    _output, messages = agent.invoke()
    guard_payloads = _caller_payloads(messages, "operation_guard")
    exec_payloads = _caller_payloads(messages, "execute_file_command")
    assert guard_payloads
    assert not exec_payloads
    assert "denied" in str(guard_payloads[-1].get("message", "")).lower()


def test_negative_denies_write_when_rules_are_read_only(prompt_workspace: Path) -> None:
    tools = get_run_file_command(
        base=prompt_workspace,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="src/**", operations={Operation.READ})],
        deny_rules=[],
        takes_precedence=ActionVerdict.deny,
    )
    cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="touch", argv=["src/new_file.py"])],
    )
    endpoint = MockLLMEndpoint(
        responses=[
            _invoke_payload(cmd),
            {"action": "stop", "rationale": "done", "value": "ok"},
        ]
    )
    agent = Agent(
        name="negative_write_agent",
        tools=[*tools, stop],
        system_prompt="x",
        agent_endpoint=endpoint,
        initial_messages=None,
    )
    _output, messages = agent.invoke()
    guard_payloads = _caller_payloads(messages, "operation_guard")
    exec_payloads = _caller_payloads(messages, "execute_file_command")
    assert guard_payloads
    assert not exec_payloads
    assert "denied" in str(guard_payloads[-1].get("message", "")).lower()
