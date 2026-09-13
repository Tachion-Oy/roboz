"""The monitor requires actual resources, each owning its availability check."""

from roboshed.dependency_health import DependencyHealthMonitor


# Expected: reportArgumentType; object is not an ExternalDependency.
DependencyHealthMonitor([object()])
