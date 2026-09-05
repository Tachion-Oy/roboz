"""Shared runtime state and cancellable provider execution."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from roboz import FactoryCtx
from roboz.exceptions import ExternalCallCancelledError
from roboz.runtime import run_cancellable_external_call
from roboz.runtime.pipe import EventPipe
from roboz.tooling import ToolDependency

from .contracts import EmailService

T = TypeVar("T")


@dataclass(frozen=True)
class EmailRuntimeContext(FactoryCtx):
    """Provider binding, cancellation, timeout, and prompt policy for email tools."""

    service: ToolDependency[EmailService]
    is_cancelled: Callable[[], bool]
    timeout_s: float
    pipe: EventPipe | None
    prompt_before_inbox_read: bool = False


def run_email_call(
    ctx: EmailRuntimeContext,
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
