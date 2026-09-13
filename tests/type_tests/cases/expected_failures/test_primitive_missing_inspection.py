"""Opting into the inspection protocol requires its method."""

from dataclasses import dataclass

from roboz import HasExternalDependencies


@dataclass(frozen=True, kw_only=True)
class IncompleteContext(HasExternalDependencies):
    label: str


# Expected: reportAbstractUsage; external_dependencies is not implemented.
IncompleteContext(label="example")
