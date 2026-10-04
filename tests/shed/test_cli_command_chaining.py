"""Inline CTL operators preserve a guarded loop and Bash list semantics."""

import asyncio
import errno
import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from roboz import Agent, runtime
from roboz.dependencies import ExecutableDependency
from roboz.llm import MockLLMEndpoint
from roboz.models import Role
from roboz.models.truncation import LIGHT_MAX_CHARS
from roboz.runtime import EventPipe
from roboz.shed.models import ActionVerdict, Operation, PermissionRule
from roboz.shed.tools.cli_commands import (
    FileCommand,
    Token,
    get_run_file_command,
)
from roboz.shed.tools.cli_commands.constants import MAX_COMMAND_OUTPUT_CHARS
from roboz.tools import stop


def _copy(source: str, destination: str) -> list[Token]:
    return [("cp", "CMD"), (source, "PTH"), (destination, "PTH")]


def _invoke(tools: list, tokens: list[Token]) -> list[dict]:
    agent = Agent(
        name="command_chain_test",
        system_prompt="Transfer files.",
        tools=[*tools, stop],
        agent_endpoint=MockLLMEndpoint(
            responses=[
                {
                    "action": "run_file_command",
                    "rationale": "Transfer",
                    "value": tokens,
                },
                {"action": "stop", "rationale": "Done", "value": "ok"},
            ]
        ),
        initial_messages=None,
    )
    _, messages = agent.invoke()
    return [json.loads(m.content) for m in messages if m.role == Role.USER]


def _result(responses: list[dict]) -> str:
    return next(
        r["value"]
        for r in reversed(responses)
        if r.get("caller") == "execute_file_command"
    )


def test_public_contract_accepts_only_tokens_and_rejects_progress() -> None:
    schema = FileCommand.model_json_schema()
    assert "sequence" not in FileCommand.model_fields
    assert "sequence" not in FileCommand(value=_copy("a", "b")).model_dump()
    assert "sequence" not in schema["properties"]
    assert "CommandSequence" not in schema.get("$defs", {})
    assert schema["$defs"]["CommandName"]["enum"] == [
        "cp", "mv", "pwd", "cat", "head", "tail", "wc", "tee", "touch", "mkdir", "grep", "rg", "ls", "find",
        "diff", "gio", "rm",
    ]
    assert schema["$defs"]["ControlOperator"]["enum"] == ["&&", "||", ";", "|"]
    with pytest.raises(ValidationError):
        FileCommand.model_validate({"value": _copy("a", "b"), "chain": "&&"})
    with pytest.raises(ValidationError):
        FileCommand.model_validate({"value": _copy("a", "b"), "sequence": None})


@pytest.mark.parametrize(
    "tokens",
    [
        [("&&", "CTL"), *_copy("a", "b")],
        [*_copy("a", "b"), ("||", "CTL")],
        [*_copy("a", "b"), (";", "CTL"), ("|", "CTL"), *_copy("a", "c")],
        [*_copy("a", "b"), ("&", "CTL"), *_copy("a", "c")],
        [*_copy("a", "b"), ("&&", "CTL"), ("a", "PTH")],
        [*_copy("a", "b"), *_copy("a", "c")],
    ],
)
def test_malformed_sequence_rejects_before_first_transfer(
    tmp_path: Path, tokens
) -> None:
    with pytest.raises(ValidationError):
        FileCommand(value=tokens)
    (tmp_path / "a").write_text("data")
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    responses = _invoke(tools, tokens)
    assert not any(r.get("caller") == "guard_file_command" for r in responses)
    assert not any(r.get("caller") == "execute_file_command" for r in responses)
    assert not (tmp_path / "b").exists()


@pytest.mark.parametrize("failure", ["denial", "preparation"])
def test_failure_labels_use_prepared_command_or_command_name(
    tmp_path: Path, failure: str
) -> None:
    source = tmp_path / "source file"
    source.write_text("data")
    tokens = [
        ("head", "CMD"), ("-n", "FLG"),
        ("2" if failure == "denial" else "-2", "ARG"), (source.name, "PTH"),
    ]
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.deny
    )
    result = _result(_invoke(tools, tokens))
    label = shlex.join(["head", "-n", "2", str(source)]) if failure == "denial" else "head"
    assert result.startswith("Overall: failure (exit 1)")
    assert f"--- begin: {label} ---\n" in result
    assert f"--- end: {label} ---" in result


def test_one_continuation_reuses_guard_and_resolves_after_prior_writes(
    tmp_path: Path,
) -> None:
    (tmp_path / "source").write_text("data")
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    entry, guard, execute, continuation = tools
    assert entry.chained_to is None
    assert guard.chained_to == [entry, continuation]
    assert execute.chained_to == [guard]
    assert continuation.chained_to == [execute]
    tokens = [
        *_copy("source", "created.txt"),
        ("&&", "CTL"),
        ("mv", "CMD"),
        ("*.txt", "PTH"),
        ("renamed", "PTH"),
        ("&&", "CTL"),
        *_copy("renamed", "backup"),
    ]
    responses = _invoke(tools, tokens)
    assert [r["caller"] for r in responses] == [
        "run_file_command",
        "guard_file_command",
        "execute_file_command",
        "continue_file_command",
        "guard_file_command",
        "execute_file_command",
        "continue_file_command",
        "guard_file_command",
        "execute_file_command",
        "stop",
    ]
    assert _result(responses).startswith("Overall: success (exit 0)")
    assert (tmp_path / "backup").read_text() == "data"
    assert not (tmp_path / "created.txt").exists()
    for response in responses:
        if "original_input" in response:
            assert response["original_input"]["request"]["value"] == [
                list(t) for t in tokens
            ]
    # Reusing the same tools starts a fresh sequence.
    assert _result(_invoke(tools, _copy("source", "fresh"))).startswith(
        "Overall: success"
    )
    assert (tmp_path / "fresh").read_text() == "data"


@pytest.mark.parametrize("name", ["&&", "||", ";", "|"])
def test_operator_text_in_pth_is_literal(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text("data")
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    assert _result(_invoke(tools, _copy(name, "target"))).startswith("Overall: success")
    assert (tmp_path / "target").read_text() == "data"


@pytest.mark.skipif(not shutil.which("bash"), reason="Requires Bash")
@pytest.mark.parametrize("operator", ["&&", "||"])
def test_recursive_partial_move_status_controls_continuation(
    tmp_path: Path, operator: str
) -> None:
    native, guarded = tmp_path / "native", tmp_path / "guarded"
    for root in (native, guarded):
        (root / "src/nested").mkdir(parents=True)
        (root / "src/file").write_text("data")
        (root / "out").mkdir()
    expected = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            shlex.join(
                [
                    "mv",
                    f"{native}/src/",
                    f"{native}/src/file",
                    f"{native}/src/nested",
                    "out",
                ]
            )
            + f" {operator} "
            + shlex.join(["cp", "out/src/file", "continued"]),
        ],
        cwd=native,
        capture_output=True,
    )
    tokens: list[Token] = [
        ("mv", "CMD"),
        ("src/", "PTH"),
        ("src/**", "PTH"),
        ("out", "PTH"),
        (operator, "CTL"),
        *_copy("out/src/file", "continued"),
    ]
    responses = _invoke(
        get_run_file_command(base=guarded, default_verdict=ActionVerdict.allow),
        tokens,
    )
    status = "success" if expected.returncode == 0 else "failure"
    result = _result(responses)
    assert result.startswith(f"Overall: {status} (exit {expected.returncode})")
    assert "Command failed (exit 1)" in result
    assert not (guarded / "src").exists()
    assert (guarded / "out/src/file").read_text() == "data"
    assert (guarded / "continued").exists() is (operator == "||")
    contents = [
        {
            str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*")
        }
        for root in (native, guarded)
    ]
    assert contents[0] == contents[1]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize(
    ("sources", "operators"),
    [
        (["missing", "source", "source"], ["&&", "||"]),
        (["source", "missing", "source"], ["||", "&&"]),
        (["missing", "source", "source", "source"], ["&&", "|", ";"]),
        (
            ["missing", "source", "source", "source", "source"],
            ["&&", "|", "|", "||"],
        ),
        (["missing", "source", "source"], ["|", "&&"]),
        (["source", "missing", "source"], ["||", "|"]),
        (["source", "missing", "source", "missing"], ["&&", "||", ";"]),
        (["source", "missing", "source", "source"], ["|", "&&", "||"]),
    ],
)
def test_mixed_lists_match_shell(
    tmp_path: Path, sources: list[str], operators: list[str], shell: str
) -> None:
    if not shutil.which(shell):
        pytest.skip(f"Requires {shell}")
    native, guarded = tmp_path / "native", tmp_path / "guarded"
    for root in [native, guarded]:
        root.mkdir()
        (root / "source").write_text("data")
    tokens: list[Token] = []
    script: list[str] = []
    for i, source in enumerate(sources):
        if i:
            tokens.append((operators[i - 1], "CTL"))
            script.append(operators[i - 1])
        tokens.extend(_copy(source, f"out{i}"))
        script.append(shlex.join(["cp", source, f"out{i}"]))
    expected = subprocess.run(
        [shell, "-f", "-c", " ".join(script)],
        cwd=native,
        capture_output=True,
        check=False,
    )
    tools = get_run_file_command(
        base=guarded, default_verdict=ActionVerdict.allow
    )
    result = _result(_invoke(tools, tokens))
    status = "success" if expected.returncode == 0 else "failure"
    assert result.startswith(f"Overall: {status} (exit {expected.returncode})")
    assert {p.name: p.read_text() for p in guarded.iterdir()} == {
        p.name: p.read_text() for p in native.iterdir()
    }


def test_skipped_pipeline_never_scans_sources_or_prompts(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "source").write_text("data")
    (tmp_path / "blocked").mkdir()
    original_iterdir = Path.iterdir

    def iterdir(path: Path):
        assert path != tmp_path / "blocked", "Skipped pattern was expanded"
        return original_iterdir(path)

    def unexpected_prompt(*args, **kwargs):
        pytest.fail("Skipped command requested approval")

    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(runtime, "interact_with_user", unexpected_prompt)
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule(pattern="skip*", operations={Operation.CREATE})],
        pipe=EventPipe(),
    )
    tokens = [
        *_copy("source", "first"),
        ("||", "CTL"),
        *_copy("blocked/*", "skip1"),
        ("|", "CTL"),
        *_copy("source", "skip2"),
        (";", "CTL"),
        *_copy("first", "last"),
    ]
    responses = _invoke(tools, tokens)
    assert _result(responses).startswith("Overall: success")
    assert sum(r["caller"] == "guard_file_command" for r in responses) == 2
    assert (tmp_path / "last").read_text() == "data"


@pytest.mark.parametrize("denial", ["policy", "approval"])
def test_denied_step_never_launches_and_fallback_is_guarded(
    tmp_path: Path, monkeypatch, denial: str
) -> None:
    (tmp_path / "source").write_text("data")
    (tmp_path / "protected").write_text("old")
    prompts: list[str] = []
    launched: list[str] = []
    original_run = subprocess.run

    def approve(message: str, **kwargs):
        prompts.append(message)
        return "no" if "protected" in message else "yes"

    def run(argv, *, cwd, input, **kwargs):
        launched.append(Path(argv[-1]).name)
        return original_run(argv, cwd=cwd, input=input, **kwargs)

    monkeypatch.setattr(runtime, "interact_with_user", approve)
    monkeypatch.setattr(subprocess, "run", run)
    protected = PermissionRule(pattern="protected", operations={Operation.DELETE})
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        deny_rules=[protected] if denial == "policy" else [],
        ask_rules=[
            protected,
            PermissionRule(pattern="fallback", operations={Operation.CREATE}),
        ],
        pipe=EventPipe(),
    )
    responses = _invoke(
        tools,
        [
            *_copy("source", "protected"),
            ("||", "CTL"),
            *_copy("source", "fallback"),
            (";", "CTL"),
            *_copy("source", "protected"),
        ],
    )
    assert _result(responses).startswith("Overall: failure (exit 1)")
    assert launched == ["fallback"]
    assert (tmp_path / "protected").read_text() == "old"
    assert (tmp_path / "fallback").read_text() == "data"
    assert len(prompts) == (1 if denial == "policy" else 3)


@pytest.mark.parametrize("stdout", [b"", b"  data\n\n"])
def test_pipeline_preserves_exact_stdout_and_uses_last_status(
    tmp_path: Path, monkeypatch, stdout: bytes
) -> None:
    (tmp_path / "source").write_text("data")
    inputs: list[bytes] = []

    def run(argv, *, cwd, input, **kwargs):
        inputs.append(input)
        return subprocess.CompletedProcess(
            argv, 7 if len(inputs) == 1 else 0, stdout, b"diagnostic"
        )

    monkeypatch.setattr(subprocess, "run", run)
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    result = _result(
        _invoke(
            tools,
            [
                *_copy("source", "one"),
                ("|", "CTL"),
                *_copy("source", "two"),
                ("&&", "CTL"),
                *_copy("source", "three"),
            ],
        )
    )
    assert inputs == [b"", stdout, b""]
    assert result.startswith("Overall: success (exit 0)")
    assert "exit 7" in result and "stderr:\ndiagnostic" in result


@pytest.mark.parametrize("failure", ["preparation", "denial", "timeout"])
def test_failed_pipeline_stage_supplies_empty_input_to_next_guarded_stage(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    (tmp_path / "source").write_text("data")
    inputs: list[bytes] = []

    def run(argv, *, cwd, input, **kwargs):
        inputs.append(input)
        if Path(argv[-1]).name == "first":
            raise subprocess.TimeoutExpired(argv, 1, output="discard partial")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        deny_rules=[PermissionRule(pattern="first", operations={Operation.CREATE})]
        if failure == "denial"
        else [],
    )
    source = "missing" if failure == "preparation" else "source"
    responses = _invoke(
        tools,
        [
            *_copy(source, "first"),
            ("|", "CTL"),
            *_copy("source", "last"),
        ],
    )
    assert inputs == ([b"", b""] if failure == "timeout" else [b""])
    assert sum(r["caller"] == "guard_file_command" for r in responses) == 2
    result = _result(responses)
    assert result.startswith("Overall: success (exit 0)")
    assert "discard partial" not in result


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        ("lookup", 127),
        ("disappeared", 127),
        ("permission", 126),
        ("format", 126),
        ("timeout", 124),
        ("signal", 143),
    ],
)
def test_ordinary_process_failures_allow_fallback(
    tmp_path: Path, monkeypatch, failure: str, status: int
) -> None:
    (tmp_path / "source").write_text("data")
    launched: list[str] = []
    original_require = ExecutableDependency.require

    def require(binding):
        if failure == "lookup" and binding.executable == "cp":
            raise FileNotFoundError("Missing cp")
        return original_require(binding)

    def run(argv, *, cwd, input, **kwargs):
        launched.append(Path(argv[0]).name)
        if launched[-1] == "cp":
            if failure == "disappeared":
                raise FileNotFoundError(2, "Missing executable", argv[0])
            if failure == "permission":
                raise PermissionError("Cannot launch executable")
            if failure == "format":
                raise OSError(errno.ENOEXEC, "Invalid executable format", argv[0])
            if failure == "timeout":
                raise subprocess.TimeoutExpired(
                    argv, 1, output="discard partial", stderr="discard error"
                )
            return subprocess.CompletedProcess(argv, -15, b"", b"")
        return subprocess.CompletedProcess(argv, 0, b"fallback", b"")

    monkeypatch.setattr(ExecutableDependency, "require", require)
    monkeypatch.setattr(subprocess, "run", run)
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    result = _result(
        _invoke(
            tools,
            [
                *_copy("source", "first"),
                ("||", "CTL"),
                ("mv", "CMD"),
                ("source", "PTH"),
                ("fallback", "PTH"),
            ],
        )
    )
    assert launched == (["mv"] if failure == "lookup" else ["cp", "mv"])
    assert result.startswith("Overall: success (exit 0)")
    assert f"exit {status}" in result
    assert "discard" not in result


@pytest.mark.parametrize("failure", ["error", "oversized", "cancelled"])
def test_terminal_failures_never_reach_fallback(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    (tmp_path / "source").write_text("data")
    launched: list[list[str]] = []

    def run(argv, *, cwd, input, **kwargs):
        launched.append(argv)
        if failure == "error":
            raise OSError("Unexpected process failure")
        if failure == "cancelled":
            raise asyncio.CancelledError()
        return subprocess.CompletedProcess(
            argv, 0, b"x" * (MAX_COMMAND_OUTPUT_CHARS + 1), b""
        )

    monkeypatch.setattr(subprocess, "run", run)
    tools = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    tokens = [*_copy("source", "first"), ("||", "CTL"), *_copy("source", "fallback")]
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            _invoke(tools, tokens)
    else:
        assert _result(_invoke(tools, tokens)).startswith("Overall: failure")
    assert len(launched) == 1


@pytest.mark.parametrize("terminal_error", [False, True])
def test_sequence_history_is_bounded_and_keeps_latest_diagnostics(
    tmp_path: Path, monkeypatch, terminal_error: bool
) -> None:
    (tmp_path / "source").write_text("data")
    calls = []

    def run(argv, *, cwd, input, **kwargs):
        step = len(calls)
        calls.append(argv)
        if terminal_error and step == 3:
            raise OSError("latest execution error")
        stdout = f"start-{step}\n" + "x" * (LIGHT_MAX_CHARS // 2) + f"\nend-{step}"
        return subprocess.CompletedProcess(
            argv, 7 if step == 3 else 0, stdout.encode(), f"diagnostic-{step}".encode()
        )

    monkeypatch.setattr(subprocess, "run", run)
    tokens: list[Token] = []
    for step in range(4):
        if tokens:
            tokens.append((";", "CTL"))
        tokens.extend(_copy("source", f"out{step}"))
    responses = _invoke(
        get_run_file_command(base=tmp_path, default_verdict=ActionVerdict.allow),
        tokens,
    )
    histories = [r["accumulated_output"] for r in responses if "remaining" in r]
    assert len(calls) == 4 and len(histories) == 3
    assert all(len(history) <= LIGHT_MAX_CHARS for history in histories)
    result = _result(responses)
    assert len(result) <= LIGHT_MAX_CHARS
    assert result.count("earlier command output omitted") == 1
    assert "start-0" not in result
    if terminal_error:
        assert result.startswith("Overall: failure (execution error)")
        assert "latest execution error" in result
    else:
        assert result.startswith("Overall: failure (exit 7)")
        assert "end-3" in result and "stderr:\ndiagnostic-3" in result


def test_bounding_history_preserves_full_piped_stdout(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "source").write_text("data")
    stdout = "p" * (LIGHT_MAX_CHARS + 1)
    inputs = []

    def run(argv, *, cwd, input, **kwargs):
        inputs.append(input)
        if len(inputs) == 1:
            return subprocess.CompletedProcess(
                argv, 0, stdout.encode(), b"s" * (LIGHT_MAX_CHARS + 1)
            )
        return subprocess.CompletedProcess(argv, 0, b"latest pipeline output", b"")

    monkeypatch.setattr(subprocess, "run", run)
    responses = _invoke(
        get_run_file_command(base=tmp_path, default_verdict=ActionVerdict.allow),
        [*_copy("source", "first"), ("|", "CTL"), *_copy("source", "last")],
    )
    assert inputs == [b"", stdout.encode()]
    history = next(r["accumulated_output"] for r in responses if "remaining" in r)
    assert len(history) <= LIGHT_MAX_CHARS
    result = _result(responses)
    assert len(result) <= LIGHT_MAX_CHARS
    assert result.startswith("Overall: success (exit 0)")
    assert "earlier command output omitted" in result
    assert "latest pipeline output" in result
