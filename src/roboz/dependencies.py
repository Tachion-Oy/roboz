"""Resource inheritance and inspection utilities for integration authors."""

from __future__ import annotations

import os
import re
import shutil
import ssl
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote


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
class DependencyFailure:
    """A credential-redacted failed check; successful checks return None."""

    reason_code: DependencyReasonCode
    message: str

    @classmethod
    def from_exception(
        cls, error: BaseException, *, secrets: Iterable[str] = ()
    ) -> DependencyFailure:
        """Retain readable causes, without credentials or traceback locals."""
        chain = tuple(_causes(error))
        reason = reason_code_for_exception(error)
        message = "\nCaused by: ".join(_exception_message(cause) for cause in chain)
        return cls(reason, redact_dependency_message(message, secrets=secrets))


class ExternalDependencyKind(StrEnum):
    """Stable categories understood by dependency inspection."""

    EXECUTABLE = "executable"
    NETWORK_SERVICE = "network_service"
    MODEL_ENDPOINT = "model_endpoint"


class ExternalDependency(ABC):
    """A resource whose availability depends on the surrounding environment.

    Integration authors implement identity, category, safe metadata, and an
    explicit availability check on their resource. Factory authors annotate the
    concrete resource type as ``ctx``.
    """

    @property
    @abstractmethod
    def dependency_id(self) -> str:
        """Return the resource's stable, namespace-qualified identity."""

    @property
    @abstractmethod
    def kind(self) -> ExternalDependencyKind:
        """Return the resource category."""

    @abstractmethod
    def redacted_metadata(self) -> Mapping[str, str]:
        """Return safe, secret-free metadata for inspection."""

    @abstractmethod
    def check(self) -> DependencyFailure | None:
        """Return None when available, otherwise the resource's own diagnosis.

        Perform the resource-specific check without caching. Return a readable,
        credential-redacted failure; use ``DependencyFailure.from_exception``
        to preserve operational errors and their causes. Discovery never checks.
        """

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report this resource without performing external work."""
        return (self,)


@dataclass(frozen=True)
class ExecutableDependency(ExternalDependency):
    """An external executable resolved from ``PATH``."""

    executable: str
    display_name: str | None = None

    def __post_init__(self) -> None:
        """Validate the executable name."""
        if not self.executable.strip():
            raise ValueError("executable must be non-empty")

    @property
    def dependency_id(self) -> str:
        """Return the executable's namespace-qualified identity."""
        return f"executable:{self.executable}"

    @property
    def kind(self) -> ExternalDependencyKind:
        """Return the executable dependency category."""
        return ExternalDependencyKind.EXECUTABLE

    def redacted_metadata(self) -> Mapping[str, str]:
        """Return safe executable metadata for inspection."""
        return {
            "executable": self.executable,
            "display_name": self.display_name or self.executable,
        }

    def check(self) -> DependencyFailure | None:
        """Check PATH without starting the executable; explain resolution failures."""
        try:
            if self.resolve() is not None:
                return None
            return DependencyFailure(
                DependencyReasonCode.NOT_FOUND,
                f"Executable was not found on PATH: {self.executable}",
            )
        except Exception as error:
            return DependencyFailure.from_exception(error)

    def resolve(self) -> Path | None:
        """Resolve the executable without starting a process."""
        resolved = shutil.which(self.executable)
        return Path(resolved) if resolved is not None else None

    def require(self) -> Path:
        """Resolve the executable or fail before command construction."""
        resolved = self.resolve()
        if resolved is None:
            raise FileNotFoundError(
                f"required executable is unavailable: {self.executable}"
            )
        return resolved


def dedupe_external_dependencies(
    resources: Iterable[ExternalDependency],
) -> tuple[ExternalDependency, ...]:
    """Retain the first resource per identity, preserving encounter order.

    Raises:
        TypeError: If any entry is not an ``ExternalDependency`` instance.
    """
    unique: list[ExternalDependency] = []
    seen: set[str] = set()
    for resource in resources:
        if not isinstance(resource, ExternalDependency):
            raise TypeError("external dependencies must be ExternalDependency instances")
        if resource.dependency_id in seen:
            continue
        seen.add(resource.dependency_id)
        unique.append(resource)
    return tuple(unique)


def _causes(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )


def _exception_message(error: BaseException) -> str:
    # SDK status errors often stringify the entire response. Retain only its
    # diagnostic message, not the surrounding request/response payload.
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, Mapping) else None
    if isinstance(detail, Mapping):
        text = str(detail.get("message", "Provider request failed"))
    else:
        text = str(error)
    status = getattr(error, "status_code", None)
    status_text = f" (HTTP {status})" if isinstance(status, int) else ""
    return f"{type(error).__name__}{status_text}: {text}".rstrip(": ")


_CREDENTIAL = re.compile(
    r"(?i)([\"']?\b(?:[\w-]*(?:api[_-]?key|password|secret|token)|authorization|cookie)"
    r"\b[\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&}]+)"
)
_AUTHORIZATION = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s\"',;]+")
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+@")


def redact_dependency_message(message: str, *, secrets: Iterable[str] = ()) -> str:
    """Redact supplied credentials and secret environment values from diagnostics.

    Read only the current process environment; never load or decrypt files.
    Resource checks supply any explicitly configured credentials.
    """
    values = set(secrets)
    values.update(
        value
        for name, value in os.environ.items()
        if name.upper().endswith(("_SECRET", "_PASSWORD", "_API_KEY", "_TOKEN"))
    )
    for value in sorted((value for value in values if value), key=len, reverse=True):
        message = message.replace(value, "[redacted]")
        message = message.replace(quote(value, safe=""), "[redacted]")
    message = _URL_CREDENTIALS.sub(r"\1[redacted]@", message)
    message = _AUTHORIZATION.sub(r"\1 [redacted]", message)
    return _CREDENTIAL.sub(r"\1[redacted]", message)


def reason_code_for_exception(error: BaseException) -> DependencyReasonCode:
    """Classify the underlying cause before a generic wrapping exception."""
    for cause in reversed(tuple(_causes(error))):
        reason = _reason_code(cause)
        if reason is not DependencyReasonCode.CHECK_FAILED:
            return reason
    return DependencyReasonCode.CHECK_FAILED


def _reason_code(exc: BaseException) -> DependencyReasonCode:
    """Map provider and transport failures to a sanitized reason code."""
    if isinstance(exc, TimeoutError):
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
