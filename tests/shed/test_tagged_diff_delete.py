"""Guarded comparisons and deletion preserve native arguments and results."""

import json
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

import pytest

from roboz import Agent, runtime
from roboz.llm import MockLLMEndpoint
from roboz.models import Role
from roboz.runtime import EventPipe
from roboz.shed.models import ActionVerdict, Operation, PermissionRule
from roboz.shed.tools.cli_commands_v2 import TaggedFileCommand, get_run_tagged_file_command
from roboz.shed.tools.cli_commands_v2 import paths
from roboz.shed.tools.cli_commands_v2.command import resolve_tagged_command
from roboz.tools import stop


def _invoke(base: Path, tokens: list, **policy) -> str:
    tools = get_run_tagged_file_command(
        base=base,
        default_verdict=policy.pop("default_verdict", ActionVerdict.allow),
        **policy,
    )
    agent = Agent(
        name="diff_delete_test",
        system_prompt="Compare or delete the requested files.",
        tools=[*tools, stop],
        agent_endpoint=MockLLMEndpoint(responses=[
            {"action": "run_tagged_file_command", "rationale": "Run", "value": tokens},
            {"action": "stop", "rationale": "Done", "value": "ok"},
        ]),
        initial_messages=None,
    )
    _, messages = agent.invoke()
    responses = [json.loads(m.content) for m in messages if m.role == Role.USER]
    return next(
        r["value"] for r in reversed(responses)
        if r.get("caller") == "execute_tagged_file_command"
    )


def _resolve(base: Path, tokens: list):
    return resolve_tagged_command(base)(TaggedFileCommand(value=tokens), [])


def _delete_tokens(command: str) -> list:
    return [("gio", "CMD"), ("trash", "ARG")] if command == "gio" else [
        ("rm", "CMD"), ("-r", "FLG"), ("-f", "FLG")
    ]


@pytest.mark.parametrize("flags, second", [
    ([], "same\n"),
    ([], "different\n"),
    (["-u"], "different\n"),
    (["--unified"], "different\n"),
    (["-q"], "different\n"),
    (["--brief"], "different\n"),
    (["-s"], "same\n"),
    (["--report-identical-files"], "same\n"),
    (["-i", "-w", "--"], "S A M E\n"),
    (["--ignore-case", "--ignore-all-space"], "S A M E\n"),
])
def test_diff_output_and_status_match_native(tmp_path: Path, flags, second) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("same\n")
    b.write_text(second)
    native = subprocess.run(["diff", *flags, str(a), str(b)], capture_output=True)
    result = _invoke(tmp_path, [
        ("diff", "CMD"), *((flag, "FLG") for flag in flags), ("a", "PTH"), ("b", "PTH")
    ])
    status = "success" if native.returncode == 0 else "failure"
    assert result.startswith(f"Overall: {status} (exit {native.returncode})")
    if native.stdout:
        assert "\n" + native.stdout.decode().rstrip("\n") + "\n--- end:" in result
    else:
        assert "Command completed with no output:" in result


@pytest.mark.parametrize("operands, valid", [
    ([], False), (["a"], False), (["*.txt"], True), (["a", "*.txt"], False),
    (["a", "a"], True), (["b.txt", "a"], True),
])
def test_diff_counts_expanded_operands_and_preserves_order_and_repeats(
    tmp_path: Path, operands, valid,
) -> None:
    for name in ("a", "a.txt", "b.txt"):
        (tmp_path / name).touch()
    resolved = _resolve(tmp_path, [("diff", "CMD"), *((p, "PTH") for p in operands)])
    if not valid:
        assert "exactly two" in resolved.original_input.failure
        assert resolved.original_input.ready is None
        return
    expected = ["a.txt", "b.txt"] if operands == ["*.txt"] else operands
    assert resolved.original_input.ready.argv == ["diff", *(str(tmp_path / p) for p in expected)]
    assert [(i.operation, i.location) for i in resolved.items] == [
        (Operation.READ, tmp_path / p) for p in dict.fromkeys(expected)
    ]


@pytest.mark.parametrize("kind", ["directory", "symlink", "hardlink", "fifo", "missing"])
def test_diff_reuses_reader_restrictions(tmp_path: Path, kind) -> None:
    (tmp_path / "a").touch()
    b = tmp_path / "b"
    if kind == "directory":
        b.mkdir()
    elif kind == "symlink":
        b.symlink_to(tmp_path / "a")
    elif kind == "hardlink":
        b.hardlink_to(tmp_path / "a")
    elif kind == "fifo":
        os.mkfifo(b)
    resolved = _resolve(tmp_path, [("diff", "CMD"), ("a", "PTH"), ("b", "PTH")])
    assert resolved.original_input.failure
    assert resolved.original_input.ready is None


@pytest.mark.parametrize("identical", [False, True])
def test_diff_status_controls_chaining(tmp_path: Path, identical) -> None:
    (tmp_path / "a").write_text("a\n")
    (tmp_path / "b").write_text("a\n" if identical else "b\n")
    result = _invoke(tmp_path, [
        ("diff", "CMD"), ("a", "PTH"), ("b", "PTH"), ("&&", "CTL"),
        ("touch", "CMD"), ("equal", "PTH"), ("||", "CTL"),
        ("touch", "CMD"), ("different", "PTH"),
    ])
    assert result.startswith("Overall: success")
    assert (tmp_path / "equal").exists() is identical
    assert (tmp_path / "different").exists() is not identical


@pytest.mark.parametrize("tokens", [
    [("diff", "CMD"), ("-r", "FLG")],
    [("diff", "CMD"), ("--unified=2", "FLG")],
    [("diff", "CMD"), ("-u", "FLG"), ("--unified", "FLG")],
    [("diff", "CMD"), ("-", "ARG")],
    [("gio", "CMD")],
    [("gio", "CMD"), ("info", "ARG")],
    [("gio", "CMD"), ("trash", "PTH")],
    [("gio", "CMD"), ("file", "PTH"), ("trash", "ARG")],
    [("gio", "CMD"), ("trash", "ARG"), ("trash", "ARG")],
    [("gio", "CMD"), ("trash", "ARG"), ("--force", "FLG")],
    [("gio", "CMD"), ("trash", "ARG"), ("--", "FLG")],
    [("rm", "CMD"), ("-rf", "FLG")],
    [("rm", "CMD"), ("-r", "FLG"), ("-R", "FLG")],
    [("rm", "CMD"), ("--force=yes", "FLG")],
    [("rm", "CMD"), ("-i", "FLG")],
    [("rm", "CMD"), ("file", "ARG")],
    [("rm", "CMD"), ("file", "PTH"), ("-f", "FLG")],
    [("rm", "CMD"), ("--", "FLG"), ("-f", "FLG")],
])
def test_invalid_syntax_never_launches(tmp_path: Path, monkeypatch, tokens) -> None:
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid command launched a process")

    monkeypatch.setattr(subprocess, "run", unexpected)
    assert _invoke(tmp_path, tokens).startswith("Overall: failure (exit 1)")


@pytest.mark.parametrize("recursive_flag", ["-r", "-R", "--recursive"])
def test_rm_recursively_removes_all_entry_types_without_following_links(
    tmp_path: Path, recursive_flag,
) -> None:
    tree = tmp_path / "tree"
    (tree / ".hidden/nested").mkdir(parents=True)
    (tree / ".hidden/nested/data").write_text("hidden")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("target contents")
    (tree / "directory_link").symlink_to(outside, target_is_directory=True)
    (tree / "file_link").symlink_to(outside / "keep")
    (tree / "dangling").symlink_to(outside / "absent")
    (tree / "hardlink").hardlink_to(outside / "keep")
    os.mkfifo(tree / "fifo")
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(tree / "socket"))
        result = _invoke(
            tmp_path, [("rm", "CMD"), (recursive_flag, "FLG"), ("tree", "PTH")],
            default_verdict=ActionVerdict.deny,
            allow_rules=[PermissionRule(pattern="tree/**", operations={Operation.DELETE})],
        )
    assert result.startswith("Overall: success")
    assert not tree.exists()
    assert (outside / "keep").read_text() == "target contents"
    assert (outside / "keep").stat().st_nlink == 1


@pytest.mark.parametrize("pattern", ["links/*", "links/**/*", "links/dangling"])
def test_rm_globs_select_terminal_links_including_dangling_links(
    tmp_path: Path, pattern,
) -> None:
    (tmp_path / "links").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside/keep").write_text("survive")
    (tmp_path / "links/dangling").symlink_to(tmp_path / "absent")
    (tmp_path / "links/directory").symlink_to(tmp_path / "outside", target_is_directory=True)
    (tmp_path / "links/file").symlink_to(tmp_path / "outside/keep")
    result = _invoke(
        tmp_path, [("rm", "CMD"), (pattern, "PTH")],
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="links/*", operations={Operation.DELETE})],
    )
    assert result.startswith("Overall: success")
    assert not (tmp_path / "links/dangling").is_symlink()
    if "*" in pattern:
        assert list((tmp_path / "links").iterdir()) == []
    assert (tmp_path / "outside/keep").read_text() == "survive"


@pytest.mark.parametrize("operand", ["link/file", "link/../safe", "link/.", "link/", "link/*"])
def test_symlink_parent_paths_prevent_every_deletion(tmp_path: Path, operand) -> None:
    (tmp_path / "safe").touch()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside/file").touch()
    (tmp_path / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    result = _invoke(tmp_path, [*_delete_tokens("rm"), ("safe", "PTH"), (operand, "PTH")])
    assert result.startswith("Overall: failure")
    assert (tmp_path / "safe").exists()
    assert (tmp_path / "outside/file").exists()


@pytest.mark.parametrize("operand, exit_code, removed", [
    ("tree/", 0, True), ("tree/.", 1, False), ("tree/nested/..", 1, False),
    ("file/", 1, False), ("absent/../file", 1, False),
])
def test_rm_preserves_meaningful_path_suffixes(tmp_path: Path, operand, exit_code, removed) -> None:
    (tmp_path / "tree/nested").mkdir(parents=True)
    (tmp_path / "tree/nested/keep").touch()
    (tmp_path / "file").touch()
    tokens = [("rm", "CMD"), ("-r", "FLG"), (operand, "PTH")]
    resolved = _resolve(tmp_path, tokens)
    assert resolved.original_input.ready.argv[-1] == str(tmp_path) + "/" + operand
    result = _invoke(tmp_path, tokens)
    assert f"(exit {exit_code})" in result.splitlines()[0]
    assert (tmp_path / "tree").exists() is not removed
    assert (tmp_path / "file").exists()


@pytest.mark.parametrize("flags, operands, exit_code", [
    ([], [], 1), (["-f"], [], 0), (["--force"], ["absent"], 0),
    ([], ["absent"], 1), (["-f"], ["file", "absent"], 0),
    ([], ["file", "absent"], 1), ([], ["directory"], 1),
    (["--"], ["--force"], 0), (["--"], ["-"], 0),
])
def test_rm_native_missing_no_operand_and_nonrecursive_behavior(
    tmp_path: Path, flags, operands, exit_code,
) -> None:
    for name in ("file", "--force", "-"):
        (tmp_path / name).touch()
    (tmp_path / "directory").mkdir()
    result = _invoke(tmp_path, [
        ("rm", "CMD"), *((f, "FLG") for f in flags), *((p, "PTH") for p in operands)
    ])
    assert f"(exit {exit_code})" in result.splitlines()[0]
    assert (tmp_path / "file").exists() is ("file" not in operands)
    assert (tmp_path / "directory").is_dir()
    for name in ("--force", "-"):
        assert (tmp_path / name).exists() is (name not in operands)


@pytest.mark.parametrize("command", ["rm", "gio"])
@pytest.mark.parametrize("decision", ["deny", "decline", "approve"])
def test_deletion_permissions_and_approvals_complete_before_one_launch(
    tmp_path: Path, monkeypatch, command, decision,
) -> None:
    (tmp_path / "tree/.hidden").mkdir(parents=True)
    (tmp_path / "tree/.hidden/file").touch()
    prompts, launches = [], []

    def approve(message, **kwargs):
        assert launches == []
        prompts.append(message)
        return "no" if decision == "decline" else "yes"

    def record(argv, **kwargs):
        launches.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(runtime, "interact_with_user", approve)
    monkeypatch.setattr(subprocess, "run", record)
    result = _invoke(
        tmp_path, [*_delete_tokens(command), ("tree", "PTH"), ("tree", "PTH")],
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="tree/**", operations={Operation.DELETE})],
        deny_rules=[PermissionRule(pattern="tree/.hidden/file", operations={Operation.DELETE})]
        if decision == "deny" else [],
        ask_rules=[PermissionRule(pattern="tree/**", operations={Operation.DELETE})],
        pipe=EventPipe(),
    )
    assert result.startswith("Overall: success") is (decision == "approve")
    assert len(prompts) == {"deny": 0, "decline": 1, "approve": 3}[decision]
    assert len(launches) == (1 if decision == "approve" else 0)
    if launches:
        assert launches[0][-2:] == [str(tmp_path / "tree")] * 2
    assert (tmp_path / "tree/.hidden/file").exists()


@pytest.mark.parametrize("command", ["rm", "gio"])
@pytest.mark.parametrize("operands", [["first", "tree"], ["first", "missing/../tree", "tree"]])
def test_denied_descendant_prevents_actual_deletion(
    tmp_path: Path, monkeypatch, command, operands,
) -> None:
    (tmp_path / "first").touch()
    (tmp_path / "tree/.hidden").mkdir(parents=True)
    (tmp_path / "tree/.hidden/keep").touch()

    def unexpected(*args, **kwargs):
        pytest.fail("Denied deletion launched a process")

    if command == "gio":
        monkeypatch.setattr(subprocess, "run", unexpected)
    result = _invoke(
        tmp_path, [*_delete_tokens(command), *((p, "PTH") for p in operands)],
        deny_rules=[PermissionRule(pattern="tree/.hidden/keep", operations={Operation.DELETE})],
    )
    assert result.startswith("Overall: failure")
    assert (tmp_path / "first").exists()
    assert (tmp_path / "tree/.hidden/keep").exists()


def test_force_does_not_bypass_missing_target_policy_or_unmatched_glob(tmp_path: Path) -> None:
    (tmp_path / "safe").touch()
    for operand in ("missing", "missing*"):
        result = _invoke(
            tmp_path, [("rm", "CMD"), ("-f", "FLG"), ("safe", "PTH"), (operand, "PTH")],
            deny_rules=[PermissionRule(pattern="missing", operations={Operation.DELETE})],
        )
        assert result.startswith("Overall: failure")
        assert (tmp_path / "safe").exists()


@pytest.mark.parametrize("command", ["rm", "gio"])
@pytest.mark.parametrize("glob", [False, True])
def test_deletion_deadline_covers_all_operands_and_glob_expansion(
    tmp_path: Path, monkeypatch, command, glob,
) -> None:
    (tmp_path / "first").mkdir()
    (tmp_path / "first/a").touch()
    (tmp_path / "second").mkdir()
    (tmp_path / "second/b").touch()
    (tmp_path / "third").mkdir()
    elapsed = 0
    enumerated = []
    native_scandir = os.scandir

    def slow_entries(entries):
        nonlocal elapsed
        for entry in entries:
            elapsed += 31
            enumerated.append(entry.path)
            yield entry

    @contextmanager
    def slow_scandir(path):
        with native_scandir(path) as entries:
            yield slow_entries(entries)

    def unexpected(*args, **kwargs):
        pytest.fail("Timed-out preparation launched a process")

    monkeypatch.setattr(paths, "monotonic", lambda: elapsed)
    monkeypatch.setattr(paths, "scandir", slow_scandir)
    monkeypatch.setattr(subprocess, "run", unexpected)
    operands = ["**/*"] if glob else ["first", "second"]
    result = _invoke(tmp_path, [*_delete_tokens(command), *((p, "PTH") for p in operands)])
    assert "Deletion preparation timed out after 60 seconds" in result
    assert len(enumerated) == 2
    assert (tmp_path / "first/a").exists() and (tmp_path / "second/b").exists()


@pytest.mark.parametrize("operands, status", [([], 1), (["entries/*"], 0), (["entries/.", "absent"], 2)])
def test_gio_arguments_delete_requirements_and_native_results_are_isolated(
    tmp_path: Path, monkeypatch, operands, status,
) -> None:
    (tmp_path / "entries").mkdir()
    (tmp_path / "entries/link").symlink_to(tmp_path / "missing")
    (tmp_path / "entries/file").touch()
    tokens = [("gio", "CMD"), ("trash", "ARG"), *((p, "PTH") for p in operands)]
    resolved = _resolve(tmp_path, tokens)
    expected_paths = (
        [tmp_path] if not operands else [tmp_path / "entries/file", tmp_path / "entries/link"]
        if operands == ["entries/*"] else [tmp_path / "entries", tmp_path / "entries/file",
                                           tmp_path / "entries/link", tmp_path / "absent"]
    )
    assert [(i.operation, i.location) for i in resolved.items] == [
        (Operation.DELETE, p) for p in expected_paths
    ]
    launched = []

    def record(argv, **kwargs):
        launched.append(argv)
        return subprocess.CompletedProcess(argv, status, b"native stdout\n", b"native stderr\n")

    monkeypatch.setattr(subprocess, "run", record)
    result = _invoke(tmp_path, tokens)
    assert len(launched) == 1
    expected_args = ["entries/file", "entries/link"] if operands == ["entries/*"] else operands
    assert launched[0][1:] == ["trash", *(str(tmp_path) + "/" + p for p in expected_args)]
    assert f"(exit {status})" in result.splitlines()[0]
    assert "native stdout" in result and "native stderr" in result
    assert (tmp_path / "entries/file").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Requires the Linux XDG trash backend")
def test_native_gio_moves_files_and_directories_to_isolated_trash(
    tmp_path: Path, monkeypatch,
) -> None:
    # GLib uses a filesystem-level trash outside XDG_DATA_HOME on other devices.
    # Skip before invoking gio if the temporary directory would take that path.
    if tmp_path.stat().st_dev != Path.home().stat().st_dev:
        pytest.skip("Isolated XDG trash requires the same filesystem as the home directory")
    data_home = tmp_path / "data"
    data_home.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("GIO_USE_VFS", "local")
    monkeypatch.setenv("GIO_USE_PORTALS", "0")
    # Keep any forced portal fallback away from the desktop session.
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path={tmp_path}/no-session-bus")

    entries = tmp_path / "entries"
    (entries / "directory/.hidden").mkdir(parents=True)
    (entries / "directory/.hidden/nested.txt").write_text("nested contents")
    (entries / "file name.txt").write_text("file contents")
    target = tmp_path / "keep.txt"
    target.write_text("link target")
    (entries / "link").symlink_to(target)

    result = _invoke(
        tmp_path, [("gio", "CMD"), ("trash", "ARG"), ("entries/*", "PTH")],
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="entries/**", operations={Operation.DELETE})],
    )

    assert result.startswith("Overall: success (exit 0)"), result
    assert list(entries.iterdir()) == []
    trashed = data_home / "Trash/files"
    assert {p.name for p in trashed.iterdir()} == {"directory", "file name.txt", "link"}
    assert (trashed / "file name.txt").read_text() == "file contents"
    assert (trashed / "directory/.hidden/nested.txt").read_text() == "nested contents"
    assert (trashed / "link").is_symlink()
    assert (trashed / "link").readlink() == target
    assert target.read_text() == "link target"
    for name in ("directory", "file name.txt", "link"):
        info = (data_home / "Trash/info" / f"{name}.trashinfo").read_text()
        original = next(
            line.removeprefix("Path=")
            for line in info.splitlines() if line.startswith("Path=")
        )
        assert unquote(original) == str(entries / name)


def test_gio_no_paths_requires_delete_on_base(tmp_path: Path, monkeypatch) -> None:
    def unexpected(*args, **kwargs):
        pytest.fail("Denied base launched gio")

    monkeypatch.setattr(subprocess, "run", unexpected)
    result = _invoke(
        tmp_path, [("gio", "CMD"), ("trash", "ARG")], default_verdict=ActionVerdict.deny,
    )
    assert result.startswith("Overall: failure")
