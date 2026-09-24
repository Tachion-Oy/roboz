"""Shared cancellation and deadlines for email-provider operations."""

from collections.abc import Callable
from threading import Event
from time import monotonic

from roboz.shed.tools.contexts import EmailContext
from roboz.exceptions import ExternalCallCancelledError
from roboz.runtime import run_cancellable_external_call


def run_email_call[T](
    ctx: EmailContext,
    *,
    label: str,
    cancelled_message: str,
    operation: Callable[[Callable[[], bool]], T],
) -> T:
    """Stop waiting promptly and let abandoned workers stop before later writes."""
    abandoned = Event()
    deadline = monotonic() + ctx.timeout_s
    signals = ctx.pipe.control_signals if ctx.pipe is not None else ()

    def is_cancelled() -> bool:
        return (
            abandoned.is_set()
            or ctx.is_cancelled()
            or monotonic() >= deadline
            or any(signal.is_set for signal in signals)
        )

    if is_cancelled():
        raise ExternalCallCancelledError(cancelled_message)
    try:
        result = run_cancellable_external_call(
            lambda: operation(is_cancelled),
            control_signals=signals,
            timeout_s=ctx.timeout_s,
            label=label,
        )
        if is_cancelled():
            raise ExternalCallCancelledError(cancelled_message)
        return result
    finally:
        abandoned.set()
