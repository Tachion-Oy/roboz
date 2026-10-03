"""Authorize explicit transfer requirements before requesting any approvals."""

from pathlib import Path, PurePosixPath

from roboz import runtime
from roboz.exceptions import UserInputUnavailableError
from roboz.models import Message
from roboz.models.truncation import Severity, Truncation
from roboz.shed.models import (
    ActionVerdict,
    CommandReady,
    GuardDenyReason,
    GuardFileSingle,
    GuardFileSingleResult,
    GuardFilesResult,
    GuardStatus,
    PermissionRule,
)
from roboz.shed.tools.contexts import GuardContext
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.tooling.decorators import factory

from .contracts import CommandExecution


def _matches(
    rules: list[PermissionRule], item: GuardFileSingle[CommandReady], base: Path
) -> bool:
    """Match literal POSIX names without stripping spaces or changing separators."""
    for rule in rules:
        if item.operation not in rule.operations:
            continue
        pattern = rule.pattern if isinstance(rule.pattern, str) else rule.pattern()
        path = item.location
        if not pattern.startswith("/"):
            try:
                path = path.relative_to(base)
            except ValueError:
                continue
        if pattern.endswith("/**") and path.as_posix() == pattern[:-3]:
            return True
        if PurePosixPath(path).full_match(pattern):
            return True
    return False


def _policy_verdict(allow: bool, deny: bool, ctx: GuardContext) -> ActionVerdict:
    if allow and deny:
        return ctx.takes_precedence
    if allow:
        return ActionVerdict.allow
    if deny:
        return ActionVerdict.deny
    return ctx.default_verdict


def _denied(
    input: ResolvedFileCommand[CommandExecution, CommandReady],
    item: GuardFileSingle[CommandReady],
    message: str,
    reason: GuardDenyReason = GuardDenyReason.POLICY_DENIED,
) -> GuardFilesResult[CommandExecution, CommandReady]:
    return GuardFilesResult(
        status=GuardStatus.DENIED,
        deny_reason=reason,
        message=message,
        original_input=input.original_input,
        items=[
            GuardFileSingleResult(
                verdict=ActionVerdict.deny, location=item.location, value=item.value
            )
        ],
        truncation=Truncation(threshold=0, severity=Severity.LIGHT),
    )


def _check_policy(
    input: ResolvedFileCommand[CommandExecution, CommandReady],
    ctx: GuardContext,
    base: Path,
) -> GuardFilesResult[CommandExecution, CommandReady] | None:
    """Return the first policy denial without requesting approval."""
    for item in input.items:
        allow = _matches(ctx.allow, item, base)
        deny = _matches(ctx.deny, item, base)
        verdict = _policy_verdict(allow, deny, ctx)
        if verdict == ActionVerdict.deny:
            return _denied(
                input, item, f"Denied {item.operation}: {str(item.location)!r}"
            )
    return None


def _request_approvals(
    input: ResolvedFileCommand[CommandExecution, CommandReady],
    ctx: GuardContext,
    base: Path,
) -> GuardFilesResult[CommandExecution, CommandReady] | None:
    """Collect required approvals, then stop at the first declined or failed prompt."""
    pending = [item for item in input.items if _matches(ctx.ask, item, base)]
    if pending and ctx.pipe is None:
        return _denied(
            input, pending[0], "Approval unavailable: no interaction context"
        )
    for item in pending:
        try:
            reply = runtime.interact_with_user(
                f"Allow {item.operation} for {str(item.location)!r}? (yes/no)",
                with_reply=True,
            )
        except (UserInputUnavailableError, RuntimeError, ValueError) as error:
            return _denied(input, item, f"Approval unavailable: {error}")
        if reply is None or reply.strip().lower() not in ("y", "yes"):
            return _denied(
                input,
                item,
                "Denied by user response to permission prompt.",
                GuardDenyReason.USER_DECLINED,
            )
    return None


def _allowed(
    input: ResolvedFileCommand[CommandExecution, CommandReady],
) -> GuardFilesResult[CommandExecution, CommandReady]:
    return GuardFilesResult(
        status=GuardStatus.ALLOWED,
        original_input=input.original_input,
        items=[
            GuardFileSingleResult(
                verdict=ActionVerdict.allow, location=item.location, value=item.value
            )
            for item in input.items
        ],
        truncation=Truncation(threshold=0, severity=Severity.REMOVE),
    )


@factory
def guard_tagged_file_command(
    input: ResolvedFileCommand[CommandExecution, CommandReady],
    messages: list[Message],
    ctx: GuardContext,
) -> GuardFilesResult[CommandExecution, CommandReady]:
    """Check every transfer permission, then obtain all required approvals."""
    if ctx.base is None:
        raise ValueError("Base is required")
    denial = _check_policy(input, ctx, ctx.base)
    if denial is not None:
        return denial
    denial = _request_approvals(input, ctx, ctx.base)
    if denial is not None:
        return denial
    return _allowed(input)
