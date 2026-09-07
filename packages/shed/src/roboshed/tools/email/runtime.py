"""Shared runtime state and cancellable provider execution."""

from collections.abc import Callable
from functools import partial
from typing import TypeVar

from roboz import Ctx
from roboz.exceptions import ExternalCallCancelledError
from roboz.runtime import run_cancellable_external_call
from roboz.tooling.context import _prepare_context

T = TypeVar("T")


def run_email_call(
    ctx: Ctx,
    *,
    label: str,
    cancelled_message: str,
    operation: Callable[[], T],
) -> T:
    """Run one email-provider operation behind shared cancellation and timeout."""
    if ctx.is_cancelled():
        raise ExternalCallCancelledError(cancelled_message)
    result = run_cancellable_external_call(
        operation,
        control_signals=(ctx.pipe.control_signals if ctx.pipe is not None else None),
        timeout_s=ctx.timeout_s,
        label=label,
    )
    if ctx.is_cancelled():
        raise ExternalCallCancelledError(cancelled_message)
    return result


_prepare_email_context = partial(
    _prepare_context,
    required=("service", "is_cancelled", "timeout_s", "pipe"),
    defaults={"prompt_before_inbox_read": False},
)
