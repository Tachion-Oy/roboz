"""Resource availability checks must return DependencyFailure or None."""

from roboz.dependencies import ExecutableDependency


class InvalidCheck(ExecutableDependency):
    # Expected: reportIncompatibleMethodOverride; check must return DependencyFailure or None.
    def check(self) -> str:
        return "available"
