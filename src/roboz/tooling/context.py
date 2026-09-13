"""Optional inspection and initialization capabilities for concrete contexts."""

from abc import abstractmethod
from typing import Protocol, Self, runtime_checkable

from roboz.dependencies import ExternalDependency


class HasExternalDependencies(Protocol):
    """Inspection interface for objects that explicitly report external resources.

    Factories accept any concrete context type. Contexts without this method
    report no dependencies through their tools and need no inheritance or
    inspection boilerplate. Resource-bearing contexts may satisfy this protocol
    structurally by reporting their resources without traversing attributes.
    Explicitly inherit this protocol to declare that a context must implement
    inspection: missing implementations then fail both type checking and
    instantiation. This opt-in requirement does not apply to plain contexts.
    """

    @abstractmethod
    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report current resources without performing external work."""
        ...


@runtime_checkable
class Materializable(Protocol):
    """Optional initialization hook called immediately before a factory runs.

    Keep initialization idempotent and return the same object. Binding, copying,
    and inspection never invoke this hook. Aggregate contexts explicitly
    initialize the resources they need; arbitrary fields are not traversed.
    """

    @abstractmethod
    def materialize(self) -> Self:
        """Initialize deferred state and return this object; propagate failures."""
        ...
