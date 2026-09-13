"""Optional resource-inspection contract for factory contexts."""

from typing import Protocol

from roboz.dependencies import ExternalDependency


class Context(Protocol):
    """Inspection interface for contexts that explicitly report resources.

    Factories accept any concrete context type. Contexts without this method
    report no dependencies through their tools and need no inheritance or
    inspection boilerplate. Resource-bearing contexts may satisfy this protocol
    structurally by reporting their resources without traversing attributes.
    """

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report current resources without performing external work."""
        ...
