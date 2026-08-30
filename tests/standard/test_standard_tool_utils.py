from pathlib import Path
from unittest.mock import patch

import pytest

from roboz.runtime import EventPipe
from roboz.standard.models import (
    ActionVerdict,
    CommandReady,
    GuardDenyReason,
    GuardFileSingle,
    GuardFilesResult,
    GuardStatus,
    Operation,
    PermissionRule,
    RunFileCommands,
)
from roboz.standard.skills.cli_commands.runtime.guard import guard_operation
from roboz.standard.skills.cli_commands.runtime.types import GuardCtx, ResolvedFileCommand
from roboz.standard.skills.cli_commands.runtime.utils import (
    check_allow_deny_permission,
    check_ask_permission,
)


def _cli_ready(location: Path, base: Path) -> CommandReady:
    """Minimal CommandReady for testing guard_operation."""
    try:
        rel = location.relative_to(base)
    except ValueError:
        rel = Path(location.name)
    argv = ["cat", str(rel)]
    return CommandReady.model_validate(
        {
            "command_name": "cat",
            "argv": argv,
            "base_workdir": base.resolve(),
            "display_command": " ".join(argv),
        }
    )


def _original_input(location: Path, base: Path) -> RunFileCommands:
    """Minimal RunFileCommands matching _cli_ready for guarded input."""
    try:
        rel = location.relative_to(base)
    except ValueError:
        rel = Path(location.name)
    return RunFileCommands.model_validate(
        {
            "chain": "pipe",
            "file_commands": [{"command": "cat", "argv": [str(rel)]}],
        }
    )


def _original_input_multi(locations: list[Path], base: Path) -> RunFileCommands:
    """RunFileCommands for multiple guarded paths."""
    paths = []
    for loc in locations:
        try:
            rel = loc.relative_to(base)
        except ValueError:
            rel = Path(loc.name)
        paths.append(str(rel))
    return RunFileCommands.model_validate(
        {
            "chain": "pipe",
            "file_commands": [{"command": "cat", "argv": paths}],
        }
    )


def _assert_allow_result(
    result: object, expected_len: int | None = None
) -> GuardFilesResult:
    """Assert guard_operation returned an allow payload container."""
    assert isinstance(result, GuardFilesResult)
    assert result.status == GuardStatus.ALLOWED
    if expected_len is not None:
        assert len(result.items) == expected_len
    return result


def _assert_deny_result(
    result: object,
    *,
    expected_location: Path | None = None,
    expected_operation: Operation | None = None,
    expected_reason: GuardDenyReason = GuardDenyReason.POLICY_DENIED,
) -> GuardFilesResult:
    """Assert guard_operation returned a denied guard result."""
    assert isinstance(result, GuardFilesResult)
    assert result.status == GuardStatus.DENIED
    assert result.deny_reason == expected_reason
    assert result.message is not None
    if expected_reason == GuardDenyReason.USER_DECLINED:
        assert result.message == "Denied by user response to permission prompt."
    else:
        assert "DENIED" in result.message
    if expected_location is not None:
        assert str(expected_location) in result.message
    if expected_operation is not None:
        assert expected_operation in result.message
    return result


def test_check_allow_deny_permission_no_matching_rules_default_allow_when_allow_precedence(
    tmp_path: Path,
) -> None:
    """When no rules match and allow takes precedence, default is allow."""
    location = tmp_path / "test.txt"
    allow_rules: list[PermissionRule] = []
    deny_rules: list[PermissionRule] = []

    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.allow,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.allow,
    )

    assert result == ActionVerdict.allow


def test_check_allow_deny_permission_no_matching_rules_default_deny_when_deny_precedence(
    tmp_path: Path,
) -> None:
    """When no rules match and deny takes precedence, default is deny."""
    location = tmp_path / "test.txt"
    allow_rules: list[PermissionRule] = []
    deny_rules: list[PermissionRule] = []

    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.deny,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.deny,
    )

    assert result == ActionVerdict.deny


def test_check_allow_deny_permission_allow_rule_matches_returns_allow(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]
    deny_rules: list[PermissionRule] = []

    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.allow,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.allow,
        base_path=tmp_path,
    )

    assert result == ActionVerdict.allow


def test_check_allow_deny_permission_deny_rule_matches_returns_deny(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    allow_rules: list[PermissionRule] = []
    deny_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]

    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.allow,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.allow,
        base_path=tmp_path,
    )

    assert result == ActionVerdict.deny


def test_check_allow_deny_permission_both_match_allow_takes_precedence(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]
    deny_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]

    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.allow,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.allow,
        base_path=tmp_path,
    )

    assert result == ActionVerdict.allow


def test_check_allow_deny_permission_both_match_allow_takes_precedence_lazy(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern=lambda: "*.txt", operations={Operation.CREATE})
    ]
    deny_rules: list[PermissionRule] = [
        PermissionRule(pattern=lambda: "*.txt", operations={Operation.CREATE})
    ]

    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.allow,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.allow,
        base_path=tmp_path,
    )

    assert result == ActionVerdict.allow


def test_check_allow_deny_permission_both_match_deny_takes_precedence(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]
    deny_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]

    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.deny,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.deny,
        base_path=tmp_path,
    )

    assert result == ActionVerdict.deny


def test_check_allow_deny_permission_operation_type_matters(tmp_path: Path) -> None:
    location = tmp_path / "test.txt"
    location.touch()
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]
    deny_rules: list[PermissionRule] = []

    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.DELETE,
        takes_precedence=ActionVerdict.allow,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.allow,
        base_path=tmp_path,
    )

    assert result == ActionVerdict.allow


def test_check_ask_permission_no_ask_rules_returns_allow(tmp_path: Path) -> None:
    location = tmp_path / "test.txt"
    ask_rules: list[PermissionRule] = []

    result = check_ask_permission(
        location=location, op_type=Operation.CREATE, ask_rules=ask_rules
    )

    assert result == (ActionVerdict.allow, "no_prompt")


def test_check_ask_permission_rule_matches_but_output_none_raises(
    tmp_path: Path,
) -> None:
    """When ask rule matches and pipe has no output, raises ValueError."""
    location = tmp_path / "test.txt"
    ask_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]

    with pytest.raises(ValueError, match="Output missing"):
        check_ask_permission(
            location=location,
            op_type=Operation.CREATE,
            ask_rules=ask_rules,
            base_path=tmp_path,
            pipe=EventPipe(),
        )


def test_check_ask_permission_rule_matches_user_confirms_returns_allow(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    ask_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]

    pipe = EventPipe()
    with patch(
        "roboz.standard.skills.cli_commands.runtime.utils.interact_with_user", return_value="yes"
    ):
        result = check_ask_permission(
            location=location,
            op_type=Operation.CREATE,
            ask_rules=ask_rules,
            base_path=tmp_path,
            pipe=pipe,
        )

    assert result == (ActionVerdict.allow, "user_confirmed")


def test_check_ask_permission_rule_matches_user_declines_returns_deny(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    ask_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]

    pipe = EventPipe()
    with patch(
        "roboz.standard.skills.cli_commands.runtime.utils.interact_with_user", return_value="no"
    ):
        result = check_ask_permission(
            location=location,
            op_type=Operation.CREATE,
            ask_rules=ask_rules,
            base_path=tmp_path,
            pipe=pipe,
        )

    assert result == (ActionVerdict.deny, "user_declined")


def test_check_ask_permission_rule_matches_no_output_raises(
    tmp_path: Path,
) -> None:
    """When ask rule matches and pipe is omitted, raises ValueError."""
    location = tmp_path / "test.txt"
    ask_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]

    with pytest.raises(ValueError, match="Output missing"):
        check_ask_permission(
            location=location,
            op_type=Operation.CREATE,
            ask_rules=ask_rules,
            base_path=tmp_path,
        )


def test_check_ask_permission_rule_matches_user_reply_none_returns_deny(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    ask_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]

    pipe = EventPipe()
    with patch(
        "roboz.standard.skills.cli_commands.runtime.utils.interact_with_user", return_value=None
    ):
        result = check_ask_permission(
            location=location,
            op_type=Operation.CREATE,
            ask_rules=ask_rules,
            base_path=tmp_path,
            pipe=pipe,
        )

    assert result == (ActionVerdict.deny, "user_declined")


def test_check_ask_permission_rule_different_operation_returns_allow(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    ask_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.DELETE})
    ]

    result = check_ask_permission(
        location=location, op_type=Operation.CREATE, ask_rules=ask_rules
    )

    assert result == (ActionVerdict.allow, "no_prompt")


def test_check_ask_permission_rule_different_operation_returns_allow_lazy(
    tmp_path: Path,
) -> None:
    location = tmp_path / "test.txt"
    ask_rules: list[PermissionRule] = [
        PermissionRule(pattern=lambda: "*.txt", operations={Operation.DELETE})
    ]

    result = check_ask_permission(
        location=location, op_type=Operation.CREATE, ask_rules=ask_rules
    )

    assert result == (ActionVerdict.allow, "no_prompt")


def test_check_allow_deny_permission_outside_base_uses_default_when_no_rule_matches(
    tmp_path: Path,
) -> None:
    """Paths outside base_path are not blocked by geometry; no matching rule uses default."""
    base_path = tmp_path / "workspace"
    base_path.mkdir()
    location = tmp_path / "outside.txt"
    location.write_text("nope")
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern="*.txt", operations={Operation.CREATE})
    ]
    deny_rules: list[PermissionRule] = []
    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.allow,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.allow,
        base_path=base_path,
    )
    assert result == ActionVerdict.allow


def test_check_allow_deny_permission_absolute_allow_rule_matches_outside_base(
    tmp_path: Path,
) -> None:
    """Absolute allow rules now route to full-path matching, so they match regardless of base."""
    base_path = tmp_path / "workspace"
    base_path.mkdir()
    location = tmp_path / "outside.txt"
    location.write_text("nope")
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern=str(location.resolve()), operations={Operation.CREATE})
    ]
    deny_rules: list[PermissionRule] = []
    result = check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.deny,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.deny,
        base_path=base_path,
    )
    assert result == ActionVerdict.allow


def test_check_allow_deny_permission_absolute_allow_rule_matches_inside_base(
    tmp_path: Path,
) -> None:
    """Absolute allow rule for a path inside the base now matches via full-path routing."""
    base_path = tmp_path / "workspace"
    base_path.mkdir()
    location = base_path / "inside.txt"
    location.write_text("ok")
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern=str(location.resolve()), operations={Operation.CREATE})
    ]
    deny_rules: list[PermissionRule] = [
        PermissionRule(pattern="**", operations={Operation.CREATE})
    ]
    # allow (absolute, full-path match) takes precedence over deny (relative, base match)
    assert ActionVerdict.allow == check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.allow,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.deny,
        base_path=base_path,
    )
    # when deny takes precedence, deny wins
    assert ActionVerdict.deny == check_allow_deny_permission(
        location=location,
        op_type=Operation.CREATE,
        takes_precedence=ActionVerdict.deny,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        default_verdict=ActionVerdict.deny,
        base_path=base_path,
    )


def test_guard_operation_allows_create_non_existent_location(tmp_path: Path) -> None:
    """CREATE on non-existent path should be allowed (that's what create means)."""
    location = tmp_path / "new_file.txt"
    tool = guard_operation(
        ctx=GuardCtx(
            **{
                "base": tmp_path,
                "takes_precedence": ActionVerdict.allow,
                "default_verdict": ActionVerdict.allow,
                "allow": [
                    PermissionRule(pattern="*.txt", operations={Operation.CREATE})
                ],
                "deny": [],
                "ask": [],
            }
        )
    )
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=location,
                    value=_cli_ready(location, tmp_path),
                )
            ],
        ),
        messages=[],
    )

    allow_result = _assert_allow_result(result, expected_len=1)
    assert allow_result.items[0].verdict == ActionVerdict.allow
    assert allow_result.items[0].location == location


def test_guard_operation_denies_create_when_location_is_directory(
    tmp_path: Path,
) -> None:
    """CREATE should be denied when path exists and is a directory."""
    location = tmp_path / "adir"
    location.mkdir()
    tool = guard_operation(
        ctx=GuardCtx(
            **{
                "base": tmp_path,
                "takes_precedence": ActionVerdict.deny,
                "default_verdict": ActionVerdict.deny,
                "allow": [
                    PermissionRule(pattern="*.txt", operations={Operation.CREATE})
                ],
                "deny": [],
                "ask": [],
            }
        )
    )
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=location,
                    value=_cli_ready(location, tmp_path),
                )
            ],
        ),
        messages=[],
    )

    _assert_deny_result(
        result,
        expected_location=location,
        expected_operation=Operation.CREATE,
    )


def test_guard_operation_allows_create_overwrite_without_delete_permission_when_default_is_allow(
    tmp_path: Path,
) -> None:
    """With precedence-default logic, missing DELETE falls back to allow here.

    Touch creates the file; tee writes stdin to it, overwriting. The guard checks
    overwrite as CREATE + implicit DELETE. If DELETE has no matching allow/deny
    rule and precedence is allow, the verdict defaults to allow.
    """
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()
    location = plans_dir / "plan_123.md"
    location.touch()  # simulate touch having created it
    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [
                PermissionRule(pattern="**", operations={Operation.READ}),
                PermissionRule(pattern="plans/*.md", operations={Operation.CREATE}),
            ],
            "deny": [PermissionRule(pattern="**", operations={Operation.CREATE})],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=location,
                    value=_cli_ready(location, tmp_path),
                )
            ],
        ),
        messages=[],
    )

    allow_result = _assert_allow_result(result, expected_len=1)
    assert allow_result.items[0].verdict == ActionVerdict.allow


def test_guard_operation_allows_create_overwrite_when_delete_permitted(
    tmp_path: Path,
) -> None:
    """When both CREATE and DELETE are allowed, overwrite (tee to existing) succeeds."""
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()
    location = plans_dir / "plan_123.md"
    location.write_text("initial")
    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [
                PermissionRule(
                    pattern="plans/*.md",
                    operations={Operation.CREATE, Operation.DELETE},
                )
            ],
            "deny": [],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=location,
                    value=_cli_ready(location, tmp_path),
                )
            ],
        ),
        messages=[],
    )

    allow_result = _assert_allow_result(result, expected_len=1)
    assert allow_result.items[0].verdict == ActionVerdict.allow


def test_guard_operation_overwrite_allows_existing_file(tmp_path: Path) -> None:
    location = tmp_path / "existing.txt"
    location.write_text("original")
    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [PermissionRule(pattern="*.txt", operations={Operation.DELETE})],
            "deny": [],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.DELETE,
                    location=location,
                    value=_cli_ready(location, tmp_path),
                )
            ],
        ),
        messages=[],
    )

    allow_result = _assert_allow_result(result, expected_len=1)
    assert allow_result.items[0].verdict == ActionVerdict.allow


def test_guard_operation_base_path_allows_file_outside_when_rules_default_allow(
    tmp_path: Path,
) -> None:
    """Outside-base paths are not auto-denied; no matching rule uses precedence default."""
    location = tmp_path / "outside.txt"
    base_path = tmp_path / "workspace"
    base_path.mkdir()
    ctx = GuardCtx(
        **{
            "base": base_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [PermissionRule(pattern="*.txt", operations={Operation.CREATE})],
            "deny": [],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=location,
                    value=_cli_ready(location, tmp_path),
                )
            ],
        ),
        messages=[],
    )

    allow_result = _assert_allow_result(result, expected_len=1)
    assert allow_result.items[0].verdict == ActionVerdict.allow


def test_guard_operation_base_path_allows_file_within(tmp_path: Path) -> None:
    """Paths inside base_path pass the boundary check and are evaluated by rules."""
    base_path = tmp_path / "workspace"
    base_path.mkdir()
    location = base_path / "inside.txt"
    location.write_text("seed")
    ctx = GuardCtx(
        **{
            "base": base_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [
                PermissionRule(
                    pattern="*.txt", operations={Operation.CREATE, Operation.DELETE}
                )
            ],
            "deny": [],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, base_path),
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=location,
                    value=_cli_ready(location, base_path),
                )
            ],
        ),
        messages=[],
    )

    allow_result = _assert_allow_result(result, expected_len=1)
    assert allow_result.items[0].verdict == ActionVerdict.allow


def test_guard_operation_deny_verdict_modifies_message(tmp_path: Path) -> None:
    location = tmp_path / "test.txt"
    location.write_text("seed")
    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.deny,
            "default_verdict": ActionVerdict.deny,
            "allow": [],
            "deny": [PermissionRule(pattern="*.txt", operations={Operation.CREATE})],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=location,
                    value=_cli_ready(location, tmp_path),
                )
            ],
        ),
        messages=[],
    )

    _assert_deny_result(
        result,
        expected_location=location,
        expected_operation=Operation.CREATE,
    )


def test_guard_operation_deny_includes_constraints_from_ctx(tmp_path: Path) -> None:
    """Guard denial message includes allowed paths and permissions formatted from ctx."""
    location = tmp_path / "test.txt"
    location.write_text("seed")
    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.deny,
            "default_verdict": ActionVerdict.deny,
            "allow": [],
            "deny": [PermissionRule(pattern="*.txt", operations={Operation.CREATE})],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=location,
                    value=_cli_ready(location, tmp_path),
                )
            ],
        ),
        messages=[],
    )

    deny = _assert_deny_result(
        result,
        expected_location=location,
        expected_operation=Operation.CREATE,
    )
    message = deny.message
    assert message is not None
    assert "Tool constraints" in message
    assert "Working directory" in message


def test_guard_operation_allow_verdict_preserves_content(tmp_path: Path) -> None:
    location = tmp_path / "test.txt"
    location.write_text("seed")
    cli_payload = _cli_ready(location, tmp_path)
    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [
                PermissionRule(
                    pattern="*.txt", operations={Operation.CREATE, Operation.DELETE}
                )
            ],
            "deny": [],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input(location, tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=location,
                    value=cli_payload,
                )
            ],
        ),
        messages=[],
    )

    allow_result = _assert_allow_result(result, expected_len=1)
    assert allow_result.items[0].verdict == ActionVerdict.allow
    assert allow_result.items[0].value == cli_payload


def test_guard_operation_ask_permission_raises_when_output_none(
    tmp_path: Path,
) -> None:
    """When ask rule matches and guard ctx has no usable pipe output, raises ValueError."""
    # Use non-existent file so we reach check_ask_permission (CREATE on existing
    # file triggers DELETE check first, which can deny before we get to ask)
    location = tmp_path / "new_file.txt"
    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [
                PermissionRule(pattern="**/*.txt", operations={Operation.CREATE})
            ],
            "deny": [],
            "ask": [PermissionRule(pattern="**/*.txt", operations={Operation.CREATE})],
            "pipe": EventPipe(),
        }
    )

    tool = guard_operation(ctx=ctx)
    with pytest.raises(ValueError, match="Output missing"):
        tool(
            input=ResolvedFileCommand(
                original_input=_original_input(location, tmp_path),
                items=[
                    GuardFileSingle(
                        operation=Operation.CREATE,
                        location=location,
                        value=_cli_ready(location, tmp_path),
                    )
                ],
            ),
            messages=[],
        )


def test_guard_operation_full_permission_flow_user_confirms(tmp_path: Path) -> None:
    location = tmp_path / "test.txt"
    location.write_text("seed")
    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [
                PermissionRule(
                    pattern="*.txt", operations={Operation.CREATE, Operation.DELETE}
                )
            ],
            "deny": [],
            "ask": [
                PermissionRule(
                    pattern="*.txt", operations={Operation.CREATE, Operation.DELETE}
                )
            ],
            "pipe": EventPipe(),
        }
    )

    with patch(
        "roboz.standard.skills.cli_commands.runtime.utils.interact_with_user", return_value="yes"
    ):
        tool = guard_operation(ctx=ctx)
        result = tool(
            input=ResolvedFileCommand(
                original_input=_original_input(location, tmp_path),
                items=[
                    GuardFileSingle(
                        operation=Operation.CREATE,
                        location=location,
                        value=_cli_ready(location, tmp_path),
                    )
                ],
            ),
            messages=[],
        )

    allow_result = _assert_allow_result(result, expected_len=1)
    assert allow_result.items[0].verdict == ActionVerdict.allow


def test_guard_operation_full_permission_flow_user_declines(tmp_path: Path) -> None:
    location = tmp_path / "test_decline.txt"
    location.write_text("seed")
    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [
                PermissionRule(
                    pattern="*.txt", operations={Operation.CREATE, Operation.DELETE}
                )
            ],
            "deny": [],
            "ask": [
                PermissionRule(
                    pattern="*.txt", operations={Operation.CREATE, Operation.DELETE}
                )
            ],
            "pipe": EventPipe(),
        }
    )

    with patch(
        "roboz.standard.skills.cli_commands.runtime.utils.interact_with_user", return_value="no"
    ):
        tool = guard_operation(ctx=ctx)
        result = tool(
            input=ResolvedFileCommand(
                original_input=_original_input(location, tmp_path),
                items=[
                    GuardFileSingle(
                        operation=Operation.CREATE,
                        location=location,
                        value=_cli_ready(location, tmp_path),
                    )
                ],
            ),
            messages=[],
        )

    deny = _assert_deny_result(result, expected_reason=GuardDenyReason.USER_DECLINED)
    assert deny.message == "Denied by user response to permission prompt."


def test_guard_operation_multiple_files_all_allowed(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("a")
    second.write_text("b")

    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.allow,
            "default_verdict": ActionVerdict.allow,
            "allow": [PermissionRule(pattern="*.txt", operations={Operation.READ})],
            "deny": [],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input_multi([first, second], tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.READ,
                    location=first,
                    value=_cli_ready(first, tmp_path),
                ),
                GuardFileSingle(
                    operation=Operation.READ,
                    location=second,
                    value=_cli_ready(second, tmp_path),
                ),
            ],
        ),
        messages=[],
    )

    allow_result = _assert_allow_result(result, expected_len=2)
    assert allow_result.items[0].location == first
    assert allow_result.items[1].location == second
    assert allow_result.items[0].verdict == ActionVerdict.allow
    assert allow_result.items[1].verdict == ActionVerdict.allow


def test_guard_operation_short_circuits_on_first_deny(tmp_path: Path) -> None:
    not_a_file = tmp_path / "folder"
    not_a_file.mkdir()
    second_file = tmp_path / "second.txt"
    second_file.write_text("ok")

    ctx = GuardCtx(
        **{
            "base": tmp_path,
            "takes_precedence": ActionVerdict.deny,
            "default_verdict": ActionVerdict.deny,
            "allow": [PermissionRule(pattern="*.txt", operations={Operation.READ})],
            "deny": [],
            "ask": [],
        }
    )

    tool = guard_operation(ctx=ctx)
    result = tool(
        input=ResolvedFileCommand(
            original_input=_original_input_multi([not_a_file, second_file], tmp_path),
            items=[
                GuardFileSingle(
                    operation=Operation.READ,
                    location=not_a_file,
                    value=_cli_ready(not_a_file, tmp_path),
                ),
                GuardFileSingle(
                    operation=Operation.READ,
                    location=second_file,
                    value=_cli_ready(second_file, tmp_path),
                ),
            ],
        ),
        messages=[],
    )

    # New contract: guard short-circuits immediately on first deny.
    _assert_deny_result(
        result,
        expected_location=not_a_file,
        expected_operation=Operation.READ,
    )
