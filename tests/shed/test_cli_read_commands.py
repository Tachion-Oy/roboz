"""Guarded readers use native output after explicit file permission checks."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from roboz import Agent, runtime
from roboz.llm import MockLLMEndpoint
from roboz.models import Role, Str
from roboz.runtime import EventPipe
from roboz.shed.models import ActionVerdict, GuardStatus, Operation, PermissionRule
from roboz.shed.tools.cli_commands import (
    FileCommand,
    Token,
    get_run_file_command,
)
from roboz.shed.tools.cli_commands.command import resolve_command
from roboz.tools import stop


def _invoke(tools: list, tokens: list[Token]) -> list[dict]:
    agent = Agent(
        name="reader_test",
        system_prompt="Read the requested files.",
        tools=[*tools, stop],
        agent_endpoint=MockLLMEndpoint(
            responses=[
                {
                    "action": "run_file_command",
                    "rationale": "Read",
                    "value": tokens,
                },
                {"action": "stop", "rationale": "Done", "value": "ok"},
            ]
        ),
        initial_messages=None,
    )
    _, messages = agent.invoke()
    return [
        json.loads(message.content) for message in messages if message.role == Role.USER
    ]


def _result(responses: list[dict]) -> str:
    return next(
        response["value"]
        for response in reversed(responses)
        if response.get("caller") == "execute_file_command"
    )


@pytest.mark.parametrize(
    ("command", "options"),
    [
        ("pwd", []),
        ("pwd", ["--physical"]),
        ("cat", []),
        ("cat", ["-n", "-b", "-s", "-E", "-T"]),
        (
            "cat",
            [
                "--number",
                "--number-nonblank",
                "--squeeze-blank",
                "--show-ends",
                "--show-tabs",
            ],
        ),
        ("head", []),
        ("head", ["-n", "2", "-q"]),
        ("head", ["--bytes", "5", "--verbose"]),
        ("head", ["--lines", "0", "--silent"]),
        ("tail", []),
        ("tail", ["--lines", "2", "--quiet"]),
        ("tail", ["-c", "5", "-v"]),
        ("tail", ["-n", "0"]),
        ("wc", []),
        ("wc", ["-l", "-w", "-c", "-m", "-L"]),
        ("wc", ["--lines", "--words", "--bytes", "--chars", "--max-line-length"]),
    ],
)
def test_reader_output_matches_native_command(tmp_path: Path, command, options) -> None:
    files = [tmp_path / "first", tmp_path / "second"]
    for index, path in enumerate(files):
        path.write_text(f"start {index}\n\n\n\ttext é\n" + "line\n" * 12)
    names = [] if command == "pwd" else [str(path) for path in files]
    native = subprocess.run(
        [command, *options, *names],
        cwd=tmp_path,
        input="",
        capture_output=True,
        text=True,
        check=True,
    )
    tokens = [(command, "CMD")]
    tokens.extend(
        (option, "ARG" if option.isdecimal() else "FLG") for option in options
    )
    tokens.extend((name, "PTH") for name in names)
    entry, guard, execute, _ = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    resolved = entry(FileCommand(value=tokens), [])
    assert resolved.original_input.failure is None
    assert [(item.operation, item.location) for item in resolved.items] == [
        (Operation.READ, path) for path in ([tmp_path] if command == "pwd" else files)
    ]
    result = execute(guard(resolved, []), [])
    assert isinstance(result, Str)
    assert result.value.startswith("Overall: success (exit 0)")
    if native.stdout:
        assert "\n" + native.stdout.rstrip("\n") + "\n--- end:" in result.value
    else:
        assert "Command completed with no output:" in result.value


@pytest.mark.parametrize(
    "tokens",
    [
        [("pwd", "CMD"), ("file", "PTH")],
        [("pwd", "CMD"), ("-L", "FLG")],
        [("cat", "CMD"), ("-nb", "FLG")],
        [("cat", "CMD"), ("-n", "FLG"), ("--number", "FLG")],
        [("cat", "CMD"), ("file", "ARG")],
        [("cat", "CMD"), ("file", "PTH"), ("-n", "FLG")],
        [("cat", "CMD"), ("--", "FLG"), ("-n", "FLG")],
        [("head", "CMD"), ("--lines=2", "FLG")],
        [("head", "CMD"), ("-n", "FLG")],
        [("head", "CMD"), ("-n", "FLG"), ("2", "PTH")],
        [("head", "CMD"), ("-n", "FLG"), ("-", "ARG")],
        [("head", "CMD"), ("-n", "FLG"), ("-2", "ARG")],
        [("head", "CMD"), ("-c", "FLG"), ("1K", "ARG")],
        [("head", "CMD"), ("2", "ARG")],
        [("head", "CMD"), ("-n", "FLG"), ("2", "ARG"), ("-c", "FLG"), ("2", "ARG")],
        [("head", "CMD"), ("-q", "FLG"), ("-v", "FLG")],
        [("tail", "CMD"), ("-n", "FLG"), ("+2", "ARG")],
        [("tail", "CMD"), ("-n", "FLG"), ("٢", "ARG")],
        [("tail", "CMD"), ("-f", "FLG")],
        [("tail", "CMD"), ("--quiet", "FLG"), ("--silent", "FLG")],
        [("wc", "CMD"), ("--files0-from", "FLG"), ("list", "PTH")],
    ],
)
def test_invalid_reader_arguments_fail_before_execution(tmp_path: Path, tokens) -> None:
    resolved = resolve_command(tmp_path)(FileCommand(value=tokens), [])
    assert resolved.original_input.failure
    assert resolved.original_input.ready is None
    assert resolved.items == []


@pytest.mark.parametrize(
    "kind", ["directory", "symlink", "parent_symlink", "hardlink", "fifo", "unmatched"]
)
def test_unsafe_reader_paths_reject_the_entire_command(
    tmp_path: Path, monkeypatch, kind
) -> None:
    (tmp_path / "safe.txt").write_text("safe")
    bad = tmp_path / "bad.txt"
    operand = "*.txt"
    if kind == "directory":
        bad.mkdir()
    elif kind == "symlink":
        bad.symlink_to(tmp_path / "safe.txt")
    elif kind == "parent_symlink":
        bad.symlink_to(tmp_path, target_is_directory=True)
        operand = "bad.txt/../safe.txt"
    elif kind == "hardlink":
        bad.hardlink_to(tmp_path / "safe.txt")
    elif kind == "fifo":
        os.mkfifo(bad)
    else:
        operand = "missing*.txt"

    def unexpected_run(*args, **kwargs):
        pytest.fail("Unsafe reader reached native execution")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    responses = _invoke(tools, [("cat", "CMD"), ("safe.txt", "PTH"), (operand, "PTH")])
    assert _result(responses).startswith("Overall: failure (exit 1)")


def test_reader_patterns_preserve_order_repeats_and_literal_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "src/nested").mkdir(parents=True)
    (tmp_path / "src/z.txt").write_text("z\n")
    (tmp_path / "src/nested/a.txt").write_text("a\n")
    (tmp_path / "src/.hidden.txt").write_text("hidden\n")
    unusual = "-report * space\\name\n.txt"
    (tmp_path / unusual).write_text("odd\n")
    entry, guard, execute, _ = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    resolved = entry(
        FileCommand(
            value=[
                ("cat", "CMD"),
                ("--", "FLG"),
                ("src/z.txt", "PTH"),
                ("src/**/*.txt", "PTH"),
                ("-report*.txt", "PTH"),
            ]
        ),
        [],
    )
    assert [item.location for item in resolved.items] == [
        tmp_path / "src/z.txt",
        tmp_path / "src/nested/a.txt",
        tmp_path / unusual,
    ]
    result = execute(guard(resolved, []), [])
    assert isinstance(result, Str)
    assert "\nz\na\nz\nodd\n--- end:" in result.value


@pytest.mark.parametrize("policy_denied", [False, True])
def test_read_approvals_are_deduplicated_and_follow_all_policy_checks(
    tmp_path: Path,
    monkeypatch,
    policy_denied: bool,
) -> None:
    (tmp_path / "a.txt").write_text("a\n")
    (tmp_path / "b.txt").write_text("b\n")
    prompts = []

    def approve(message, **kwargs):
        prompts.append(message)
        return "yes"

    monkeypatch.setattr(runtime, "interact_with_user", approve)
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule(pattern="*.txt", operations={Operation.READ})],
        deny_rules=[PermissionRule(pattern="b.txt", operations={Operation.READ})]
        if policy_denied
        else [],
        pipe=EventPipe(),
    )
    result = _result(
        _invoke(tools, [("cat", "CMD"), ("a.txt", "PTH"), ("*.txt", "PTH")])
    )
    if policy_denied:
        assert prompts == []
        assert result.startswith("Overall: failure")
        assert "\na\n" not in result
    else:
        assert len(prompts) == 2
        assert result.startswith("Overall: success")
        assert "\na\na\nb\n--- end:" in result


@pytest.mark.parametrize("command", ["cat", "head", "tail", "wc"])
def test_stdin_only_readers_need_no_file_permissions(tmp_path: Path, command) -> None:
    entry, guard, execute, _ = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.deny
    )
    resolved = entry(FileCommand(value=[(command, "CMD")]), [])
    assert resolved.items == []
    assert resolved.original_input.ready.stdin == ""
    guarded = guard(resolved, [])
    assert guarded.status == GuardStatus.ALLOWED
    result = execute(guarded, [])
    assert isinstance(result, Str)
    assert result.value.startswith("Overall: success (exit 0)")


@pytest.mark.parametrize(("command", "escaped"), [("head", r"\xc3"), ("tail", r"\xa9")])
@pytest.mark.parametrize("piped", [False, True])
def test_byte_readers_preserve_split_utf8_output(
    tmp_path: Path, command: str, escaped: str, piped: bool
) -> None:
    (tmp_path / "data").write_bytes("é".encode("utf-8"))
    tokens = [(command, "CMD"), ("-c", "FLG"), ("1", "ARG"), ("data", "PTH")]
    if piped:
        tokens.extend([("|", "CTL"), ("wc", "CMD"), ("-c", "FLG")])
    result = _result(
        _invoke(
            get_run_file_command(
                base=tmp_path, default_verdict=ActionVerdict.allow
            ),
            tokens,
        )
    )
    assert result.startswith("Overall: success (exit 0)")
    assert f"\n{'1' if piped else escaped}\n--- end:" in result


@pytest.mark.parametrize("content", [b"a\r\n", b"\xff\x00"])
def test_reader_pipeline_preserves_binary_and_crlf_bytes(
    tmp_path: Path, content: bytes
) -> None:
    (tmp_path / "data").write_bytes(content)
    result = _result(
        _invoke(
            get_run_file_command(
                base=tmp_path, default_verdict=ActionVerdict.allow
            ),
            [("cat", "CMD"), ("data", "PTH"), ("|", "CTL"), ("wc", "CMD"), ("-c", "FLG")],
        )
    )
    assert result.startswith("Overall: success (exit 0)")
    assert f"\n{len(content)}\n--- end:" in result


def test_reader_pipeline_distinguishes_literal_dash_from_stdin(tmp_path: Path) -> None:
    (tmp_path / "data").write_text("one\ntwo\nthree\n")
    (tmp_path / "-").write_text("literal\n")
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="*", operations={Operation.READ})],
    )
    responses = _invoke(
        tools,
        [
            ("cat", "CMD"),
            ("data", "PTH"),
            ("|", "CTL"),
            ("head", "CMD"),
            ("-n", "FLG"),
            ("2", "ARG"),
            ("|", "CTL"),
            ("wc", "CMD"),
            ("-l", "FLG"),
            (";", "CTL"),
            ("cat", "CMD"),
            ("data", "PTH"),
            ("|", "CTL"),
            ("cat", "CMD"),
            ("-", "PTH"),
            ("-", "ARG"),
        ],
    )
    result = _result(responses)
    assert result.startswith("Overall: success (exit 0)")
    assert "\n2\n--- end:" in result
    assert "\nliteral\none\ntwo\nthree\n--- end:" in result


@pytest.mark.parametrize("failure", ["invalid", "denied", "declined"])
def test_failed_read_does_not_reuse_payload_and_fallback_runs(
    tmp_path: Path,
    monkeypatch,
    failure,
) -> None:
    (tmp_path / "safe").write_text("safe\n")
    (tmp_path / "secret").write_text("secret\n")
    launched = []
    run = subprocess.run

    def record(argv, *, cwd, input, **kwargs):
        launched.append(argv[1:])
        return run(argv, cwd=cwd, input=input, **kwargs)

    monkeypatch.setattr(subprocess, "run", record)
    monkeypatch.setattr(runtime, "interact_with_user", lambda *args, **kwargs: "no")
    rule = PermissionRule(pattern="secret", operations={Operation.READ})
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        deny_rules=[rule] if failure == "denied" else [],
        ask_rules=[rule] if failure == "declined" else [],
        pipe=EventPipe(),
    )
    middle = [("cat", "CMD"), ("secret", "PTH")]
    if failure == "invalid":
        middle = [("cat", "CMD"), ("--unknown", "FLG")]
    result = _result(
        _invoke(
            tools,
            [
                ("cat", "CMD"),
                ("safe", "PTH"),
                (";", "CTL"),
                *middle,
                ("||", "CTL"),
                ("wc", "CMD"),
                ("-l", "FLG"),
            ],
        )
    )
    assert launched == [[str(tmp_path / "safe")], ["-l"]]
    assert result.startswith("Overall: success (exit 0)")
    assert "exit 1" in result and "\n0\n--- end:" in result


def test_read_after_transfer_resolves_new_files_lazily(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("new\n")
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    result = _result(
        _invoke(
            tools,
            [
                ("cp", "CMD"),
                ("source", "PTH"),
                ("copied.txt", "PTH"),
                ("&&", "CTL"),
                ("mv", "CMD"),
                ("copied.txt", "PTH"),
                ("moved.txt", "PTH"),
                ("&&", "CTL"),
                ("cat", "CMD"),
                ("*.txt", "PTH"),
                ("||", "CTL"),
                ("cat", "CMD"),
                ("unmatched*", "PTH"),
            ],
        )
    )
    assert result.startswith("Overall: success (exit 0)")
    assert "\nnew\n--- end:" in result
    assert not (tmp_path / "copied.txt").exists()
    assert (tmp_path / "moved.txt").read_text() == "new\n"


def test_pwd_requires_read_on_base(tmp_path: Path) -> None:
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.deny
    )
    assert _result(_invoke(tools, [("pwd", "CMD")])).startswith("Overall: failure")


def test_denied_pipe_supplies_empty_stdin_and_clears_it_for_next_list(
    tmp_path: Path,
) -> None:
    (tmp_path / "secret").write_text("secret\n")
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.deny
    )
    result = _result(
        _invoke(
            tools,
            [
                ("cat", "CMD"),
                ("secret", "PTH"),
                ("|", "CTL"),
                ("wc", "CMD"),
                ("-l", "FLG"),
                (";", "CTL"),
                ("wc", "CMD"),
                ("-l", "FLG"),
                ("-", "ARG"),
            ],
        )
    )
    assert result.startswith("Overall: success (exit 0)")
    assert "exit 1" in result
    assert "\n0\n--- end:" in result and "\n0 -\n--- end:" in result


def test_absolute_reader_paths_use_absolute_permission_rules(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside\n")
    tokens = [("cat", "CMD"), (str(outside), "PTH")]
    denied = get_run_file_command(
        base=base,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
    )
    assert _result(_invoke(denied, tokens)).startswith("Overall: failure")
    allowed = get_run_file_command(
        base=base,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern=str(outside), operations={Operation.READ})],
    )
    assert "\noutside\n--- end:" in _result(_invoke(allowed, tokens))
