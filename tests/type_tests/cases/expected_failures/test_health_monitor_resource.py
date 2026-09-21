"""The monitor requires actual resources, each owning its availability check."""

from roboz.shed.dependency_health import DependencyHealthMonitor


# Expected: reportArgumentType; object is not an ExternalDependency.
DependencyHealthMonitor([object()])
