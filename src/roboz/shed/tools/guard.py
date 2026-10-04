"""Authorize explicit filesystem requirements before requesting any approvals."""

import json
from pathlib import Path, PurePosixPath

from roboz import runtime
from roboz.exceptions import UserInputUnavailableError
from roboz.models import Message
from roboz.models.truncation import Severity, Truncation
from roboz.shed.models import (
    ActionVerdict,
    GuardDenyReason,
    GuardFileSingle,
    GuardFileSingleResult,
    GuardFilesResult,
    GuardStatus,
    PermissionRule,
    TInput,
    TPayload,
)
from roboz.shed.tools.contexts import GuardContext
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.tooling import Tool
from roboz.tooling.decorators import factory


def _matches(
    rules: list[PermissionRule], item: GuardFileSingle[TPayload], base: Path
) -> bool:
    """Match literal POSIX names without stripping spaces or changing separators."""
    for rule in rules:
        if item.operation not in rule.operations:
            continue
        pattern = rule.pattern if isinstance(rule.pattern, str) else rule.pattern()
        path = item.location
        if not (pattern.startswith("/") or Path(pattern).is_absolute()):
            try:
                path = path.relative_to(base)
            except ValueError:
                continue
        if pattern.endswith("/**") and path.as_posix() == pattern[:-3]:
            return True
        if PurePosixPath(path.as_posix()).full_match(pattern):
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
    input: ResolvedFileCommand[TInput, TPayload],
    item: GuardFileSingle[TPayload],
    message: str,
    reason: GuardDenyReason = GuardDenyReason.POLICY_DENIED,
) -> GuardFilesResult[TInput, TPayload]:
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
    input: ResolvedFileCommand[TInput, TPayload],
    ctx: GuardContext,
    base: Path,
) -> GuardFilesResult[TInput, TPayload] | None:
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
    input: ResolvedFileCommand[TInput, TPayload],
    ctx: GuardContext,
    base: Path,
) -> GuardFilesResult[TInput, TPayload]:
    """Collect required approvals, then stop at the first declined or failed prompt."""
    pending = [item for item in input.items if _matches(ctx.ask, item, base)]
    exchanges: list[str] = []
    if pending and ctx.pipe is None:
        return _denied(
            input, pending[0], "Approval unavailable: no interaction context"
        )
    for item in pending:
        prompt = f"Allow {item.operation} for {str(item.location)!r}? (yes/no)"
        try:
            reply = runtime.interact_with_user(prompt, with_reply=True)
        except (UserInputUnavailableError, RuntimeError, ValueError) as error:
            exchanges.append(f"Permission prompt: {prompt}\nApproval unavailable: {error}")
            return _denied(input, item, "\n".join(exchanges))
        approved = reply is not None and reply.strip().lower() in ("y", "yes")
        reply_text = (
            json.dumps(reply, ensure_ascii=False)
            if reply is not None
            else "(no reply received)"
        )
        exchanges.append(
            f"Permission prompt: {prompt}\nUser reply: {reply_text}\n"
            f"Decision: {'allowed' if approved else 'denied'}"
        )
        if not approved:
            diagnostic = (
                "Denied: no reply received to permission prompt."
                if reply is None
                else "Denied by user response to permission prompt."
            )
            return _denied(
                input,
                item,
                "\n".join([*exchanges, diagnostic]),
                GuardDenyReason.USER_DECLINED,
            )
    return _allowed(input, message="\n".join(exchanges) or None)


def _allowed(
    input: ResolvedFileCommand[TInput, TPayload],
    *,
    message: str | None = None,
) -> GuardFilesResult[TInput, TPayload]:
    return GuardFilesResult(
        status=GuardStatus.ALLOWED,
        message=message,
        original_input=input.original_input,
        items=[
            GuardFileSingleResult(
                verdict=ActionVerdict.allow, location=item.location, value=item.value
            )
            for item in input.items
        ],
        truncation=Truncation(threshold=0, severity=Severity.REMOVE),
    )


def guard_items(
    *,
    items_to_guard: list[GuardFileSingle[TPayload]],
    original_input: TInput,
    ctx: GuardContext,
) -> GuardFilesResult[TInput, TPayload]:
    """Check all explicit requirements before prompting; retain typed payloads.

    Resolvers supply canonical permission locations and every operation required
    by the effect, including READ and DELETE when overwriting an existing file.
    """
    if ctx.base is None:
        raise ValueError("Base is required")
    input = ResolvedFileCommand(original_input=original_input, items=items_to_guard)
    denial = _check_policy(input, ctx, ctx.base)
    if denial is not None:
        return denial
    return _request_approvals(input, ctx, ctx.base)


@factory
def operation_guard(
    input: ResolvedFileCommand, messages: list[Message], ctx: GuardContext
) -> GuardFilesResult:
    """Check every required filesystem permission, then obtain all approvals."""
    return guard_items(
        items_to_guard=input.items, original_input=input.original_input, ctx=ctx
    )


def build_guarded_tool_chain(
    *,
    entry: Tool,
    guard: Tool,
    execute: Tool,
    continuation: Tool | None = None,
) -> list[Tool]:
    """Build resolve -> guard -> execute, optionally looping through continuation.

    Without continuation, a denial ends the chain. With continuation, the
    executor also receives denials to record a failed step without performing
    its effect. The continuation's own condition selects execution outputs that
    need another step, which returns through the same guard.
    """
    guard = guard.copy(
        chained_to=entry,
        chain_condition=lambda output: isinstance(output, ResolvedFileCommand),
    )
    execute = execute.copy(
        chained_to=guard,
        chain_condition=lambda output: (
            isinstance(output, GuardFilesResult)
            and (output.status == GuardStatus.ALLOWED or continuation is not None)
        ),
    )
    if continuation is None:
        return [entry, guard, execute]
    continuation = continuation.copy(chained_to=execute)
    guard.chain(continuation)
    return [entry, guard, execute, continuation]
