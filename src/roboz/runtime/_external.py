from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import BoundedSemaphore, Event, Thread, current_thread
from uuid import uuid4

from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
    ExternalCallTimeoutError,
)
from roboz.runtime.observability import (
    ExternalCallPhase,
    ObservedFailure,
    RuntimeEventLevel,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_WORKERS = 8
_DEFAULT_POLL_INTERVAL_S = 0.05
_DEFAULT_LABEL = "external-call"
_external_call_slots = BoundedSemaphore(_DEFAULT_MAX_WORKERS)


class ControlSignal:
    """Shared run-control signal for blocking waits around external calls."""

    def __init__(
        self,
        *,
        error_type: type[Exception] = ExternalCallCancelledError,
        message: str = "External call was cancelled",
    ) -> None:
        self._event = Event()
        self._error_type = error_type
        self._message = message

    @property
    def label(self) -> str:
        """Human-readable identity for logs (the raised error type)."""
        return self._error_type.__name__

    @property
    def failure(self) -> ObservedFailure:
        if issubclass(self._error_type, ExternalCallInterruptedError):
            return ObservedFailure.interrupted(level=RuntimeEventLevel.WARNING)
        return ObservedFailure.cancelled(level=RuntimeEventLevel.WARNING)

    def set(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    def raise_if_set(self) -> None:
        if self.is_set:
            raise self._error_type(self._message)


@dataclass(frozen=True)
class _CallSucceeded[T]:
    value: T


@dataclass(frozen=True)
class _CallFailed:
    error: BaseException


type _WorkerOutcome[T] = _CallSucceeded[T] | _CallFailed


class _ExternalCallRunner[T]:
    """Own the state and lifecycle of one cancellable external call."""

    def __init__(
        self,
        fn: Callable[[], T],
        *,
        control_signals: Sequence[ControlSignal] | None,
        timeout_s: float | None,
        poll_interval_s: float,
        label: str,
        call_id: str | None,
        attempt: int,
        log_data: dict[str, str | int | float | bool | None] | None,
    ) -> None:
        self._fn = fn
        self._signals = tuple(control_signals or (ControlSignal(),))
        self._timeout_s = timeout_s
        self._poll_interval_s = poll_interval_s
        self._label = label
        self._call_id = call_id or str(uuid4())
        self._attempt = attempt
        self._deadline = None if timeout_s is None else time.monotonic() + timeout_s
        self._result_queue: Queue[_WorkerOutcome[T]] = Queue(maxsize=1)
        self._abandoned = Event()
        self._started = time.monotonic()
        self._worker_name = f"roboz-external-call:{label}"
        self._base_data: dict[str, str | int | float | bool | None] = {
            "call_id": self._call_id,
            "label": label,
            "attempt": attempt,
        }
        if timeout_s is not None:
            self._base_data["timeout_s"] = timeout_s
        self._base_data.update(log_data or {})

    def run(self) -> T:
        self._log_started()
        self._acquire_slot()
        self._start_worker()
        return self._await_result()

    def _log_started(self) -> None:
        logger.info(
            "External call started (label=%s, call_id=%s, attempt=%d, "
            "timeout_s=%s, data=%s)",
            self._label,
            self._call_id,
            self._attempt,
            self._timeout_s,
            self._base_data,
        )

    def _acquire_slot(self) -> None:
        while True:
            self._raise_if_any_signal_set(ExternalCallPhase.WAITING_FOR_SLOT)
            timeout_left = self._time_left()
            if timeout_left is not None and timeout_left <= 0:
                self._abandoned.set()
                logger.error(
                    "External call timed out waiting for a slot (label=%s, "
                    "call_id=%s, attempt=%d, duration_ms=%d)",
                    self._label,
                    self._call_id,
                    self._attempt,
                    self._duration_ms(),
                )
                raise ExternalCallTimeoutError(
                    "Timed out waiting for external call slot"
                )
            if _external_call_slots.acquire(timeout=self._poll_timeout(timeout_left)):
                return

    def _start_worker(self) -> None:
        Thread(
            target=self._worker,
            daemon=True,
            name=self._worker_name,
        ).start()

    def _worker(self) -> None:
        worker_started = time.monotonic()
        logger.debug(
            "External-call worker started (label=%s, call_id=%s, thread=%s)",
            self._label,
            self._call_id,
            current_thread().name,
        )
        try:
            result = self._fn()
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures
            ran = time.monotonic() - worker_started
            if self._abandoned.is_set():
                logger.warning(
                    "External-call worker raised after caller abandonment; error "
                    "discarded (label=%s, call_id=%s, ran_s=%.3f, "
                    "error_type=%s)",
                    self._label,
                    self._call_id,
                    ran,
                    type(exc).__name__,
                )
            else:
                logger.debug(
                    "External-call worker raised (label=%s, call_id=%s, "
                    "ran_s=%.3f, error_type=%s)",
                    self._label,
                    self._call_id,
                    ran,
                    type(exc).__name__,
                )
            self._put_outcome(_CallFailed(exc))
        else:
            ran = time.monotonic() - worker_started
            if self._abandoned.is_set():
                logger.warning(
                    "External-call worker finished after caller abandonment; result "
                    "discarded (label=%s, call_id=%s, ran_s=%.3f)",
                    self._label,
                    self._call_id,
                    ran,
                )
            else:
                logger.debug(
                    "External-call worker finished (label=%s, call_id=%s, ran_s=%.3f)",
                    self._label,
                    self._call_id,
                    ran,
                )
            self._put_outcome(_CallSucceeded(result))
        finally:
            _external_call_slots.release()

    def _await_result(self) -> T:
        while True:
            self._raise_if_any_signal_set(
                ExternalCallPhase.AWAITING_RESULT,
                worker_name=self._worker_name,
            )
            timeout_left = self._time_left()
            if timeout_left is not None and timeout_left <= 0:
                self._abandoned.set()
                logger.error(
                    "External call timed out (label=%s, call_id=%s, attempt=%d, "
                    "duration_ms=%d, worker=%s left running)",
                    self._label,
                    self._call_id,
                    self._attempt,
                    self._duration_ms(),
                    self._worker_name,
                )
                raise ExternalCallTimeoutError("External call timed out")
            try:
                outcome = self._result_queue.get(
                    timeout=self._poll_timeout(timeout_left)
                )
            except Empty:
                continue
            if isinstance(outcome, _CallFailed):
                logger.error(
                    "External call failed (label=%s, call_id=%s, attempt=%d, "
                    "duration_ms=%d, error_type=%s)",
                    self._label,
                    self._call_id,
                    self._attempt,
                    self._duration_ms(),
                    type(outcome.error).__name__,
                )
                raise outcome.error
            self._log_succeeded(outcome.value)
            return outcome.value

    def _raise_if_any_signal_set(
        self,
        phase: ExternalCallPhase,
        *,
        worker_name: str | None = None,
    ) -> None:
        for signal in self._signals:
            if not signal.is_set:
                continue
            self._abandoned.set()
            worker = (
                ""
                if worker_name is None
                else f"; worker thread {worker_name!r} left running"
            )
            logger.info(
                "%s abandoning external call: %s set during %s after %.3fs%s",
                self._label,
                signal.label,
                phase,
                self._elapsed(),
                worker,
            )
            self._log_abandoned(signal.failure, signal.label, phase)
            signal.raise_if_set()

    def _log_abandoned(
        self,
        failure: ObservedFailure,
        signal_label: str,
        phase: ExternalCallPhase,
    ) -> None:
        logger.warning(
            "External call %s (label=%s, call_id=%s, attempt=%d, "
            "duration_ms=%d, phase=%s, signal=%s)",
            failure.kind,
            self._label,
            self._call_id,
            self._attempt,
            self._duration_ms(),
            phase,
            signal_label,
        )

    def _log_succeeded(self, result: T) -> None:
        duration_ms = self._duration_ms()
        result_data: dict[str, str | int | float | bool | None] = self._base_data | {
            "duration_ms": duration_ms,
            "result_type": type(result).__name__,
        }
        logger.info(
            "External call succeeded (label=%s, call_id=%s, attempt=%d, "
            "duration_ms=%d, result_type=%s, data=%s)",
            self._label,
            self._call_id,
            self._attempt,
            duration_ms,
            type(result).__name__,
            result_data,
        )

    def _put_outcome(self, outcome: _WorkerOutcome[T]) -> None:
        try:
            self._result_queue.put_nowait(outcome)
        except Full:
            logger.debug("external-call result queue full; dropping worker result")

    def _elapsed(self) -> float:
        return time.monotonic() - self._started

    def _duration_ms(self) -> int:
        return round(self._elapsed() * 1000)

    def _time_left(self) -> float | None:
        if self._deadline is None:
            return None
        return self._deadline - time.monotonic()

    def _poll_timeout(self, timeout_left: float | None) -> float:
        if timeout_left is None:
            return self._poll_interval_s
        return max(0.0, min(self._poll_interval_s, timeout_left))


def run_cancellable_external_call[T](
    fn: Callable[[], T],
    *,
    control_signals: Sequence[ControlSignal] | None = None,
    timeout_s: float | None = None,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    label: str = _DEFAULT_LABEL,
    call_id: str | None = None,
    attempt: int = 1,
    log_data: dict[str, str | int | float | bool | None] | None = None,
) -> T:
    """Run blocking third-party code behind a cancellable wait boundary.

    The worker thread is not forcibly killed. On cancellation or timeout the caller
    stops waiting immediately; a late worker result is ignored.

    ``label`` identifies this call across log lines (e.g. an LLM call id), so the
    abandoned worker thread can be matched to the run thread that walked away.

    ``log_data`` is deliberately not sanitized. Callers may use it only for known
    non-sensitive identifiers and scalar metadata, never payloads, URLs, headers,
    response content, or exception messages.
    """
    return _ExternalCallRunner(
        fn,
        control_signals=control_signals,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
        label=label,
        call_id=call_id,
        attempt=attempt,
        log_data=log_data,
    ).run()
