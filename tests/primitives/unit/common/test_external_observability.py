from __future__ import annotations

import logging
import time
from threading import BoundedSemaphore, Event, Thread

import pytest

from roboz.exceptions import ExternalCallCancelledError, ExternalCallTimeoutError
from roboz.runtime import _external
from roboz.runtime._external import ControlSignal, run_cancellable_external_call
from roboz.runtime.observability import (
    FailureKind,
    ObservedFailure,
    RuntimeEventLevel,
)


def test_observed_failure_keeps_kind_and_severity_typed() -> None:
    failure = ObservedFailure.interrupted(level=RuntimeEventLevel.WARNING)

    assert failure.kind is FailureKind.INTERRUPTED
    assert failure.level is RuntimeEventLevel.WARNING


def test_external_call_logs_metadata_without_result_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "secret-value-123456789"

    with caplog.at_level(logging.INFO, logger="roboz.runtime._external"):
        result = run_cancellable_external_call(
            lambda: f"answer api_key={secret}",
            call_id="call-123",
            label="test-provider",
            log_data={"request_items": 2},
        )

    assert result.endswith(secret)
    assert "External call started" in caplog.text
    assert "External call succeeded" in caplog.text
    assert "call-123" in caplog.text
    assert "'request_items': 2" in caplog.text
    assert "result_type" in caplog.text
    assert secret not in caplog.text


def test_external_call_logs_failure_before_caller_can_handle_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "secret-value-123456789"

    def fail() -> str:
        raise RuntimeError(f"provider rejected password={secret}")

    with (
        caplog.at_level(logging.ERROR, logger="roboz.runtime._external"),
        pytest.raises(RuntimeError),
    ):
        run_cancellable_external_call(fail, label="test-provider")

    assert "External call failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "provider rejected" not in caplog.text
    assert secret not in caplog.text


def test_external_call_logs_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        caplog.at_level(logging.INFO, logger="roboz.runtime._external"),
        pytest.raises(ExternalCallTimeoutError),
    ):
        run_cancellable_external_call(
            lambda: time.sleep(0.2),
            timeout_s=0.01,
            label="slow-provider",
        )

    assert "External call timed out" in caplog.text


def test_external_call_times_out_before_starting_worker_when_slots_are_full(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(_external, "_external_call_slots", slots)
    called = False

    def external_call() -> None:
        nonlocal called
        called = True

    try:
        with (
            caplog.at_level(logging.ERROR, logger="roboz.runtime._external"),
            pytest.raises(
                ExternalCallTimeoutError,
                match="Timed out waiting for external call slot",
            ),
        ):
            run_cancellable_external_call(
                external_call,
                timeout_s=0.01,
                poll_interval_s=0.001,
                label="slot-timeout",
            )
    finally:
        slots.release()

    assert called is False
    assert "timed out waiting for a slot" in caplog.text


def test_external_call_cancels_before_starting_worker_when_slots_are_full(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(_external, "_external_call_slots", slots)
    signal = ControlSignal()
    signal.set()
    called = False

    def external_call() -> None:
        nonlocal called
        called = True

    try:
        with (
            caplog.at_level(logging.WARNING, logger="roboz.runtime._external"),
            pytest.raises(ExternalCallCancelledError),
        ):
            run_cancellable_external_call(
                external_call,
                control_signals=(signal,),
                label="slot-cancel",
            )
    finally:
        slots.release()

    assert called is False
    assert "phase=waiting-for-slot" in caplog.text


def test_external_call_releases_slot_when_worker_thread_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(_external, "_external_call_slots", slots)

    def fail_to_start(self) -> None:
        raise RuntimeError("cannot start worker")

    monkeypatch.setattr(_external._ExternalCallRunner, "_start_worker", fail_to_start)

    with pytest.raises(RuntimeError, match="cannot start worker"):
        run_cancellable_external_call(lambda: None)

    assert slots.acquire(blocking=False) is True
    slots.release()


def test_cancelled_call_retains_slot_until_abandoned_worker_exits(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(_external, "_external_call_slots", slots)
    signal = ControlSignal()
    worker_started = Event()
    release_worker = Event()
    caller_finished = Event()
    errors: list[ExternalCallCancelledError] = []

    def external_call() -> str:
        worker_started.set()
        release_worker.wait()
        return "late result"

    def invoke() -> None:
        try:
            run_cancellable_external_call(
                external_call,
                control_signals=(signal,),
                poll_interval_s=0.001,
                label="abandoned-worker",
            )
        except ExternalCallCancelledError as exc:
            errors.append(exc)
        finally:
            caller_finished.set()

    caller = Thread(target=invoke)
    slot_reacquired = False
    with caplog.at_level(logging.WARNING, logger="roboz.runtime._external"):
        caller.start()
        try:
            assert worker_started.wait(timeout=1)
            signal.set()
            assert caller_finished.wait(timeout=1)
            assert slots.acquire(blocking=False) is False

            release_worker.set()
            slot_reacquired = slots.acquire(timeout=1)
            assert slot_reacquired
        finally:
            release_worker.set()
            caller.join(timeout=1)

    if slot_reacquired:
        slots.release()
    assert caller.is_alive() is False
    assert len(errors) == 1
    assert isinstance(errors[0], ExternalCallCancelledError)
    assert "worker finished after caller abandonment" in caplog.text
