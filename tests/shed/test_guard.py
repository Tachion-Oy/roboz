"""Permission-guard semantics for overlapping allow / deny / ask rules.

These pin the deliberately *asymmetric* layering in ``resolve_allow_verdict``:
allow vs. deny is decided by ``takes_precedence``; the ask (prompt) step is then
layered only on top of an ``allow`` verdict. A deny is absolute - there is no
"conditional deny" the user can approve away. See
``docs/reference.md`` (Permission rule semantics) for the full description.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from roboz.shed.tools import GuardContext
from roboz.shed.models import (
    ActionVerdict,
    ApplyPatch,
    GuardDenyReason,
    Operation,
    PermissionRule,
)
from roboz.shed.tools.apply_patch import get_apply_patch
from roboz.shed.tools.guard import resolve_allow_verdict

from roboz.exceptions import UserInputUnavailableError
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

    with patch(
        "roboz.shed.tools.utils.interact_with_user", return_value="yes"
    ) as prompt:
        verdict, reason = resolve_allow_verdict(location, CREATE, ctx)

    # allow wins the overlap, then the prompt confirms -> allowed.
    assert verdict == ActionVerdict.allow
    assert reason is None
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

    with patch("roboz.shed.tools.utils.interact_with_user", return_value="no"):
        verdict, reason = resolve_allow_verdict(location, CREATE, ctx)

    # Allowed by policy, but the user declines the prompt -> denied (by user).
    assert verdict == ActionVerdict.deny
    assert reason == GuardDenyReason.USER_DECLINED


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

    with patch("roboz.shed.tools.utils.interact_with_user") as prompt:
        verdict, reason = resolve_allow_verdict(location, CREATE, ctx)

    assert verdict == ActionVerdict.deny
    assert reason == GuardDenyReason.POLICY_DENIED
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

    with patch("roboz.shed.tools.utils.interact_with_user") as prompt:
        verdict, reason = resolve_allow_verdict(location, CREATE, ctx)

    assert verdict == ActionVerdict.deny
    assert reason == GuardDenyReason.POLICY_DENIED
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
    entry, guard, _execute = get_apply_patch(
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
        with pytest.raises(UserInputUnavailableError):
            guard(prepared, [])
    finally:
        reset_output(token)

    assert target.read_text(encoding="utf-8") == "before"
