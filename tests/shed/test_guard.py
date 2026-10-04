"""All guarded tools apply policy precedence before requesting approvals."""

from pathlib import Path
from unittest.mock import patch


from roboz.shed.tools import GuardContext
from roboz.shed.models import (
    ActionVerdict,
    ApplyPatch,
    GuardDenyReason,
    GuardFileSingle,
    GuardStatus,
    Operation,
    PermissionRule,
)
from roboz.shed.tools.apply_patch import get_apply_patch
from roboz.shed.tools.guard import guard_items
from roboz.models import Str

from roboz.runtime.io import (
    bind_output,
    reset_output,
)
from roboz.runtime.pipe import EventPipe

CREATE = Operation.CREATE
PATTERN = "shared/**"


def _ctx(
    *,
    base: Path,
    takes_precedence: ActionVerdict,
    allow: list[PermissionRule],
    deny: list[PermissionRule],
    ask: list[PermissionRule],
) -> GuardContext:
    return GuardContext(
        base=base,
        takes_precedence=takes_precedence,
        allow=allow,
        deny=deny,
        ask=ask,
        default_verdict=ActionVerdict.deny,
        pipe=EventPipe(),
    )


def test_overlapping_allow_and_deny_allow_precedence_then_prompt_yes_allows(
    tmp_path: Path,
) -> None:
    # Same location matches an allow rule, a deny rule, and an ask rule.
    ctx = _ctx(
        base=tmp_path,
        takes_precedence=ActionVerdict.allow,
        allow=[PermissionRule(pattern=PATTERN, operations={CREATE})],
        deny=[PermissionRule(pattern=PATTERN, operations={CREATE})],
        ask=[PermissionRule(pattern=PATTERN, operations={CREATE})],
    )
    location = tmp_path / "shared" / "f.md"  # not created: skip overwrite sub-check

    with patch("roboz.runtime.interact_with_user", return_value="yes") as prompt:
        result = guard_items(
            items_to_guard=[
                GuardFileSingle(
                    operation=CREATE, location=location, value=Str(value="payload")
                )
            ],
            original_input=Str(value="request"),
            ctx=ctx,
        )

    # allow wins the overlap, then the prompt confirms -> allowed.
    assert result.status == GuardStatus.ALLOWED
    assert result.deny_reason is None
    prompt.assert_called_once()


def test_overlapping_allow_and_deny_allow_precedence_then_prompt_no_denies(
    tmp_path: Path,
) -> None:
    ctx = _ctx(
        base=tmp_path,
        takes_precedence=ActionVerdict.allow,
        allow=[PermissionRule(pattern=PATTERN, operations={CREATE})],
        deny=[PermissionRule(pattern=PATTERN, operations={CREATE})],
        ask=[PermissionRule(pattern=PATTERN, operations={CREATE})],
    )
    location = tmp_path / "shared" / "f.md"

    with patch("roboz.runtime.interact_with_user", return_value="no"):
        result = guard_items(
            items_to_guard=[
                GuardFileSingle(
                    operation=CREATE, location=location, value=Str(value="payload")
                )
            ],
            original_input=Str(value="request"),
            ctx=ctx,
        )

    # Allowed by policy, but the user declines the prompt -> denied (by user).
    assert result.status == GuardStatus.DENIED
    assert result.deny_reason == GuardDenyReason.USER_DECLINED


def test_deny_is_absolute_ask_rule_never_prompts(tmp_path: Path) -> None:
    # The asymmetry: a denied location that *also* has an ask rule is NOT
    # promptable. The deny short-circuits before the ask step ever runs, so the
    # user is never asked to approve it.
    ctx = _ctx(
        base=tmp_path,
        takes_precedence=ActionVerdict.allow,
        allow=[],
        deny=[PermissionRule(pattern=PATTERN, operations={CREATE})],
        ask=[PermissionRule(pattern=PATTERN, operations={CREATE})],
    )
    location = tmp_path / "shared" / "f.md"

    with patch("roboz.runtime.interact_with_user") as prompt:
        result = guard_items(
            items_to_guard=[
                GuardFileSingle(
                    operation=CREATE, location=location, value=Str(value="payload")
                )
            ],
            original_input=Str(value="request"),
            ctx=ctx,
        )

    assert result.status == GuardStatus.DENIED
    assert result.deny_reason == GuardDenyReason.POLICY_DENIED
    prompt.assert_not_called()


def test_overlap_with_deny_precedence_never_prompts(tmp_path: Path) -> None:
    # When deny wins the overlap by precedence, the ask step is unreachable too.
    ctx = _ctx(
        base=tmp_path,
        takes_precedence=ActionVerdict.deny,
        allow=[PermissionRule(pattern=PATTERN, operations={CREATE})],
        deny=[PermissionRule(pattern=PATTERN, operations={CREATE})],
        ask=[PermissionRule(pattern=PATTERN, operations={CREATE})],
    )
    location = tmp_path / "shared" / "f.md"

    with patch("roboz.runtime.interact_with_user") as prompt:
        result = guard_items(
            items_to_guard=[
                GuardFileSingle(
                    operation=CREATE, location=location, value=Str(value="payload")
                )
            ],
            original_input=Str(value="request"),
            ctx=ctx,
        )

    assert result.status == GuardStatus.DENIED
    assert result.deny_reason == GuardDenyReason.POLICY_DENIED
    prompt.assert_not_called()


def test_autonomous_approval_failure_leaves_operation_unexecuted(
    tmp_path: Path,
) -> None:
    target = tmp_path / "shared" / "f.md"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")
    rules = [
        PermissionRule(
            pattern=PATTERN,
            operations={CREATE, Operation.READ, Operation.DELETE},
        )
    ]
    entry, guard, execute = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=rules,
        ask_rules=[PermissionRule(pattern=PATTERN, operations={CREATE})],
        pipe=EventPipe(),
    )
    prepared = entry(
        ApplyPatch(
            path="shared/f.md",
            old_string="before",
            new_string="after",
            replace_all=False,
        ),
        [],
    )
    token = bind_output(None)
    try:
        result = guard(prepared, [])
        assert result.status == GuardStatus.DENIED
        assert "Approval unavailable" in result.message
        assert not execute.chain_condition(result)
    finally:
        reset_output(token)

    assert target.read_text(encoding="utf-8") == "before"
