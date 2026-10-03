"""Discovery uses native command behavior after checking explicit roots."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from roboz import Agent, runtime
from roboz.llm import MockLLMEndpoint
from roboz.models import Role
from roboz.runtime import EventPipe
from roboz.shed.models import ActionVerdict, Operation, PermissionRule
from roboz.shed.tools.cli_commands_v2 import TaggedFileCommand, get_run_tagged_file_command
from roboz.shed.tools.cli_commands_v2.command import resolve_tagged_command
from roboz.tools import stop


def _invoke(base: Path, tokens: list, **policy) -> str:
    tools = get_run_tagged_file_command(
        base=base,
        default_verdict=policy.pop("default_verdict", ActionVerdict.allow),
        **policy,
    )
    agent = Agent(
        name="discovery_test",
        system_prompt="Discover files.",
        tools=[*tools, stop],
        agent_endpoint=MockLLMEndpoint(responses=[
            {"action": "run_tagged_file_command", "rationale": "Discover", "value": tokens},
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


@pytest.mark.parametrize("command, arguments", [
    ("ls", []),
    ("ls", [("-lah", "FLG"), ("src", "PTH")]),
    ("ls", [("src", "PTH"), ("-1", "FLG"), ("-r", "FLG"), ("-r", "FLG")]),
    ("ls", [("-R", "FLG"), ("-A", "FLG"), ("src", "PTH")]),
    ("ls", [("-l", "FLG"), ("-1", "FLG"), ("src", "PTH")]),
    ("ls", [("-dR", "FLG"), ("src", "PTH")]),
    ("ls", [("--numeric-uid-gid", "FLG"), ("--size", "FLG"), ("src", "PTH")]),
    ("find", []),
    ("find", [("-P", "FLG"), ("--", "FLG"), ("src", "PTH")]),
    ("find", [("src", "PTH"), ("-type", "FLG"), ("f", "ARG"),
              ("-name", "FLG"), ("*.py", "ARG")]),
    ("find", [(".", "PTH"), ("-path", "FLG"), ("./src/*.py", "ARG")]),
    ("find", [("src", "PTH"), ("-mindepth", "FLG"), ("1", "ARG"),
              ("-maxdepth", "FLG"), ("1", "ARG"), ("-print0", "FLG")]),
    ("find", [("src", "PTH"), ("(", "FLG"), ("-iname", "FLG"), ("*.PY", "ARG"),
              ("-o", "FLG"), ("-empty", "FLG"), (")", "FLG"),
              ("!", "FLG"), ("-name", "FLG"), (".hidden*", "ARG")]),
    ("find", [("src", "PTH"), ("-name", "FLG"), ("nested", "ARG"),
              ("-prune", "FLG"), ("-o", "FLG"), ("-print", "FLG")]),
    ("find", [("src", "PTH"), ("-depth", "FLG"), ("-xdev", "FLG"),
              ("-type", "FLG"), ("f", "ARG"), ("-size", "FLG"), ("+0c", "ARG"),
              ("-mtime", "FLG"), ("-1", "ARG"), ("-mmin", "FLG"), ("-2", "ARG")]),
    ("find", [("src", "PTH"), ("-print", "FLG"), ("-quit", "FLG")]),
])
def test_discovery_output_matches_native_bytes(tmp_path: Path, command, arguments) -> None:
    (tmp_path / "src/nested").mkdir(parents=True)
    for name in ("a.py", "B.PY", ".hidden.py", "space name\n.py", "nested/deep.py"):
        (tmp_path / "src" / name).write_bytes(b"test\n")
    (tmp_path / "src/empty").touch()
    native = subprocess.run(
        [command, *(value for value, _ in arguments)],
        cwd=tmp_path, capture_output=True, check=True,
    )
    result = _invoke(tmp_path, [
        (command, "CMD"), *arguments,
        ("|", "CTL"), ("tee", "CMD"), (".captured", "PTH"),
    ])
    assert result.startswith("Overall: success")
    assert (tmp_path / ".captured").read_bytes() == native.stdout


@pytest.mark.parametrize("command", ["ls", "find"])
def test_roots_only_are_authorized_and_descendant_links_are_not_followed(
    tmp_path: Path, command: str,
) -> None:
    base = tmp_path / "base"
    (base / "src").mkdir(parents=True)
    (base / "src/denied.txt").write_text("private content")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside-only").touch()
    (base / "src/link").symlink_to(outside, target_is_directory=True)
    os.mkfifo(base / "src/pipe")
    (base / "src/hardlink").hardlink_to(base / "src/denied.txt")
    flags = [("-R", "FLG")] if command == "ls" else []
    result = _invoke(
        base, [(command, "CMD"), *flags, ("src", "PTH")],
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="src", operations={Operation.READ})],
        deny_rules=[PermissionRule(pattern="src/*", operations={Operation.READ})],
    )
    assert result.startswith("Overall: success")
    assert all(name in result for name in ("denied.txt", "link", "pipe", "hardlink"))
    assert "outside-only" not in result and "private content" not in result


@pytest.mark.parametrize("command", ["ls", "find"])
@pytest.mark.parametrize("denied", [False, True])
def test_root_checks_and_approvals_complete_before_execution(
    tmp_path: Path, monkeypatch, command, denied,
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    prompts = []
    launches = []
    native_run = subprocess.run

    def approve(message, **kwargs):
        assert launches == []
        prompts.append(message)
        return "yes"

    def record(*args, **kwargs):
        launches.append(args)
        return native_run(*args, **kwargs)

    monkeypatch.setattr(runtime, "interact_with_user", approve)
    monkeypatch.setattr(subprocess, "run", record)
    result = _invoke(
        tmp_path, [(command, "CMD"), ("a", "PTH"), ("a", "PTH"), ("b", "PTH")],
        deny_rules=[PermissionRule(pattern="b", operations={Operation.READ})] if denied else [],
        ask_rules=[PermissionRule(pattern="*", operations={Operation.READ})],
        pipe=EventPipe(),
    )
    assert result.startswith("Overall: success") is not denied
    assert len(prompts) == (0 if denied else 2)
    assert len(launches) == (0 if denied else 1)


@pytest.mark.parametrize("command", ["ls", "find"])
def test_default_base_requires_read_and_external_roots_need_absolute_rules(
    tmp_path: Path, command,
) -> None:
    base = tmp_path / "base"
    base.mkdir()
    assert _invoke(base, [(command, "CMD")], default_verdict=ActionVerdict.deny).startswith(
        "Overall: failure"
    )
    target = tmp_path / "external"
    target.touch()
    tokens = [(command, "CMD"), (str(target), "PTH")]
    for pattern, success in [("**", False), (str(target), True)]:
        result = _invoke(base, tokens, default_verdict=ActionVerdict.deny,
                         allow_rules=[PermissionRule(pattern=pattern, operations={Operation.READ})])
        assert result.startswith("Overall: success") is success


@pytest.mark.parametrize("command", ["ls", "find"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_explicit_unsupported_operands_fail_preparation(tmp_path: Path, command, kind) -> None:
    source = tmp_path / "source"
    source.touch()
    target = tmp_path / "target"
    if kind == "symlink":
        target.symlink_to(source)
    elif kind == "hardlink":
        target.hardlink_to(source)
    else:
        os.mkfifo(target)
    resolved = resolve_tagged_command(tmp_path)(
        TaggedFileCommand(value=[(command, "CMD"), ("target", "PTH")]), []
    )
    assert resolved.original_input.failure
    assert resolved.original_input.ready is None


@pytest.mark.parametrize("tokens", [
    [("ls", "CMD"), ("-lL", "FLG")],
    [("ls", "CMD"), ("--dereference", "FLG")],
    [("ls", "CMD"), ("--", "FLG"), ("-l", "FLG")],
    [("ls", "CMD"), ("file", "ARG")],
    [("find", "CMD"), ("-L", "FLG")],
    [("find", "CMD"), ("-follow", "FLG")],
    [("find", "CMD"), ("-exec", "FLG"), ("touch", "ARG")],
    [("find", "CMD"), ("-delete", "FLG")],
    [("find", "CMD"), ("-fprint", "FLG"), ("out", "ARG")],
    [("find", "CMD"), ("-files0-from", "FLG"), ("-", "ARG")],
    [("find", "CMD"), ("-newer", "FLG"), ("reference", "PTH")],
    [("find", "CMD"), ("-name", "FLG"), ("*.py", "PTH")],
    [("find", "CMD"), ("-name", "FLG")],
    [("find", "CMD"), ("-empty", "FLG"), ("late-root", "PTH")],
    [("find", "CMD"), ("-delete", "ARG")],
])
def test_unsupported_discovery_syntax_has_no_execution_payload(tmp_path: Path, tokens) -> None:
    resolved = resolve_tagged_command(tmp_path)(TaggedFileCommand(value=tokens), [])
    assert resolved.original_input.failure
    assert resolved.original_input.ready is None
    assert resolved.items == []


def test_predicate_option_text_is_literal_and_native_errors_control_fallback(
    tmp_path: Path,
) -> None:
    (tmp_path / "-delete").touch()
    result = _invoke(tmp_path, [
        ("find", "CMD"), ("-name", "FLG"), ("-delete", "ARG"),
        (";", "CTL"), ("find", "CMD"), ("(", "FLG"),
        ("||", "CTL"), ("ls", "CMD"), ("--", "FLG"), ("-delete", "PTH"),
    ])
    assert result.startswith("Overall: success")
    assert "./-delete" in result and "exit 1" in result
    assert (tmp_path / "-delete").exists()


def test_creation_and_discovery_are_resolved_lazily_with_raw_print0(tmp_path: Path) -> None:
    name = "new/a\nfile"
    result = _invoke(tmp_path, [
        ("mkdir", "CMD"), ("new", "PTH"), ("&&", "CTL"),
        ("touch", "CMD"), (name, "PTH"), ("&&", "CTL"),
        ("ls", "CMD"), ("new/*", "PTH"), (";", "CTL"),
        ("find", "CMD"), ("new", "PTH"), ("-type", "FLG"), ("f", "ARG"),
        ("-print0", "FLG"), ("|", "CTL"), ("tee", "CMD"), ("output", "PTH"),
    ])
    assert result.startswith("Overall: success")
    assert (tmp_path / "output").read_bytes() == os.fsencode(name) + b"\0"


@pytest.mark.parametrize("command, status", [("ls", 2), ("find", 1)])
def test_missing_literals_keep_native_status_and_unmatched_patterns_fail(
    tmp_path: Path, command, status,
) -> None:
    result = _invoke(tmp_path, [(command, "CMD"), ("missing", "PTH")])
    assert result.startswith(f"Overall: failure (exit {status})")
    result = _invoke(tmp_path, [(command, "CMD"), ("missing*", "PTH")])
    assert "Source pattern has no matches" in result


@pytest.mark.parametrize("root", ["-name", "!", "("])
def test_find_pth_cannot_become_an_expression(tmp_path: Path, root) -> None:
    (tmp_path / root).touch()
    result = _invoke(tmp_path, [("find", "CMD"), (root, "PTH")])
    assert result.startswith("Overall: success")
    assert f"./{root}" in result
