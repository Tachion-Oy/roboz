"""Safe dependency checks, bounded scheduling, and cached health observations."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from roboz.dependencies import (
    BoundDependency,
    ExecutableDependency,
    ExternalDependency,
    ExternalDependencyKind,
)

logger = logging.getLogger(__name__)


class DependencyReasonCode(StrEnum):
    """Stable, sanitized categories for operational failures."""

    NOT_FOUND = "not_found"
    MISSING_CREDENTIALS = "missing_credentials"
    AUTHENTICATION_FAILED = "authentication_failed"
    CONNECTION_FAILED = "connection_failed"
    TLS_FAILED = "tls_failed"
    TIMEOUT = "timeout"
    PROTOCOL_ERROR = "protocol_error"
    MODEL_UNAVAILABLE = "model_unavailable"
    CHECK_FAILED = "check_failed"


@dataclass(frozen=True)
class DependencyCheckResult:
    """The result of a safe dependency probe, without provider error payloads."""

    available: bool
    reason_code: DependencyReasonCode | None = None

    @classmethod
    def success(cls) -> DependencyCheckResult:
        """Return an available result."""
        return cls(available=True)

    @classmethod
    def failure(cls, reason_code: DependencyReasonCode) -> DependencyCheckResult:
        """Return an unavailable result with a sanitized reason."""
        return cls(available=False, reason_code=reason_code)


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


class DependencyHealthMonitor:
    """Periodically check validated dependencies and cache sanitized results."""

    def __init__(
        self,
        dependencies: Sequence[BoundDependency],
        *,
        interval_s: float = 60.0,
        timeout_s: float = 20.0,
        max_concurrency: int = 4,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Configure checks and scheduling without starting tasks or resolving dependencies."""
        self._dependencies = {
            item.dependency.dependency_id: item for item in dependencies
        }
        self._records = {
            item.dependency.dependency_id: DependencyRecord(
                dependency_id=item.dependency.dependency_id,
                kind=item.dependency.kind,
                redacted_metadata=_sanitize_metadata(item.dependency),
            )
            for item in dependencies
        }
        self._interval_s = interval_s
        self._timeout_s = timeout_s
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._inflight: dict[str, asyncio.Task[DependencyCheckResult]] = {}
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
        logger.warning(
            "Dependency health iteration failed (%s).",
            reason_code_for_exception(error).value,
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
            result = DependencyCheckResult.failure(DependencyReasonCode.TIMEOUT)
        except Exception as exc:
            result = DependencyCheckResult.failure(reason_code_for_exception(exc))
        finally:
            if task.done() and self._inflight.get(dependency_id) is task:
                self._inflight.pop(dependency_id, None)
            elif not task.done():
                task.add_done_callback(
                    lambda completed, dependency_id=dependency_id: self._clear_inflight(
                        dependency_id, completed
                    )
                )
        self._records[dependency_id] = self._records[dependency_id].model_copy(
            update={
                "status": (
                    DependencyStatus.AVAILABLE
                    if result.available
                    else DependencyStatus.UNAVAILABLE
                ),
                "checked_at": datetime.fromtimestamp(
                    self._wall_clock(), tz=timezone.utc
                ),
                "latency_ms": round(max(0.0, self._monotonic() - started) * 1000.0, 3),
                "reason_code": result.reason_code,
            }
        )

    async def _run_check(self, dependency_id: str) -> DependencyCheckResult:
        bound = self._dependencies[dependency_id]
        async with self._semaphore:
            if inspect.iscoroutinefunction(bound.check):
                raw = await bound.check(bound.dependency)
            else:
                raw = await asyncio.to_thread(bound.check, bound.dependency)
                if inspect.isawaitable(raw):
                    raw = await raw
        if not isinstance(raw, DependencyCheckResult):
            return DependencyCheckResult.failure(DependencyReasonCode.PROTOCOL_ERROR)
        return raw

    def _clear_inflight(
        self, dependency_id: str, task: asyncio.Task[DependencyCheckResult]
    ) -> None:
        if not task.cancelled():
            task.exception()
        if self._inflight.get(dependency_id) is task:
            self._inflight.pop(dependency_id, None)


def check_executable(dependency: ExternalDependency) -> DependencyCheckResult:
    """Resolve an executable and verify its path without starting it."""
    try:
        materialized = dependency.materialize()
        if not isinstance(materialized, ExecutableDependency):
            return DependencyCheckResult.failure(DependencyReasonCode.PROTOCOL_ERROR)
        resolved = materialized.resolve()
        if (
            resolved is None
            or not resolved.exists()
            or not os.access(resolved, os.X_OK)
        ):
            return DependencyCheckResult.failure(DependencyReasonCode.NOT_FOUND)
        return DependencyCheckResult.success()
    except Exception as exc:
        return DependencyCheckResult.failure(reason_code_for_exception(exc))


def check_openai_compatible_endpoint(
    dependency: ExternalDependency,
) -> DependencyCheckResult:
    """Authenticate through model discovery and confirm the configured model."""
    try:
        endpoint = dependency.materialize()
        client = getattr(endpoint, "client", None)
        model_name = getattr(endpoint, "model_name", None)
        models_api = getattr(client, "models", None)
        list_models = getattr(models_api, "list", None)
        if not isinstance(model_name, str) or not callable(list_models):
            return DependencyCheckResult.failure(DependencyReasonCode.PROTOCOL_ERROR)
        response = list_models(timeout=10.0)
        data = getattr(response, "data", None)
        if data is None and isinstance(response, dict):
            data = response.get("data")
        if data is None:
            return DependencyCheckResult.failure(DependencyReasonCode.PROTOCOL_ERROR)
        model_ids = {_model_id(item) for item in data}
        if None in model_ids:
            return DependencyCheckResult.failure(DependencyReasonCode.PROTOCOL_ERROR)
        recognized_name = model_name.split(":", 1)[0]
        if model_name not in model_ids and recognized_name not in model_ids:
            return DependencyCheckResult.failure(DependencyReasonCode.MODEL_UNAVAILABLE)
        return DependencyCheckResult.success()
    except Exception as exc:
        return DependencyCheckResult.failure(reason_code_for_exception(exc))


def check_network_service(dependency: ExternalDependency) -> DependencyCheckResult:
    """Run the service-owned safe protocol probe."""
    try:
        provider = dependency.materialize()
        probe = getattr(provider, "probe", None)
        if not callable(probe):
            return DependencyCheckResult.failure(DependencyReasonCode.PROTOCOL_ERROR)
        result = probe()
        if not isinstance(result, dict):
            return DependencyCheckResult.failure(DependencyReasonCode.PROTOCOL_ERROR)
        return DependencyCheckResult.success()
    except Exception as exc:
        return DependencyCheckResult.failure(reason_code_for_exception(exc))


def reason_code_for_exception(exc: BaseException) -> DependencyReasonCode:
    """Map provider and transport failures to a sanitized reason code."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return DependencyReasonCode.TIMEOUT
    if isinstance(exc, ssl.SSLError):
        return DependencyReasonCode.TLS_FAILED
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code in {401, 403}:
        return DependencyReasonCode.AUTHENTICATION_FAILED
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timed out" in message:
        return DependencyReasonCode.TIMEOUT
    if "ssl" in name or "tls" in name or "certificate" in message:
        return DependencyReasonCode.TLS_FAILED
    if (
        (message.startswith("set ") and "api_key" in message)
        or "not found in environment" in message
        or "not configured" in message
        or "missing" in message
        and ("key" in message or "credential" in message)
    ):
        return DependencyReasonCode.MISSING_CREDENTIALS
    if (
        "authentication" in name
        or "auth" in name
        or "authentication" in message
        or "credentials" in message
    ):
        return DependencyReasonCode.AUTHENTICATION_FAILED
    if isinstance(exc, (ConnectionError, OSError)) or "connection" in name:
        return DependencyReasonCode.CONNECTION_FAILED
    if isinstance(exc, (TypeError, ValueError, AttributeError)):
        return DependencyReasonCode.PROTOCOL_ERROR
    return DependencyReasonCode.CHECK_FAILED


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


def _model_id(item: Any) -> str | None:
    if isinstance(item, dict):
        value = item.get("id")
    else:
        value = getattr(item, "id", None)
    return value if isinstance(value, str) else None


__all__ = [
    "DependencyCheckResult",
    "DependencyHealthMonitor",
    "DependencyReasonCode",
    "DependencyRecord",
    "DependencyStatus",
    "check_executable",
    "check_network_service",
    "check_openai_compatible_endpoint",
    "reason_code_for_exception",
]
