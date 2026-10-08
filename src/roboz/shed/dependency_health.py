"""Safe dependency checks, bounded scheduling, and cached health observations."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel
from roboz.dependencies import (
    DependencyFailure,
    DependencyReasonCode,
    ExternalDependency,
    ExternalDependencyKind,
    dedupe_external_dependencies,
    reason_code_for_exception,
)
from roboz.runtime._logging import log_with_data

logger = logging.getLogger(__name__)


class DependencyStatus(StrEnum):
    """Cached observation state, independent of application readiness."""

    PENDING = "pending"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class DependencyRecord(BaseModel):
    """A sanitized health observation suitable for any consumer."""

    dependency_id: str
    kind: ExternalDependencyKind
    redacted_metadata: dict[str, str]
    status: DependencyStatus = DependencyStatus.PENDING
    checked_at: datetime | None = None
    latency_ms: float | None = None
    reason_code: DependencyReasonCode | None = None
    message: str | None = None


class DependencyHealthMonitor:
    """Periodically call resource-owned checks and cache sanitized observations.

    Combine agent dependencies and standalone resources in the supplied sequence.
    The first resource for each dependency ID is retained for this monitor's
    lifetime. Construction records pending status; observation performs checks.
    """

    def __init__(
        self,
        dependencies: Sequence[ExternalDependency],
        *,
        interval_s: float = 60.0,
        timeout_s: float = 20.0,
        max_concurrency: int = 4,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Configure checks and scheduling without starting tasks or resolving dependencies."""
        resources = dedupe_external_dependencies(dependencies)
        self._dependencies = {item.dependency_id: item for item in resources}
        self._records = {
            item.dependency_id: DependencyRecord(
                dependency_id=item.dependency_id,
                kind=item.kind,
                redacted_metadata=_sanitize_metadata(item),
            )
            for item in resources
        }
        self._interval_s = interval_s
        self._timeout_s = timeout_s
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._inflight: dict[str, asyncio.Task[DependencyFailure | None]] = {}
        self._scheduler: asyncio.Task[None] | None = None

    def records(self) -> list[DependencyRecord]:
        """Return detached copies of every cached observation."""
        return [record.model_copy(deep=True) for record in self._records.values()]

    def record(self, dependency_id: str) -> DependencyRecord | None:
        """Return a detached cached observation, or None for an unknown identity."""
        record = self._records.get(dependency_id)
        return None if record is None else record.model_copy(deep=True)

    async def start(self) -> None:
        """Start or restart periodic observation without changing caller readiness state."""
        if self._scheduler is None or self._scheduler.done():
            if self._scheduler is not None and not self._scheduler.cancelled():
                error = self._scheduler.exception()
                if error is not None:
                    self._log_iteration_failure(error)
            self._scheduler = asyncio.create_task(
                self._schedule(), name="dependency-health"
            )

    async def stop(self) -> None:
        """Cancel and await owned scheduler and observation tasks."""
        scheduler = self._scheduler
        self._scheduler = None
        if scheduler is not None:
            scheduler.cancel()
            await asyncio.gather(scheduler, return_exceptions=True)
        tasks = tuple(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()

    async def run_once(self) -> None:
        """Observe dependencies without overlapping an in-flight check.

        Timeouts bound observation waits. Active checkers retain their concurrency
        slots until completion, including workers that cannot be cancelled.
        """
        await asyncio.gather(
            *(self._observe(dependency_id) for dependency_id in self._dependencies)
        )

    async def _schedule(self) -> None:
        while True:
            started = self._monotonic()
            try:
                await self.run_once()
            except Exception as exc:
                self._log_iteration_failure(exc)
            delay = max(0.0, self._interval_s - (self._monotonic() - started))
            await self._sleep(delay)

    @staticmethod
    def _log_iteration_failure(error: BaseException) -> None:
        """Report a scheduler failure without exposing provider exception payloads."""
        failure = DependencyFailure.from_exception(error)
        log_with_data(
            logger, logging.ERROR,
            f"Dependency health iteration failed: {failure.message}",
            {"reason_code": failure.reason_code.value, "diagnostic": failure.message},
        )

    async def _observe(self, dependency_id: str) -> None:
        existing = self._inflight.get(dependency_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._run_check(dependency_id), name=f"dependency-check:{dependency_id}"
        )
        self._inflight[dependency_id] = task
        started = self._monotonic()
        try:
            result = await asyncio.wait_for(asyncio.shield(task), self._timeout_s)
        except TimeoutError:
            result = DependencyFailure(
                DependencyReasonCode.TIMEOUT,
                f"Dependency check timed out after {self._timeout_s:g} seconds",
            )
        except Exception as exc:
            result = DependencyFailure.from_exception(exc)
        finally:
            if task.done() and self._inflight.get(dependency_id) is task:
                self._inflight.pop(dependency_id, None)
            elif not task.done():
                task.add_done_callback(
                    lambda completed, dependency_id=dependency_id: self._clear_inflight(
                        dependency_id, completed
                    )
                )
        previous = self._records[dependency_id]
        record = previous.model_copy(
            update={
                "status": (
                    DependencyStatus.AVAILABLE
                    if result is None
                    else DependencyStatus.UNAVAILABLE
                ),
                "checked_at": datetime.fromtimestamp(
                    self._wall_clock(), tz=timezone.utc
                ),
                "latency_ms": round(max(0.0, self._monotonic() - started) * 1000.0, 3),
                "reason_code": None if result is None else result.reason_code,
                "message": None if result is None else result.message,
            }
        )

        self._records[dependency_id] = record
        changed = (previous.status, previous.reason_code, previous.message) != (
            record.status, record.reason_code, record.message
        )
        level = logging.DEBUG
        if changed and record.status is DependencyStatus.UNAVAILABLE:
            level = logging.WARNING
        elif previous.status is DependencyStatus.UNAVAILABLE and result is None:
            level = logging.INFO
        log_with_data(
            logger, level,
            f"Dependency {dependency_id} {record.status}"
            + (f": {record.message}" if record.message else ""),
            {
                "dependency_id": dependency_id,
                "status": record.status.value,
                "checked_at": record.checked_at.isoformat() if record.checked_at else None,
                "latency_ms": record.latency_ms,
                "reason_code": record.reason_code.value if record.reason_code else None,
                "diagnostic": record.message,
            },
        )

    async def _run_check(self, dependency_id: str) -> DependencyFailure | None:
        dependency = self._dependencies[dependency_id]
        async with self._semaphore:
            return await asyncio.to_thread(check_dependency, dependency)

    def _clear_inflight(
        self, dependency_id: str, task: asyncio.Task[DependencyFailure | None]
    ) -> None:
        if not task.cancelled():
            task.exception()
        if self._inflight.get(dependency_id) is task:
            self._inflight.pop(dependency_id, None)


def check_dependency(dependency: ExternalDependency) -> DependencyFailure | None:
    """Return the resource-owned diagnostic, or None when available."""
    try:
        result = dependency.check()
        if result is not None and not isinstance(result, DependencyFailure):
            raise TypeError("ExternalDependency.check() must return DependencyFailure or None")
        return result
    except Exception as exc:
        return DependencyFailure.from_exception(exc)


_METADATA_KEYS: Mapping[ExternalDependencyKind, frozenset[str]] = {
    ExternalDependencyKind.EXECUTABLE: frozenset({"executable", "display_name"}),
    ExternalDependencyKind.MODEL_ENDPOINT: frozenset(
        {"api_name", "model_name", "endpoint_type"}
    ),
    ExternalDependencyKind.NETWORK_SERVICE: frozenset(
        {"provider", "service", "host", "port", "tls_mode", "display_name"}
    ),
}


def _sanitize_metadata(dependency: ExternalDependency) -> dict[str, str]:
    try:
        metadata = dependency.redacted_metadata()
    except Exception:
        return {}
    allowed = _METADATA_KEYS.get(dependency.kind, frozenset())
    return {
        key: str(value)
        for key, value in metadata.items()
        if key in allowed
        and isinstance(key, str)
        and isinstance(value, (str, int, float))
    }


__all__ = [
    "DependencyFailure",
    "DependencyHealthMonitor",
    "DependencyReasonCode",
    "DependencyRecord",
    "DependencyStatus",
    "check_dependency",
    "reason_code_for_exception",
]
