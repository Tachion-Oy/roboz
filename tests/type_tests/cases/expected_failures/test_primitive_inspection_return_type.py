"""Explicit inspection declarations require resource objects in the returned tuple."""

from roboz import HasExternalDependencies


class InvalidContext(HasExternalDependencies):
    # Expected: reportIncompatibleMethodOverride; entries must be ExternalDependency.
    def external_dependencies(self) -> tuple[str, ...]:
        return ("an endpoint name",)
