"""A resource must implement check before it can be instantiated."""

from collections.abc import Mapping

from roboz.dependencies import ExternalDependency, ExternalDependencyKind


class MissingCheck(ExternalDependency):
    @property
    def dependency_id(self) -> str:
        return "test:missing-check"

    @property
    def kind(self) -> ExternalDependencyKind:
        return ExternalDependencyKind.NETWORK_SERVICE

    def redacted_metadata(self) -> Mapping[str, str]:
        return {}


# Expected: reportAbstractUsage; check is not implemented.
MissingCheck()
