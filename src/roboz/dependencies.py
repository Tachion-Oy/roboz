"""Resource inheritance and inspection utilities for integration authors."""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ExternalDependencyKind(StrEnum):
    """Stable categories understood by dependency inspection."""

    EXECUTABLE = "executable"
    NETWORK_SERVICE = "network_service"
    MODEL_ENDPOINT = "model_endpoint"


class ExternalDependency(ABC):
    """Base for concrete resources supplied directly to typed factories.

    Integration authors implement identity, category, and safe metadata on their
    resource. Factory authors annotate the concrete resource type as ``ctx``.
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
