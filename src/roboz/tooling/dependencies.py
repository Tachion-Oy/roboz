"""External operational dependencies injected into factory-built tools."""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping


class ExternalDependencyKind(StrEnum):
    """Stable categories understood by deployment-level dependency catalogs."""

    EXECUTABLE = "executable"
    NETWORK_SERVICE = "network_service"
    MODEL_ENDPOINT = "model_endpoint"


class ExternalDependency(ABC):
    """A process-external resource whose availability may change independently."""

    @property
    @abstractmethod
    def dependency_id(self) -> str:
        """Globally unique, namespace-qualified identity for this dependency.

        IDs use a kind-specific namespace such as ``executable:``, ``network:``,
        or ``model:``. Equal IDs identify the same logical dependency regardless
        of object instance or cosmetic metadata.
        """

    @property
    @abstractmethod
    def kind(self) -> ExternalDependencyKind:
        """Operational category used for health checks and registration matching."""

    @abstractmethod
    def redacted_metadata(self) -> Mapping[str, str]:
        """Safe, secret-free metadata for diagnostics and catalogs."""

    def materialize(self) -> ExternalDependency:
        """Return this already-materialized dependency."""
        return self


class ExternalDependencySource(ABC):
    """An object whose current graph exposes external dependencies."""

    __slots__ = ()

    @abstractmethod
    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Derive external dependencies from the object's current graph."""


@dataclass(frozen=True)
class ExecutableDependency(ExternalDependency):
    """An executable resolved from ``PATH`` and injected into a Tool factory."""

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


class NetworkServiceDependency(ExternalDependency):
    """Base for injected external service adapters such as IMAP or RAG clients."""

    @property
    def kind(self) -> ExternalDependencyKind:
        """Return the network-service dependency category."""
        return ExternalDependencyKind.NETWORK_SERVICE


class ModelEndpointDependency(ExternalDependency):
    """Base for model endpoints used by LLM and transcription tools."""

    @property
    def kind(self) -> ExternalDependencyKind:
        """Return the model-endpoint dependency category."""
        return ExternalDependencyKind.MODEL_ENDPOINT


@dataclass(frozen=True)
class LazyExternalDependency[TExternal: ExternalDependency](ExternalDependency):
    """Inspectable dependency identity with deferred, cached construction."""

    dependency_id_value: str
    dependency_kind: ExternalDependencyKind
    metadata: Mapping[str, str]
    resolver: Callable[[], TExternal]

    def __post_init__(self) -> None:
        """Validate and freeze the lazy dependency's public metadata."""
        if not self.dependency_id_value.strip():
            raise ValueError("dependency_id must be non-empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def dependency_id(self) -> str:
        """Return the declared identity without resolving the dependency."""
        return self.dependency_id_value

    @property
    def kind(self) -> ExternalDependencyKind:
        """Return the declared category without resolving the dependency."""
        return self.dependency_kind

    def redacted_metadata(self) -> Mapping[str, str]:
        """Return a detached copy of safe dependency metadata."""
        return dict(self.metadata)

    @cached_property
    def materialized(self) -> TExternal:
        """Resolve, validate, and cache the concrete dependency."""
        resource = self.resolver()
        self._validate_resolved(resource)
        return resource

    def materialize(self) -> TExternal:
        """Return the cached concrete dependency, resolving it if needed."""
        return self.materialized

    def _validate_resolved(self, resource: TExternal) -> None:
        if resource.dependency_id != self.dependency_id:
            raise ValueError(
                "lazy dependency resolver returned a different dependency id: "
                f"{resource.dependency_id!r} != {self.dependency_id!r}"
            )
        if resource.kind != self.kind:
            raise ValueError(
                "lazy dependency resolver returned a different dependency kind: "
                f"{resource.kind!r} != {self.kind!r}"
            )


def dedupe_external_dependencies(
    resources: Iterable[ExternalDependency],
) -> tuple[ExternalDependency, ...]:
    """Preserve the first resource for each globally unique ``dependency_id``."""
    unique: list[ExternalDependency] = []
    seen: set[str] = set()
    for resource in resources:
        if resource.dependency_id in seen:
            continue
        seen.add(resource.dependency_id)
        unique.append(resource)
    return tuple(unique)
