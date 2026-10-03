"""Tagged mkdir guards every prospective directory before native execution."""

import json
import os
from pathlib import Path

import pytest

from roboz import Agent, runtime
from roboz.llm import MockLLMEndpoint
from roboz.models import Role
from roboz.runtime import EventPipe
from roboz.shed.models import ActionVerdict, Operation, PermissionRule
from roboz.shed.tools.cli_commands_v2 import (
    TaggedFileCommand,
    get_run_tagged_file_command,
)
from roboz.shed.tools.cli_commands_v2.command import resolve_tagged_command
from roboz.tools import stop


def _invoke(base: Path, tokens: list, **policy) -> str:
    tools = get_run_tagged_file_command(
        base=base,
        default_verdict=policy.pop("default_verdict", ActionVerdict.allow),
        **policy,
    )
    agent = Agent(
        name="mkdir_test",
        system_prompt="Create the requested directories.",
        tools=[*tools, stop],
        agent_endpoint=MockLLMEndpoint(
            responses=[
                {
                    "action": "run_tagged_file_command",
                    "rationale": "Create directories",
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
        if response.get("caller") == "execute_tagged_file_command"
    )


def _resolve(base: Path, tokens: list):
    return resolve_tagged_command(base)(TaggedFileCommand(value=tokens), [])


@pytest.mark.parametrize(
    "flags, targets, success, created",
    [
        ([], ["a", "a/b"], True, ["a", "a/b"]),
        ([], ["a", "a", "b"], False, ["a", "b"]),
        (["-p"], ["a/b", "a/b", "a/c"], True, ["a", "a/b", "a/c"]),
        (["--parents"], ["a/../b"], True, ["a", "b"]),
        (["-p"], ["a/."], True, ["a"]),
        ([], ["a/."], False, []),
        ([], ["a/"], True, ["a"]),
        ([], ["a/../b"], False, []),
        (
            ["--"],
            ["-", "--parents", " spaced ", "back\\slash", "&&"],
            True,
            ["-", "--parents", " spaced ", "back\\slash", "&&"],
        ),
    ],
)
def test_mkdir_preserves_native_order_and_path_spelling(
    tmp_path: Path, flags, targets, success, created
) -> None:
    tokens = [
        ("mkdir", "CMD"),
        *((flag, "FLG") for flag in flags),
        *((target, "PTH") for target in targets),
    ]
    resolved = _resolve(tmp_path, tokens)
    assert resolved.original_input.failure is None
    assert resolved.original_input.ready.argv == [
        "mkdir",
        *flags,
        *(os.path.join(str(tmp_path), target) for target in targets),
    ]
    result = _invoke(tmp_path, tokens)
    assert result.startswith("Overall: success") is success
    assert sorted(
        str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")
    ) == sorted(created)


def test_mkdir_permissions_include_missing_prefixes_before_dotdot(
    tmp_path: Path,
) -> None:
    (tmp_path / "existing").mkdir()
    tokens = [
        ("mkdir", "CMD"),
        ("-p", "FLG"),
        ("new/child", "PTH"),
        ("./new/child", "PTH"),
        ("existing", "PTH"),
        ("a/../b", "PTH"),
    ]
    resolved = _resolve(tmp_path, tokens)
    assert [
        (item.operation, item.location.relative_to(tmp_path).as_posix())
        for item in resolved.items
    ] == [
        (Operation.CREATE, name) for name in ("new", "new/child", "existing", "a", "b")
    ]
    assert list(tmp_path.iterdir()) == [tmp_path / "existing"]


def test_mkdir_existing_intermediate_directories_need_no_permission(
    tmp_path: Path,
) -> None:
    (tmp_path / "existing").mkdir()
    policy = {
        "default_verdict": ActionVerdict.deny,
        "allow_rules": [
            PermissionRule(pattern="existing/new", operations={Operation.CREATE})
        ],
    }
    result = _invoke(
        tmp_path,
        [("mkdir", "CMD"), ("-p", "FLG"), ("existing/new", "PTH")],
        **policy,
    )
    assert result.startswith("Overall: success")
    assert (tmp_path / "existing/new").is_dir()
    # Explicit existing targets still need CREATE, even for an otherwise silent no-op.
    result = _invoke(
        tmp_path,
        [("mkdir", "CMD"), ("-p", "FLG"), ("existing", "PTH")],
        **policy,
    )
    assert f"Denied {Operation.CREATE}" in result


def test_mkdir_absolute_target_uses_absolute_permissions(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    target = tmp_path / "outside/child"
    result = _invoke(
        base,
        [("mkdir", "CMD"), ("-p", "FLG"), (str(target), "PTH")],
        default_verdict=ActionVerdict.deny,
        allow_rules=[
            PermissionRule(
                pattern=str(target.parent) + "/**", operations={Operation.CREATE}
            )
        ],
    )
    assert result.startswith("Overall: success")
    assert target.is_dir()


@pytest.mark.parametrize("blocked", ["a", "new", "new/child"])
def test_mkdir_denial_prevents_all_creations_and_approvals(
    tmp_path: Path, monkeypatch, blocked
) -> None:
    def unexpected_prompt(*args, **kwargs):
        pytest.fail("A policy denial must prevent every approval prompt")

    monkeypatch.setattr(runtime, "interact_with_user", unexpected_prompt)
    result = _invoke(
        tmp_path,
        [("mkdir", "CMD"), ("-p", "FLG"), ("safe", "PTH"), ("a/../new/child", "PTH")],
        deny_rules=[PermissionRule(pattern=blocked, operations={Operation.CREATE})],
        ask_rules=[PermissionRule(pattern="**", operations={Operation.CREATE})],
        pipe=EventPipe(),
    )
    assert f"Denied {Operation.CREATE}" in result
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("reply", ["yes", "no", None])
def test_mkdir_approvals_are_deduplicated_and_complete_before_creation(
    tmp_path: Path, monkeypatch, reply
) -> None:
    prompts = []

    def approve(message: str, *, with_reply: bool) -> str:
        assert list(tmp_path.iterdir()) == []
        prompts.append(message)
        return reply

    monkeypatch.setattr(runtime, "interact_with_user", approve)
    result = _invoke(
        tmp_path,
        [("mkdir", "CMD"), ("-p", "FLG"), ("new/child", "PTH"), ("./new/child", "PTH")],
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="new/**", operations={Operation.CREATE})],
        ask_rules=[PermissionRule(pattern="new/**", operations={Operation.CREATE})],
        pipe=EventPipe() if reply is not None else None,
    )
    assert result.startswith("Overall: success") is (reply == "yes")
    assert len(prompts) == {"yes": 2, "no": 1, None: 0}[reply]
    assert (tmp_path / "new").exists() is (reply == "yes")


@pytest.mark.parametrize(
    "kind, target",
    [
        ("link", "bad/../target"),
        ("link", "missing/../bad/child"),
        ("dangling", "bad"),
        ("file", "bad/../target"),
        ("file", "missing/../bad/child"),
        ("fifo", "bad"),
    ],
)
def test_mkdir_unsupported_entries_reject_all_operands(
    tmp_path: Path, kind, target
) -> None:
    bad = tmp_path / "bad"
    if kind == "link":
        bad.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "dangling":
        bad.symlink_to(tmp_path / "absent")
    elif kind == "file":
        bad.write_text("unchanged")
    else:
        os.mkfifo(bad)
    result = _invoke(
        tmp_path,
        [("mkdir", "CMD"), ("-p", "FLG"), ("safe", "PTH"), (target, "PTH")],
    )
    assert result.startswith("Overall: failure (exit 1)")
    assert list(tmp_path.iterdir()) == [bad]


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        [("target", "ARG")],
        [("-m", "FLG"), ("700", "ARG"), ("target", "PTH")],
        [("-Z", "FLG"), ("target", "PTH")],
        [("-pv", "FLG"), ("target", "PTH")],
        [("--parents=yes", "FLG"), ("target", "PTH")],
        [("-p", "FLG"), ("--parents", "FLG"), ("target", "PTH")],
        [("-v", "FLG"), ("--verbose", "FLG"), ("target", "PTH")],
        [("target", "PTH"), ("-p", "FLG")],
        [("--", "FLG"), ("-p", "FLG"), ("target", "PTH")],
        [("new/*", "PTH")],
        [("new/?", "PTH")],
        [("new/[ab]", "PTH")],
    ],
)
def test_mkdir_argument_errors_leave_no_executable_payload(
    tmp_path: Path, arguments
) -> None:
    resolved = _resolve(tmp_path, [("mkdir", "CMD"), *arguments])
    assert resolved.original_input.failure
    assert resolved.original_input.ready is None
    assert resolved.items == []


@pytest.mark.parametrize("flag", ["-v", "--verbose"])
def test_mkdir_verbose_output_can_be_piped(tmp_path: Path, flag) -> None:
    result = _invoke(
        tmp_path,
        [
            ("mkdir", "CMD"),
            ("-p", "FLG"),
            (flag, "FLG"),
            ("new/child", "PTH"),
            ("|", "CTL"),
            ("tee", "CMD"),
            ("log", "PTH"),
        ],
    )
    assert result.startswith("Overall: success")
    lines = (tmp_path / "log").read_text().splitlines()
    assert len(lines) == 2
    assert str(tmp_path / "new") in lines[0]
    assert str(tmp_path / "new/child") in lines[1]


def test_mkdir_enables_dependent_touch_and_tee(tmp_path: Path) -> None:
    result = _invoke(
        tmp_path,
        [
            ("mkdir", "CMD"),
            ("-p", "FLG"),
            ("notes/daily", "PTH"),
            ("&&", "CTL"),
            ("touch", "CMD"),
            ("notes/daily/today.txt", "PTH"),
            ("&&", "CTL"),
            ("tee", "CMD"),
            ("Hello\n", "ARG"),
            ("notes/daily/today.txt", "PTH"),
        ],
    )
    assert result.startswith("Overall: success")
    assert (tmp_path / "notes/daily/today.txt").read_text() == "Hello\n"


def test_mkdir_native_failure_keeps_partial_effects_and_selects_fallback(
    tmp_path: Path,
) -> None:
    result = _invoke(
        tmp_path,
        [
            ("mkdir", "CMD"),
            ("safe", "PTH"),
            ("missing/child", "PTH"),
            ("&&", "CTL"),
            ("touch", "CMD"),
            ("skipped", "PTH"),
            ("||", "CTL"),
            ("mkdir", "CMD"),
            ("fallback", "PTH"),
        ],
    )
    assert result.startswith("Overall: success")
    assert "exit 1" in result
    assert sorted(path.name for path in tmp_path.iterdir()) == ["fallback", "safe"]
