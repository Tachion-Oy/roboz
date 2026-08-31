from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from threading import Event

from roboz.runtime._external import ControlSignal

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BASE_DELAY_S = 0.5
_DEFAULT_MAX_DELAY_S = 4.0
_DEFAULT_POLL_INTERVAL_S = 0.05
_DEFAULT_LABEL = "retry"


class RetryState:
    """Per-attempt token an attempt callable uses to report that it already
    emitted externally-visible output (e.g. streamed a chunk to the caller),
    so the retry loop normally must not retry that attempt on failure. Callers
    that can explicitly supersede that output may opt in to retrying it.

    Backed by a ``threading.Event`` rather than a plain bool: the mutation may
    happen on a worker thread (e.g. inside an ``on_delta`` callback running
    behind ``run_cancellable_external_call``) while the retry loop reads it on
    the calling thread after the attempt returns or raises.
    """

    def __init__(self, attempt: int = 1) -> None:
        self._output_emitted = Event()
        self.attempt = attempt

    def mark_output_emitted(self) -> None:
        self._output_emitted.set()

    @property
    def output_emitted(self) -> bool:
        return self._output_emitted.is_set()


def run_with_retry[T](
    fn: Callable[[RetryState], T],
    *,
    retryable_errors: tuple[type[Exception], ...],
    control_signals: Sequence[ControlSignal] = (),
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay_s: float = _DEFAULT_BASE_DELAY_S,
    max_delay_s: float = _DEFAULT_MAX_DELAY_S,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    label: str = _DEFAULT_LABEL,
    prepare_retry: Callable[[Exception, int, int, bool], bool] | None = None,
) -> T:
    """Call ``fn`` with a fresh ``RetryState`` per attempt, retrying on
    ``retryable_errors`` with capped exponential backoff.

    An attempt is not retried once its ``RetryState`` reports emitted output
    unless ``prepare_retry`` confirms that output was superseded. The callback
    receives the error, failed attempt number, maximum attempts, and whether
    output was emitted, and returns whether another attempt is safe. Errors
    outside ``retryable_errors``
    (including any ``BaseException``) propagate immediately from the first
    attempt. The backoff sleep polls ``control_signals`` so a cancellation or
    interruption fired mid-backoff raises promptly instead of after the full
    delay.
    """
    attempt = 1
    while True:
        state = RetryState(attempt)
        try:
            return fn(state)
        except retryable_errors as exc:
            output_emitted = state.output_emitted
            if attempt >= max_attempts:
                raise
            if prepare_retry is None:
                if output_emitted:
                    raise
            elif not prepare_retry(exc, attempt, max_attempts, output_emitted):
                raise
            delay = min(max_delay_s, base_delay_s * 2 ** (attempt - 1))
            logger.warning(
                "%s attempt %d/%d failed (error_type=%s); retrying in %.3fs",
                label,
                attempt,
                max_attempts,
                type(exc).__name__,
                delay,
            )
            _cancellable_backoff_sleep(
                delay, control_signals, poll_interval_s=poll_interval_s, label=label
            )
            attempt += 1


def _cancellable_backoff_sleep(
    delay_s: float,
    control_signals: Sequence[ControlSignal],
    *,
    poll_interval_s: float,
    label: str,
) -> None:
    deadline = time.monotonic() + delay_s
    while True:
        for signal in control_signals:
            if signal.is_set:
                logger.info(
                    "%s abandoning retry backoff: %s set during backoff sleep",
                    label,
                    signal.label,
                )
                signal.raise_if_set()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll_interval_s, remaining))
