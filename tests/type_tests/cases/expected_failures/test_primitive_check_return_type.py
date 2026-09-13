"""Resource availability checks must return bool."""

from roboz.dependencies import ExecutableDependency


class InvalidCheck(ExecutableDependency):
    # Expected: reportIncompatibleMethodOverride; check must return bool.
    def check(self) -> str:
        return "available"
