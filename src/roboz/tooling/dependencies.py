"""External operational dependencies injected into factory-built tools."""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from collections.abc import Iterable
from typing import Any, Callable, Mapping


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
        return self


class ExternalDependencySource(ABC):
    """An object whose current graph exposes external dependencies."""

    @abstractmethod
    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Derive external dependencies from the object's current graph."""


@dataclass(frozen=True)
class ExecutableDependency(ExternalDependency):
    """An executable resolved from ``PATH`` and injected into a Tool factory."""

    executable: str
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise ValueError("executable must be non-empty")

    @property
    def dependency_id(self) -> str:
        return f"executable:{self.executable}"

    @property
    def kind(self) -> ExternalDependencyKind:
        return ExternalDependencyKind.EXECUTABLE

    def redacted_metadata(self) -> Mapping[str, str]:
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
        return ExternalDependencyKind.NETWORK_SERVICE


class ModelEndpointDependency(ExternalDependency):
    """Base for model endpoints used by LLM and transcription tools."""

    @property
    def kind(self) -> ExternalDependencyKind:
        return ExternalDependencyKind.MODEL_ENDPOINT


@dataclass(frozen=True)
class LazyExternalDependency[TExternal: ExternalDependency](ExternalDependency):
    """Inspectable dependency identity with deferred, cached construction."""

    dependency_id_value: str
    dependency_kind: ExternalDependencyKind
    metadata: Mapping[str, str]
    resolver: Callable[[], TExternal]

    def __post_init__(self) -> None:
        if not self.dependency_id_value.strip():
            raise ValueError("dependency_id must be non-empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def dependency_id(self) -> str:
        return self.dependency_id_value

    @property
    def kind(self) -> ExternalDependencyKind:
        return self.dependency_kind

    def redacted_metadata(self) -> Mapping[str, str]:
        return dict(self.metadata)

    @cached_property
    def materialized(self) -> TExternal:
        resource = self.resolver()
        self._validate_resolved(resource)
        return resource

    def materialize(self) -> TExternal:
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


@dataclass(frozen=True)
class ToolDependency[TExternal: ExternalDependency]:
    """One Tool's binding to an injected external dependency."""

    resource: TExternal

    def __post_init__(self) -> None:
        if not isinstance(self.resource, ExternalDependency):
            raise TypeError("ToolDependency.resource must be an ExternalDependency")


@dataclass(frozen=True)
class FactoryCtx:
    """Immutable marker base required by every ``@factory`` context."""


def factory_context_dependencies(
    context: FactoryCtx,
) -> tuple[
    tuple[ToolDependency[Any], ...],
    tuple[ExternalDependencySource, ...],
]:
    """Extract direct bindings and live sources from a factory context."""

    dependencies: list[ToolDependency[Any]] = []
    sources: list[ExternalDependencySource] = []
    for field in fields(context):
        value = getattr(context, field.name)
        candidates = value if isinstance(value, tuple) else (value,)
        for candidate in candidates:
            if isinstance(candidate, ToolDependency):
                dependencies.append(candidate)
            elif isinstance(candidate, ExternalDependencySource):
                sources.append(candidate)
    return tuple(dependencies), tuple(sources)


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
