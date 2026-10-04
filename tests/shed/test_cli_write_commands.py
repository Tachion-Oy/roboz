"""Guarded writers prepare every target before native execution."""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from roboz import Agent, runtime
from roboz.llm import MockLLMEndpoint
from roboz.models import Role, Str
from roboz.runtime import EventPipe
from roboz.shed.models import ActionVerdict, Operation, PermissionRule
from roboz.shed.tools.cli_commands import (
    FileCommand,
    get_run_file_command,
)
from roboz.shed.tools.cli_commands.command import resolve_command
from roboz.shed.tools.cli_commands.contracts import CommandExecution
from roboz.tools import stop


def _invoke(base: Path, tokens: list, **policy) -> str:
    tools = get_run_file_command(
        base=base,
        default_verdict=policy.pop("default_verdict", ActionVerdict.allow),
        **policy,
    )
    agent = Agent(
        name="writer_test",
        system_prompt="Write the requested files.",
        tools=[*tools, stop],
        agent_endpoint=MockLLMEndpoint(
            responses=[
                {
                    "action": "run_file_command",
                    "rationale": "Write",
                    "value": tokens,
                },
                {"action": "stop", "rationale": "Done", "value": "ok"},
            ]
        ),
        initial_messages=None,
    )
    _, messages = agent.invoke()
    responses = [
        json.loads(message.content) for message in messages if message.role == Role.USER
    ]
    return next(
        response["value"]
        for response in reversed(responses)
        if response.get("caller") == "execute_file_command"
    )


def _resolve(base: Path, tokens: list):
    return resolve_command(base)(FileCommand(value=tokens), [])


@pytest.mark.parametrize("flags", [[], ["-a"], ["--append"]])
def test_tee_creates_and_updates_multiple_files_with_exact_inline_text(
    tmp_path: Path, flags: list[str]
) -> None:
    content = "héllo 🌍\r\nsecond\x00line\n"
    (tmp_path / "existing").write_bytes(b"old\n")
    tokens = [
        ("tee", "CMD"),
        *((flag, "FLG") for flag in flags),
        (content, "ARG"),
        ("existing", "PTH"),
        ("new", "PTH"),
    ]
    resolved = _resolve(tmp_path, tokens)
    assert [(item.operation, item.location.name) for item in resolved.items] == [
        (Operation.CREATE, "existing"),
        (Operation.READ, "existing"),
        (Operation.DELETE, "existing"),
        (Operation.CREATE, "new"),
    ]
    ready = resolved.original_input.ready
    assert ready is not None and content not in ready.argv
    assert ready.stdin == content
    assert content not in ready.display_command
    result = _invoke(tmp_path, tokens)
    assert result.startswith("Overall: success (exit 0)")
    assert content.rstrip("\n") in result
    assert (tmp_path / "existing").read_bytes() == (
        (b"old\n" if flags else b"") + content.encode("utf-8")
    )
    assert (tmp_path / "new").read_bytes() == content.encode("utf-8")


@pytest.mark.parametrize("inline", [[], [("", "ARG")]])
def test_tee_without_input_truncates_instead_of_waiting(tmp_path: Path, inline) -> None:
    (tmp_path / "target").write_text("old")
    result = _invoke(tmp_path, [("tee", "CMD"), *inline, ("target", "PTH")])
    assert result.startswith("Overall: success (exit 0)")
    assert (tmp_path / "target").read_bytes() == b""


def test_stdout_only_tee_produces_utf8_without_file_permissions(tmp_path: Path) -> None:
    content = "héllo 🌍\r\n"
    result = _invoke(
        tmp_path,
        [("tee", "CMD"), (content, "ARG"), ("|", "CTL"), ("wc", "CMD"), ("-c", "FLG")],
        default_verdict=ActionVerdict.deny,
    )
    assert result.startswith("Overall: success (exit 0)")
    assert f"\n{len(content.encode('utf-8'))}\n--- end:" in result
    assert content not in result


@pytest.mark.parametrize("content", [b"", b"\xff\x00\r\n"])
def test_piped_bytes_override_inline_text_and_do_not_leak_to_later_steps(
    tmp_path: Path, content: bytes
) -> None:
    (tmp_path / "source").write_bytes(content)
    result = _invoke(
        tmp_path,
        [
            ("cat", "CMD"),
            ("source", "PTH"),
            ("|", "CTL"),
            ("tee", "CMD"),
            ("ignored", "ARG"),
            ("copy", "PTH"),
            (";", "CTL"),
            ("tee", "CMD"),
            ("later é", "ARG"),
            ("later", "PTH"),
        ],
    )
    assert result.startswith("Overall: success (exit 0)")
    assert (tmp_path / "copy").read_bytes() == content
    assert (tmp_path / "later").read_text() == "later é"
    assert "ignored" not in result


def test_serialized_pipe_bytes_stay_separate_from_prepared_inline_text(
    tmp_path: Path,
) -> None:
    content = b"\xff\x00\r\n" + "🌍".encode("utf-8")
    (tmp_path / "source").write_bytes(content)
    entry, guard, execute, continuation = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    request = FileCommand(
        value=[
            ("cat", "CMD"),
            ("source", "PTH"),
            ("|", "CTL"),
            ("tee", "CMD"),
            ("inline é", "ARG"),
            ("copy", "PTH"),
        ]
    )
    progress = execute(guard(entry(request, []), []), [])
    assert isinstance(progress, CommandExecution)
    restored = CommandExecution.model_validate_json(progress.model_dump_json())
    assert restored.stdin == content
    resolved = continuation(restored, [])
    assert resolved.original_input.ready.stdin == "inline é"
    assert resolved.original_input.stdin == content
    result = execute(guard(resolved, []), [])
    assert isinstance(result, Str)
    assert result.value.startswith("Overall: success (exit 0)")
    assert (tmp_path / "copy").read_bytes() == content


def test_touch_then_tee_with_literal_dash_and_control_names(tmp_path: Path) -> None:
    result = _invoke(
        tmp_path,
        [
            ("touch", "CMD"),
            ("--", "FLG"),
            ("-", "PTH"),
            ("&&", "PTH"),
            (" spaced ", "PTH"),
            ("&&", "CTL"),
            ("tee", "CMD"),
            ("--", "FLG"),
            ("content", "ARG"),
            ("-", "PTH"),
            ("&&", "PTH"),
        ],
    )
    assert result.startswith("Overall: success (exit 0)")
    assert (tmp_path / "-").read_text() == "content"
    assert (tmp_path / "&&").read_text() == "content"
    assert (tmp_path / " spaced ").read_bytes() == b""


@pytest.mark.parametrize(
    "options",
    [
        [("-d", "FLG"), ("2000-01-02 03:04:05 UTC", "ARG")],
        [("-a", "FLG"), ("--date", "FLG"), ("@946684800", "ARG")],
        [("-m", "FLG"), ("-t", "FLG"), ("200001020304.05", "ARG")],
        [("-a", "FLG"), ("-m", "FLG"), ("-r", "FLG"), ("reference", "PTH")],
        [
            ("--reference", "FLG"),
            ("reference", "PTH"),
            ("-d", "FLG"),
            ("-5 seconds", "ARG"),
        ],
    ],
)
def test_touch_timestamps_match_native_command(tmp_path: Path, options) -> None:
    for name in ("reference", "actual", "native"):
        (tmp_path / name).write_text("unchanged")
        os.utime(
            tmp_path / name, ns=(1_000_000_000_000_000_000, 1_100_000_000_000_000_000)
        )
    subprocess.run(
        ["touch", *(value for value, _ in options), "native"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    result = _invoke(tmp_path, [("touch", "CMD"), *options, ("actual", "PTH")])
    assert result.startswith("Overall: success (exit 0)")
    actual, native = (tmp_path / "actual").stat(), (tmp_path / "native").stat()
    assert (actual.st_atime_ns, actual.st_mtime_ns) == (
        native.st_atime_ns,
        native.st_mtime_ns,
    )
    assert (tmp_path / "actual").read_text() == "unchanged"


@pytest.mark.parametrize(
    "target", ["missing", "parent/missing", "missing/", "missing/."]
)
def test_touch_no_create_keeps_missing_targets_as_guarded_noops(
    tmp_path: Path, target
) -> None:
    tokens = [("touch", "CMD"), ("--no-create", "FLG"), (target, "PTH")]
    resolved = _resolve(tmp_path, tokens)
    assert [(item.operation, item.location) for item in resolved.items] == [
        (Operation.CREATE, (tmp_path / target).resolve())
    ]
    assert _invoke(tmp_path, tokens).startswith("Overall: success (exit 0)")
    assert list(tmp_path.iterdir()) == []


def test_touch_patterns_keep_order_and_directory_updates_do_not_recurse(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / ".hidden.txt").write_text("hidden")
    (tmp_path / "dir.txt").mkdir()
    child = tmp_path / "dir.txt/child"
    child.write_text("child")
    os.utime(child, (100, 100))
    tokens = [
        ("touch", "CMD"),
        ("-d", "FLG"),
        ("@946684800", "ARG"),
        ("new", "PTH"),
        ("*.txt", "PTH"),
        ("a.txt", "PTH"),
    ]
    resolved = _resolve(tmp_path, tokens)
    assert resolved.original_input.ready.argv[-4:] == [
        str(tmp_path / name) for name in ("new", "a.txt", "dir.txt", "a.txt")
    ]
    assert [(item.operation, item.location.name) for item in resolved.items] == [
        (Operation.CREATE, "new"),
        (Operation.CREATE, "a.txt"),
        (Operation.READ, "a.txt"),
        (Operation.DELETE, "a.txt"),
        (Operation.CREATE, "dir.txt"),
    ]
    assert _invoke(tmp_path, tokens).startswith("Overall: success (exit 0)")
    assert (tmp_path / "dir.txt").stat().st_mtime == 946684800
    assert child.stat().st_mtime == 100


@pytest.mark.parametrize("flags", [[], [("-c", "FLG")]])
def test_touch_unmatched_pattern_rejects_before_creating_any_file(
    tmp_path: Path, flags
) -> None:
    result = _invoke(
        tmp_path, [("touch", "CMD"), *flags, ("new", "PTH"), ("*.txt", "PTH")]
    )
    assert result.startswith("Overall: failure (exit 1)")
    assert "no matches" in result
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "tokens",
    [
        [("tee", "CMD"), ("-i", "FLG")],
        [("tee", "CMD"), ("--output-error", "FLG")],
        [("tee", "CMD"), ("-aa", "FLG")],
        [("tee", "CMD"), ("-a", "FLG"), ("--append", "FLG")],
        [("tee", "CMD"), ("first", "ARG"), ("second", "ARG")],
        [("tee", "CMD"), ("target", "PTH"), ("text", "ARG")],
        [("tee", "CMD"), ("*.txt", "PTH")],
        [("touch", "CMD")],
        [("touch", "CMD"), ("-h", "FLG"), ("target", "PTH")],
        [("touch", "CMD"), ("-am", "FLG"), ("target", "PTH")],
        [("touch", "CMD"), ("-c", "FLG"), ("--no-create", "FLG"), ("target", "PTH")],
        [("touch", "CMD"), ("stray", "ARG")],
        [("touch", "CMD"), ("--date=now", "FLG"), ("target", "PTH")],
        [("touch", "CMD"), ("-d", "FLG"), ("now", "PTH"), ("target", "PTH")],
        [("touch", "CMD"), ("-r", "FLG"), ("reference", "ARG"), ("target", "PTH")],
        [("touch", "CMD"), ("-r", "FLG"), ("missing", "PTH"), ("target", "PTH")],
        [("touch", "CMD"), ("-r", "FLG"), ("*.txt", "PTH"), ("target", "PTH")],
        [("touch", "CMD"), ("-t", "FLG"), ("yesterday", "ARG"), ("target", "PTH")],
        [
            ("touch", "CMD"),
            ("-t", "FLG"),
            ("200001020304", "ARG"),
            ("-d", "FLG"),
            ("now", "ARG"),
            ("target", "PTH"),
        ],
        [
            ("touch", "CMD"),
            ("-t", "FLG"),
            ("200001020304", "ARG"),
            ("-r", "FLG"),
            ("reference", "PTH"),
            ("target", "PTH"),
        ],
    ],
)
def test_writer_argument_errors_leave_no_executable_payload(
    tmp_path: Path, tokens
) -> None:
    resolved = _resolve(tmp_path, tokens)
    assert resolved.original_input.failure
    assert resolved.original_input.ready is None
    assert resolved.items == []


@pytest.mark.parametrize("command", ["tee", "touch"])
@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "dangling",
        "parent_link",
        "hardlink",
        "fifo",
        "parent_missing",
        "slash",
        "dot_suffix",
    ],
)
def test_unsafe_target_prevents_all_writes(tmp_path: Path, command, kind) -> None:
    (tmp_path / "source").write_text("old")
    bad = tmp_path / "bad"
    target = "bad"
    if kind == "symlink":
        bad.symlink_to(tmp_path / "source")
    elif kind == "dangling":
        bad.symlink_to(tmp_path / "missing")
    elif kind == "parent_link":
        bad.symlink_to(tmp_path, target_is_directory=True)
        target = "bad/../target"
    elif kind == "hardlink":
        bad.hardlink_to(tmp_path / "source")
    elif kind == "fifo":
        os.mkfifo(bad)
    elif kind == "parent_missing":
        target = "missing/../bad"
    elif kind == "dot_suffix":
        target = "source/."
    else:
        target = "bad/"
    result = _invoke(tmp_path, [(command, "CMD"), ("safe", "PTH"), (target, "PTH")])
    assert result.startswith("Overall: failure (exit 1)")
    assert not (tmp_path / "safe").exists()
    assert (tmp_path / "source").read_text() == "old"


def test_tee_rejects_directory_before_creating_other_destinations(
    tmp_path: Path,
) -> None:
    (tmp_path / "directory").mkdir()
    result = _invoke(tmp_path, [("tee", "CMD"), ("new", "PTH"), ("directory", "PTH")])
    assert result.startswith("Overall: failure (exit 1)")
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("command", ["tee", "touch"])
@pytest.mark.parametrize(
    "operation", [Operation.CREATE, Operation.READ, Operation.DELETE]
)
def test_existing_file_denial_prevents_all_writes_and_approvals(
    tmp_path: Path, monkeypatch, command, operation
) -> None:
    target = tmp_path / "target"
    target.write_text("old")
    os.utime(target, (100, 100))

    def unexpected_prompt(*args, **kwargs):
        pytest.fail("A policy denial must prevent every approval prompt")

    monkeypatch.setattr(runtime, "interact_with_user", unexpected_prompt)
    flags = [("-a", "FLG")] if command == "tee" else []
    result = _invoke(
        tmp_path,
        [(command, "CMD"), *flags, ("new", "PTH"), ("target", "PTH")],
        deny_rules=[PermissionRule(pattern="target", operations={operation})],
        ask_rules=[PermissionRule(pattern="**", operations=set(Operation))],
        pipe=EventPipe(),
    )
    assert result.startswith("Overall: failure (exit 1)")
    assert target.stat().st_mtime == 100
    assert target.read_text() == "old"
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("accepted", [True, False])
def test_repeated_tee_destinations_keep_native_append_and_deduplicate_approvals(
    tmp_path: Path, monkeypatch, accepted: bool
) -> None:
    (tmp_path / "target").write_text("old")
    prompts = []

    def approve(message: str, *, with_reply: bool) -> str:
        prompts.append(message)
        return "yes" if accepted else "no"

    monkeypatch.setattr(runtime, "interact_with_user", approve)
    result = _invoke(
        tmp_path,
        [
            ("tee", "CMD"),
            ("-a", "FLG"),
            ("new", "ARG"),
            ("target", "PTH"),
            ("./target", "PTH"),
        ],
        ask_rules=[PermissionRule(pattern="target", operations=set(Operation))],
        pipe=EventPipe(),
    )
    assert result.startswith("Overall: success") is accepted
    assert len(prompts) == (3 if accepted else 1)
    assert (tmp_path / "target").read_text() == ("oldnewnew" if accepted else "old")


def test_reference_read_denial_prevents_touch(tmp_path: Path) -> None:
    (tmp_path / "reference").mkdir()
    result = _invoke(
        tmp_path,
        [("touch", "CMD"), ("-r", "FLG"), ("reference", "PTH"), ("target", "PTH")],
        deny_rules=[PermissionRule(pattern="reference", operations={Operation.READ})],
    )
    assert result.startswith("Overall: failure (exit 1)")
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize("failure", ["denied", "invalid", "path"])
def test_tee_failures_hide_inline_content_and_allow_independent_fallback(
    tmp_path: Path, failure
) -> None:
    content = "private inline text"
    invalid = [("--invalid", "FLG")] if failure == "invalid" else []
    target = "missing/blocked" if failure == "path" else "blocked"
    result = _invoke(
        tmp_path,
        [
            ("tee", "CMD"),
            *invalid,
            (content, "ARG"),
            (target, "PTH"),
            ("||", "CTL"),
            ("touch", "CMD"),
            ("fallback", "PTH"),
            ("||", "CTL"),
            ("tee", "CMD"),
            ("--invalid", "FLG"),
            ("skipped", "PTH"),
        ],
        deny_rules=[PermissionRule(pattern="blocked", operations={Operation.CREATE})],
    )
    assert result.startswith("Overall: success (exit 0)")
    assert content not in result
    label = (
        shlex.join(["tee", str(tmp_path / target)]) if failure == "denied" else "tee"
    )
    assert f"--- begin: {label} ---\n" in result
    assert f"--- end: {label} ---" in result
    assert (tmp_path / "fallback").exists()
    assert not (tmp_path / "blocked").exists()
    assert not (tmp_path / "skipped").exists()
