"""Shared permission guard for resolved tool operations."""

from pathlib import Path

from roboshed.models import (
    ActionVerdict,
    GuardDenyReason,
    GuardFileSingle,
    GuardFileSingleResult,
    GuardFilesResult,
    GuardStatus,
    Help,
    Operation,
    ParseError,
    TInput,
    TPayload,
)
from roboshed.tools.guard_formatting import format_guard_constraints
from roboshed.tools.types import ResolvedFileCommand
from roboshed.tools.utils import check_allow_deny_permission, check_ask_permission
from roboshed.tools.contexts import GuardContext
from roboz.models import Message
from roboz.models.truncation import Severity, Truncation
from roboz.tooling import Tool
from roboz.tooling.decorators import factory

GUARD_ERR_DENIED = "operation={operation!r} DENIED for location={location!r}"
GUARD_ERR_DENIED_BY_USER = "Denied by user response to permission prompt."
CHAIN_BREAKING_OUTPUTS: tuple[type, ...] = (ParseError, Help)


def resolve_allow_verdict(
    location: Path, operation: Operation, ctx: GuardContext
) -> tuple[ActionVerdict, GuardDenyReason | None]:
    """Check allow/deny and ask rules for a single guarded location."""
    base_path = ctx.base.resolve() if ctx.base else None
    verdict = check_allow_deny_permission(
        location=location,
        op_type=operation,
        takes_precedence=ctx.takes_precedence,
        allow_rules=ctx.allow,
        deny_rules=ctx.deny,
        default_verdict=ctx.default_verdict,
        base_path=base_path,
    )
    if (
        verdict == ActionVerdict.allow
        and operation == Operation.CREATE
        and location.exists()
        and location.is_file()
    ):
        for overwrite_operation in (Operation.READ, Operation.DELETE):
            verdict = check_allow_deny_permission(
                location=location,
                op_type=overwrite_operation,
                takes_precedence=ctx.takes_precedence,
                allow_rules=ctx.allow,
                deny_rules=ctx.deny,
                default_verdict=ctx.default_verdict,
                base_path=base_path,
            )
            if verdict == ActionVerdict.deny:
                return verdict, GuardDenyReason.POLICY_DENIED
    if verdict == ActionVerdict.allow:
        verdict, ask_outcome = check_ask_permission(
            location=location,
            op_type=operation,
            ask_rules=ctx.ask,
            base_path=base_path,
            pipe=getattr(ctx, "pipe", None),
        )
        if verdict == ActionVerdict.deny and ask_outcome == "user_declined":
            return verdict, GuardDenyReason.USER_DECLINED
    if verdict == ActionVerdict.deny:
        return verdict, GuardDenyReason.POLICY_DENIED
    return verdict, None


def guard_items(
    *,
    items_to_guard: list[GuardFileSingle[TPayload]],
    original_input: TInput,
    ctx: GuardContext,
) -> GuardFilesResult[TInput, TPayload]:
    """Validate guarded items and return deny or allowed guard results."""
    items: list[GuardFileSingleResult[TPayload]] = []
    for item in items_to_guard:
        verdict, deny_reason = resolve_allow_verdict(item.location, item.operation, ctx)
        if verdict == ActionVerdict.deny:
            if deny_reason == GuardDenyReason.USER_DECLINED:
                message = GUARD_ERR_DENIED_BY_USER
            else:
                message = GUARD_ERR_DENIED.format(
                    operation=item.operation, location=item.location
                )
                message = f"{message}\n\n{format_guard_constraints(ctx)}"
            return GuardFilesResult(
                truncation=Truncation(threshold=0, severity=Severity.LIGHT),
                status=GuardStatus.DENIED,
                deny_reason=deny_reason,
                items=[
                    GuardFileSingleResult(
                        verdict=verdict,
                        location=item.location,
                        value=item.value,
                    )
                ],
                original_input=original_input,
                message=message,
            )
        items.append(
            GuardFileSingleResult(
                verdict=verdict, location=item.location, value=item.value
            )
        )

    return GuardFilesResult(
        truncation=Truncation(threshold=0, severity=Severity.REMOVE),
        status=GuardStatus.ALLOWED,
        deny_reason=None,
        items=items,
        original_input=original_input,
    )


@factory
def operation_guard(
    input: ResolvedFileCommand, messages: list[Message], ctx: GuardContext
) -> GuardFilesResult:
    """Check whether the requested filesystem operations are permitted."""
    return guard_items(
        items_to_guard=input.items, original_input=input.original_input, ctx=ctx
    )


def build_guarded_tool_chain(
    *,
    entry: Tool,
    guard_ctx: GuardContext,
    execute: Tool,
) -> list[Tool]:
    """Wire the shared resolve -> guard -> execute recipe.

    Inserts the shared ``operation_guard`` between an already named/described
    ``entry`` resolver and an ``execute`` stage, permitting execution only when
    the guard returns ``GuardStatus.ALLOWED``. Parse-error and help outputs from
    the entry terminate the chain before the guard runs. Returns
    ``[entry, guard, execute]`` in registration order.
    """
    guard = operation_guard(guard_ctx).copy(
        chained_to=entry,
        chain_condition=lambda output: not isinstance(output, CHAIN_BREAKING_OUTPUTS),
    )
    execute = execute.copy(
        chained_to=guard,
        chain_condition=lambda output: (
            isinstance(output, GuardFilesResult)
            and output.status == GuardStatus.ALLOWED
        ),
    )
    return [entry, guard, execute]
