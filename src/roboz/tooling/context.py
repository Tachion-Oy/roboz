"""Keyword-built context for configuration, resources, and shared tool state."""

from collections.abc import Callable, Mapping
from keyword import iskeyword
from types import MappingProxyType
from typing import Any

from roboz.tooling.dependencies import (
    ExternalDependency,
    ExternalDependencySource,
    dedupe_external_dependencies,
)


class Ctx(ExternalDependencySource):
    """Named values bound to a tool without a user-defined context class.

    Construct with keyword arguments and read values as attributes. Bindings are
    immutable, while supplied objects retain their identity and mutability.
    Dynamic fields are not checked against a schema or inferred by static typing.
    """

    __slots__ = ("_values",)
    _values: Mapping[str, Any]

    def __init__(self, **values: Any) -> None:
        """Bind public attribute names to the supplied objects without copying them."""
        for key in values:
            if (
                not key.isidentifier()
                or iskeyword(key)
                or key.startswith("_")
                or hasattr(type(self), key)
            ):
                raise ValueError(f"invalid context field name: {key!r}")
        object.__setattr__(self, "_values", MappingProxyType(values))

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Inspect direct resources and the current dependencies of live sources.

        Direct fields and direct tuple entries precede live-source resources.
        Deduplicate by dependency ID, retaining the first object. Inspection
        never copies or materializes resources or runs health checks.
        """
        dependencies, sources = self._collect_dependencies()
        candidates = list(dependencies)
        for source in sources:
            candidates.extend(source.external_dependencies())
        return dedupe_external_dependencies(candidates)

    def _collect_dependencies(
        self,
    ) -> tuple[tuple[ExternalDependency, ...], tuple[ExternalDependencySource, ...]]:
        """Separate direct resources from live sources without inspecting sources."""
        dependencies: list[ExternalDependency] = []
        sources: list[ExternalDependencySource] = []
        for value in self._values.values():
            candidates = value if isinstance(value, tuple) else (value,)
            for candidate in candidates:
                if isinstance(candidate, ExternalDependency):
                    dependencies.append(candidate)
                elif isinstance(candidate, ExternalDependencySource):
                    sources.append(candidate)
        return tuple(dependencies), tuple(sources)

    def __getattr__(self, name: str) -> Any:
        """Read a supplied field or report its missing name."""
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(f"Ctx has no field {name!r}") from None

    def __setattr__(self, name: str, value: Any) -> None:
        """Reject reassignment of context bindings."""
        raise AttributeError(f"Ctx bindings are immutable: {name!r}")

    def __delattr__(self, name: str) -> None:
        """Reject deletion of context bindings."""
        raise AttributeError(f"Ctx bindings are immutable: {name!r}")


def _prepare_context(
    ctx: Ctx,
    *,
    required: tuple[str, ...],
    defaults: Mapping[str, Any] | None = None,
    default_factories: Mapping[str, Callable[[], Any]] | None = None,
) -> Ctx:
    """Complete a built-in tool context while retaining explicitly supplied values.

    Default factories run once at binding, only for omitted fields. No resource
    or state object supplied by the caller is copied or materialized.
    """
    missing = [key for key in required if key not in ctx._values]
    if missing:
        raise TypeError(f"missing required context fields: {', '.join(missing)}")
    values = dict(defaults or {}) | dict(ctx._values)
    for key, create in (default_factories or {}).items():
        if key not in values:
            values[key] = create()
    return Ctx(**values)
