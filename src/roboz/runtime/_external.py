from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import BoundedSemaphore, Event, Thread
from typing import Final
from uuid import uuid4

from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
    ExternalCallTimeoutError,
)
from roboz.runtime._logging import LogScalar, log_with_data
from roboz.runtime.observability import (
    ExternalCallPhase,
    ObservedFailure,
    RuntimeEventLevel,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_WORKERS: Final[int] = 8
_DEFAULT_POLL_INTERVAL_S: Final[float] = 0.05
_DEFAULT_LABEL: Final[str] = "external-call"
_CALL_ID_KEY: Final[str] = "call_id"
_LABEL_KEY: Final[str] = "label"
_ATTEMPT_KEY: Final[str] = "attempt"
_TIMEOUT_SECONDS_KEY: Final[str] = "timeout_s"
_DURATION_MS_KEY: Final[str] = "duration_ms"
_PHASE_KEY: Final[str] = "phase"
_SIGNAL_KEY: Final[str] = "signal"
_RAN_SECONDS_KEY: Final[str] = "ran_s"
_ERROR_TYPE_KEY: Final[str] = "error_type"
_WORKER_KEY: Final[str] = "worker"
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
        log_data: dict[str, LogScalar] | None,
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
        self._base_data: dict[str, LogScalar] = {
            _CALL_ID_KEY: self._call_id,
            _LABEL_KEY: label,
            _ATTEMPT_KEY: attempt,
        }
        if timeout_s is not None:
            self._base_data[_TIMEOUT_SECONDS_KEY] = timeout_s
        self._base_data.update(log_data or {})

    def run(self) -> T:
        self._acquire_slot()
        self._start_worker()
        return self._await_result()

    def _acquire_slot(self) -> None:
        while True:
            self._raise_if_any_signal_set(ExternalCallPhase.WAITING_FOR_SLOT)
            timeout_left = self._time_left()
            if timeout_left is not None and timeout_left <= 0:
                self._abandoned.set()
                duration_ms = self._duration_ms()
                log_with_data(
                    logger,
                    logging.ERROR,
                    (
                        f"External call timed out waiting for slot: {self._label} "
                        f"(duration_ms={duration_ms})"
                    ),
                    self._base_data | {_DURATION_MS_KEY: duration_ms},
                )
                raise ExternalCallTimeoutError(
                    "Timed out waiting for external call slot"
                )
            if _external_call_slots.acquire(timeout=self._poll_timeout(timeout_left)):
                return

    def _start_worker(self) -> None:
        worker = Thread(
            target=self._worker,
            daemon=True,
            name=self._worker_name,
        )
        try:
            worker.start()
        except BaseException:
            _external_call_slots.release()
            raise

    def _worker(self) -> None:
        worker_started = time.monotonic()
        try:
            result = self._fn()
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures
            ran = time.monotonic() - worker_started
            worker_data = self._base_data | {
                _RAN_SECONDS_KEY: ran,
                _ERROR_TYPE_KEY: type(exc).__name__,
            }
            if self._abandoned.is_set():
                log_with_data(
                    logger,
                    logging.WARNING,
                    (
                        "External-call worker failed after caller abandonment: "
                        f"{self._label} (error_type={type(exc).__name__})"
                    ),
                    worker_data,
                )
            self._put_outcome(_CallFailed(exc))
        else:
            ran = time.monotonic() - worker_started
            worker_data = self._base_data | {_RAN_SECONDS_KEY: ran}
            if self._abandoned.is_set():
                log_with_data(
                    logger,
                    logging.WARNING,
                    (
                        "External-call worker finished after caller abandonment: "
                        f"{self._label}"
                    ),
                    worker_data,
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
                duration_ms = self._duration_ms()
                log_with_data(
                    logger,
                    logging.ERROR,
                    f"External call timed out: {self._label} (duration_ms={duration_ms})",
                    self._base_data
                    | {
                        _DURATION_MS_KEY: duration_ms,
                        _WORKER_KEY: self._worker_name,
                    },
                )
                raise ExternalCallTimeoutError("External call timed out")
            try:
                outcome = self._result_queue.get(
                    timeout=self._poll_timeout(timeout_left)
                )
            except Empty:
                continue
            if isinstance(outcome, _CallFailed):
                raise outcome.error
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
            logger.debug(
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
        duration_ms = self._duration_ms()
        log_with_data(
            logger,
            logging.WARNING,
            f"External call {failure.kind}: {self._label} (phase={phase})",
            self._base_data
            | {
                _DURATION_MS_KEY: duration_ms,
                _PHASE_KEY: str(phase),
                _SIGNAL_KEY: signal_label,
            },
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
    log_data: dict[str, LogScalar] | None = None,
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
