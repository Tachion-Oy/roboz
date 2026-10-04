"""Tagged searches preserve native results after guarding required reads."""

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from roboz import Agent, runtime
from roboz.llm import MockLLMEndpoint
from roboz.models import Role, Str
from roboz.runtime import EventPipe
from roboz.shed.models import ActionVerdict, Operation, PermissionRule
from roboz.shed.tools.cli_commands_v2 import (
    TaggedFileCommand,
    get_run_tagged_file_command,
)
from roboz.shed.tools.cli_commands_v2.command import resolve_tagged_command
from roboz.shed.tools.cli_commands_v2 import paths
from roboz.shed.tools.cli_commands_v2.contracts import CommandExecution
from roboz.tools import stop


def _invoke(base: Path, tokens: list, **policy) -> str:
    tools = get_run_tagged_file_command(
        base=base,
        default_verdict=policy.pop("default_verdict", ActionVerdict.allow),
        **policy,
    )
    agent = Agent(
        name="search_test",
        system_prompt="Search the requested inputs.",
        tools=[*tools, stop],
        agent_endpoint=MockLLMEndpoint(
            responses=[
                {
                    "action": "run_tagged_file_command",
                    "rationale": "Search",
                    "value": tokens,
                },
                {"action": "stop", "rationale": "Done", "value": "ok"},
            ]
        ),
        initial_messages=None,
    )
    _, messages = agent.invoke()
    responses = [json.loads(m.content) for m in messages if m.role == Role.USER]
    return next(
        r["value"]
        for r in reversed(responses)
        if r.get("caller") == "execute_tagged_file_command"
    )


@pytest.mark.parametrize("command", ["grep", "rg"])
@pytest.mark.parametrize(
    "options, pattern",
    [
        ([], "needle"),
        ([("--line-number", "FLG"), ("--ignore-case", "FLG")], "needle"),
        ([("-F", "FLG")], "a.b"),
        ([("-v", "FLG")], "needle"),
        ([("-w", "FLG")], "word"),
        ([("-x", "FLG")], "needle"),
        ([("-c", "FLG")], "needle"),
        ([("-l", "FLG")], "needle"),
        ([("-q", "FLG")], "needle"),
        ([("-o", "FLG")], "needle"),
        ([("--max-count", "FLG"), ("1", "ARG")], "needle"),
        ([("-m", "FLG"), ("0", "ARG")], "needle"),
        ([("-A", "FLG"), ("1", "ARG"), ("-B", "FLG"), ("1", "ARG")], "needle"),
        ([("--context", "FLG"), ("1", "ARG")], "needle"),
        ([], "-dash"),
        ([], ""),
        ([], "absent"),
        ([], "["),
    ],
)
def test_search_output_and_status_match_native(
    tmp_path: Path, command, options, pattern
) -> None:
    source = tmp_path / "source"
    source.write_text("before\nNEEDLE\nneedle\nneedle word\na.b\nwordy\n-dash\nafter\n")
    native = subprocess.run(
        [command, *(value for value, _ in options), "-e", pattern, "--", str(source)],
        cwd=tmp_path,
        input=b"",
        capture_output=True,
    )
    result = _invoke(
        tmp_path, [(command, "CMD"), *options, (pattern, "ARG"), ("source", "PTH")]
    )
    status = "success" if native.returncode == 0 else "failure"
    assert result.startswith(f"Overall: {status} (exit {native.returncode})")
    if native.stdout:
        body = result.split("\n", 2)[2].rsplit("\n--- end:", 1)[0]
        assert body == native.stdout.decode().rstrip("\n")


@pytest.mark.parametrize("command, flags", [("grep", ["-R", "-E"]), ("rg", [])])
def test_recursive_search_matches_native(tmp_path: Path, command, flags) -> None:
    (tmp_path / "src/nested").mkdir(parents=True)
    (tmp_path / "src/a").write_text("todo\n")
    (tmp_path / "src/nested/b").write_text("fixme\n")
    native = subprocess.run(
        [command, *flags, "-n", "todo|fixme", str(tmp_path / "src")],
        cwd=tmp_path,
        input=b"",
        capture_output=True,
        check=True,
    )
    result = _invoke(
        tmp_path,
        [
            (command, "CMD"),
            *((f, "FLG") for f in flags),
            ("-n", "FLG"),
            ("todo|fixme", "ARG"),
            ("src", "PTH"),
        ],
    )
    body = result.split("\n", 2)[2].rsplit("\n--- end:", 1)[0]
    assert sorted(body.splitlines()) == sorted(native.stdout.decode().splitlines())


@pytest.mark.parametrize("command, flags", [("grep", ["-r"]), ("rg", [])])
def test_recursive_search_timeout_stops_directory_enumeration(
    tmp_path: Path, monkeypatch, command, flags
) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / name).write_text("needle\n")
    elapsed = 0
    enumerated = []
    native_scandir = os.scandir

    def slow_entries(entries):
        nonlocal elapsed
        for entry in entries:
            elapsed += 30
            enumerated.append(entry.name)
            yield entry

    @contextmanager
    def slow_scandir(path):
        with native_scandir(path) as entries:
            yield slow_entries(entries)

    monkeypatch.setattr(paths, "monotonic", lambda: elapsed)
    monkeypatch.setattr(paths, "scandir", slow_scandir)
    resolved = resolve_tagged_command(tmp_path)(
        TaggedFileCommand(
            value=[
                (command, "CMD"),
                *((flag, "FLG") for flag in flags),
                ("needle", "ARG"),
                (".", "PTH"),
            ]
        ),
        [],
    )
    assert resolved.original_input.failure == (
        "Search preparation timed out after 60 seconds"
    )
    assert resolved.original_input.ready is None
    assert resolved.items == []
    assert len(enumerated) < 3


def test_rg_ignore_checks_share_the_tree_traversal_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "src/source"
    source.parent.mkdir()
    source.write_text("needle\n")
    ignore = tmp_path / ".ignore"
    elapsed = 0
    native_stat = os.stat

    def slow_stat(path, *args, **kwargs):
        nonlocal elapsed
        if path in (str(source), str(ignore)):
            elapsed += 30
        return native_stat(path, *args, **kwargs)

    monkeypatch.setattr(paths, "monotonic", lambda: elapsed)
    monkeypatch.setattr(os, "stat", slow_stat)
    resolved = resolve_tagged_command(tmp_path)(
        TaggedFileCommand(value=[("rg", "CMD"), ("needle", "ARG"), ("src", "PTH")]),
        [],
    )
    assert resolved.original_input.failure == (
        "Search preparation timed out after 60 seconds"
    )
    assert resolved.original_input.ready is None
    assert resolved.items == []


def test_rg_honors_ancestor_and_nested_ignores_in_native_search(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("ignored\n")
    (tmp_path / "src/sub").mkdir(parents=True)
    (tmp_path / "src/ignored").write_text("hidden needle\n")
    (tmp_path / "src/sub/.ignore").write_text("private\n")
    (tmp_path / "src/sub/private").write_text("hidden needle\n")
    (tmp_path / "src/visible").write_text("needle\n")
    result = _invoke(
        tmp_path,
        [("rg", "CMD"), ("needle", "ARG"), ("src", "PTH")],
    )
    assert result.startswith("Overall: success")
    assert "visible:needle" in result
    assert "hidden needle" not in result


def test_rg_ignore_named_directory_does_not_block_search(tmp_path: Path) -> None:
    (tmp_path / "src/.ignore").mkdir(parents=True)
    (tmp_path / "src/.ignore/nested").write_text("hidden needle\n")
    (tmp_path / "src/visible").write_text("needle\n")
    result = _invoke(tmp_path, [("rg", "CMD"), ("needle", "ARG"), ("src", "PTH")])
    assert result.startswith("Overall: success")
    assert "visible:needle" in result
    assert "hidden needle" not in result


@pytest.mark.parametrize(
    "target, flags", [("src/ignored", []), ("src/*", []), ("src", ["-uu"])]
)
def test_rg_explicit_files_globs_and_unrestricted_search_require_selected_read(
    tmp_path: Path, target, flags
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/.ignore").write_text("ignored\n")
    (tmp_path / "src/ignored").write_text("needle\n")
    result = _invoke(
        tmp_path,
        [
            ("rg", "CMD"),
            *((flag, "FLG") for flag in flags),
            ("needle", "ARG"),
            (target, "PTH"),
        ],
        deny_rules=[PermissionRule(pattern="src/ignored", operations={Operation.READ})],
    )
    assert f"Denied {Operation.READ}" in result
    assert "needle\n" not in result


@pytest.mark.parametrize(
    "denied", [".ignore", "src/.ignore", "src/source", "src/ignored", "src/.hidden"]
)
def test_rg_denied_candidate_or_ignore_file_prevents_execution_and_approvals(
    tmp_path: Path, monkeypatch, denied
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / ".ignore").write_text("unused\n")
    (tmp_path / "src/.ignore").write_text("ignored\n")
    (tmp_path / "src/source").write_text("needle\n")
    (tmp_path / "src/ignored").write_text("hidden needle\n")
    (tmp_path / "src/.hidden").write_text("hidden needle\n")
    calls = []
    prompts = []
    native_run = subprocess.run

    def run(argv, **kwargs):
        calls.append(argv)
        return native_run(argv, **kwargs)

    def approve(message: str, *, with_reply: bool) -> str:
        prompts.append(message)
        return "yes"

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(runtime, "interact_with_user", approve)
    result = _invoke(
        tmp_path,
        [("rg", "CMD"), ("needle", "ARG"), ("src", "PTH")],
        deny_rules=[PermissionRule(pattern=denied, operations={Operation.READ})],
        ask_rules=[PermissionRule(pattern="src/**", operations={Operation.READ})],
        pipe=EventPipe(),
    )
    assert f"Denied {Operation.READ}" in result
    assert calls == []
    assert prompts == []


def test_rg_preserves_pipe_bytes_through_serialized_continuation(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/file").write_bytes(b"other\n")
    (tmp_path / "source").write_bytes(b"needle\r\n")
    entry, guard, execute, continuation = get_run_tagged_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow
    )
    request = TaggedFileCommand(
        value=[
            ("cat", "CMD"),
            ("source", "PTH"),
            ("|", "CTL"),
            ("rg", "CMD"),
            ("needle", "ARG"),
            ("src", "PTH"),
            ("-", "ARG"),
            ("|", "CTL"),
            ("tee", "CMD"),
            ("output", "PTH"),
        ]
    )
    progress = execute(guard(entry(request, []), []), [])
    assert isinstance(progress, CommandExecution)
    prepared = continuation(progress, [])
    prepared.original_input = CommandExecution.model_validate_json(
        prepared.original_input.model_dump_json()
    )
    assert prepared.original_input.stdin == b"needle\r\n"
    progress = execute(guard(prepared, []), [])
    assert isinstance(progress, CommandExecution)
    progress = CommandExecution.model_validate_json(progress.model_dump_json())
    while isinstance(progress, CommandExecution):
        progress = execute(guard(continuation(progress, []), []), [])
    assert isinstance(progress, Str) and progress.value.startswith("Overall: success")
    assert (tmp_path / "output").read_bytes() == b"<stdin>:needle\r\n"


@pytest.mark.parametrize("reply", ["yes", "no"])
def test_rg_deduplicates_ignore_file_approval_across_operands(
    tmp_path: Path, monkeypatch, reply
) -> None:
    (tmp_path / "src").mkdir()
    ignore = tmp_path / "src/.ignore"
    ignore.write_text("needle\n")
    prompts = []

    def approve(message: str, *, with_reply: bool) -> str:
        prompts.append(message)
        return reply

    monkeypatch.setattr(runtime, "interact_with_user", approve)
    result = _invoke(
        tmp_path,
        [("rg", "CMD"), ("needle", "ARG"), ("src", "PTH"), ("src/.ignore", "PTH")],
        ask_rules=[PermissionRule(pattern="src/.ignore", operations={Operation.READ})],
        pipe=EventPipe(),
    )
    assert len(prompts) == 1
    assert result.startswith("Overall: success") is (reply == "yes")


def test_rg_ignores_ambient_command_configuration(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config"
    config.write_text("--files\n")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(config))
    (tmp_path / "src").mkdir()
    (tmp_path / "src/source").write_text("needle\n")
    result = _invoke(tmp_path, [("rg", "CMD"), ("needle", "ARG"), ("src", "PTH")])
    assert "source:needle" in result


@pytest.mark.parametrize(
    "command, flags", [("grep", []), ("rg", []), ("grep", ["-r"]), ("rg", ["--hidden"])]
)
def test_search_stdin_requires_no_filesystem_permissions(
    tmp_path: Path, command, flags
) -> None:
    result = _invoke(
        tmp_path,
        [
            ("tee", "CMD"),
            ("needle\nother\n", "ARG"),
            ("|", "CTL"),
            (command, "CMD"),
            *((f, "FLG") for f in flags),
            ("needle", "ARG"),
            ("-", "ARG"),
        ],
        default_verdict=ActionVerdict.deny,
    )
    assert result.startswith("Overall: success")
    assert "\nneedle\n" in result
    assert "other" not in result


def test_grep_recursive_without_paths_searches_base(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("needle\n")
    result = _invoke(tmp_path, [("grep", "CMD"), ("-r", "FLG"), ("needle", "ARG")])
    assert "source:needle" in result


@pytest.mark.parametrize("command, flags", [("grep", ["-r"]), ("rg", [])])
@pytest.mark.parametrize("entry", ["symlink", "fifo"])
def test_recursive_search_skips_native_excluded_entries(
    tmp_path: Path, command, flags, entry
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "outside").write_text("secret needle\n")
    if entry == "symlink":
        (tmp_path / "src/link").symlink_to(tmp_path / "outside")
    else:
        os.mkfifo(tmp_path / "src/pipe")
    (tmp_path / "src/source").write_text("needle\n")
    result = _invoke(
        tmp_path,
        [
            (command, "CMD"),
            *((f, "FLG") for f in flags),
            ("needle", "ARG"),
            ("src", "PTH"),
        ],
    )
    assert result.startswith("Overall: success")
    assert "secret" not in result


@pytest.mark.parametrize("command, flags", [("grep", ["-R"]), ("rg", [])])
def test_search_rejects_selected_hardlinks(tmp_path: Path, command, flags) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "source").write_text("needle\n")
    (tmp_path / "src/link").hardlink_to(tmp_path / "source")
    result = _invoke(
        tmp_path,
        [
            (command, "CMD"),
            *((f, "FLG") for f in flags),
            ("needle", "ARG"),
            ("src", "PTH"),
        ],
    )
    assert "Hard-linked files are unsupported" in result


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        [("file", "PTH")],
        [("needle", "ARG"), ("file", "ARG")],
        [("--pre", "FLG"), ("program", "ARG"), ("needle", "ARG")],
        [("-f", "FLG"), ("patterns", "PTH")],
        [("-ni", "FLG"), ("needle", "ARG")],
        [("-n", "FLG"), ("--line-number", "FLG"), ("needle", "ARG")],
        [("-m", "FLG"), ("-1", "ARG"), ("needle", "ARG")],
        [("-C", "FLG"), ("1", "PTH"), ("needle", "ARG")],
        [("needle", "ARG"), ("-n", "FLG")],
    ],
)
@pytest.mark.parametrize("command", ["grep", "rg"])
def test_search_invalid_arguments_leave_no_payload(
    tmp_path: Path, command, arguments
) -> None:
    resolved = resolve_tagged_command(tmp_path)(
        TaggedFileCommand(value=[(command, "CMD"), *arguments]), []
    )
    assert resolved.original_input.failure
    assert resolved.original_input.ready is None
    assert resolved.items == []


def test_no_matches_in_empty_tree_select_native_fallback(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    result = _invoke(
        tmp_path,
        [
            ("rg", "CMD"),
            ("needle", "ARG"),
            ("empty", "PTH"),
            ("&&", "CTL"),
            ("touch", "CMD"),
            ("skipped", "PTH"),
            ("||", "CTL"),
            ("tee", "CMD"),
            ("fallback\n", "ARG"),
            ("|", "CTL"),
            ("grep", "CMD"),
            ("fallback", "ARG"),
        ],
    )
    assert result.startswith("Overall: success")
    assert "exit 1" in result
    assert "\nfallback\n" in result
    assert not (tmp_path / "skipped").exists()


@pytest.mark.parametrize(
    "flag, expected",
    [
        (None, ["visible"]),
        ("--hidden", ["visible", ".hidden"]),
        ("--no-ignore", ["visible", "ignored"]),
        ("-uu", ["visible", "ignored", ".hidden"]),
    ],
)
def test_rg_filters_match_native_selection(tmp_path: Path, flag, expected) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/.ignore").write_text("ignored\n")
    for name in ("visible", "ignored", ".hidden"):
        (tmp_path / "src" / name).write_text("needle\n")
    flags = [(flag, "FLG")] if flag else []
    result = _invoke(
        tmp_path,
        [("rg", "CMD"), *flags, ("-l", "FLG"), ("needle", "ARG"), ("src", "PTH")],
    )
    body = result.split("\n", 2)[2].rsplit("\n--- end:", 1)[0]
    assert sorted(body.splitlines()) == sorted(
        str(tmp_path / "src" / name) for name in expected
    )


def test_rg_search_keeps_newlines_in_filenames(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/line\nbreak").write_text("needle\n")
    result = _invoke(
        tmp_path, [("rg", "CMD"), ("-I", "FLG"), ("needle", "ARG"), ("src", "PTH")]
    )
    assert result.startswith("Overall: success")
    assert "\nneedle\n" in result
