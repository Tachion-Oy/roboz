"""Structural inspection contract for concrete factory contexts."""

from typing import Protocol

from roboz.dependencies import ExternalDependency


class Context(Protocol):
    """Inspection interface satisfied by resources and ordinary typed contexts.

    Concrete contexts need no inheritance. They own their fields, defaults, and
    state, and explicitly report their resources without traversing attributes.
    """

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report current resources without performing external work."""
        ...
