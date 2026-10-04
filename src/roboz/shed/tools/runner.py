"""Executable bindings and dependency discovery for guarded commands."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from roboz.dependencies import ExecutableDependency, ExternalDependency
from roboz.tooling.context import HasExternalDependencies


@dataclass(frozen=True)
class ExecutableCommandCatalog(HasExternalDependencies):
    """Immutable command implementations and their inspectable dependencies."""

    bindings: Mapping[str, ExecutableDependency]

    def __post_init__(self) -> None:
        """Validate and freeze executable bindings by command name."""
        bindings = dict(self.bindings)
        for command_name, binding in bindings.items():
            if command_name != binding.executable:
                raise ValueError(
                    "command binding must use its executable name: "
                    f"{command_name!r} != {binding.executable!r}"
                )
        object.__setattr__(self, "bindings", MappingProxyType(bindings))

    @classmethod
    def from_names(cls, names: Iterable[str]) -> "ExecutableCommandCatalog":
        """Build a command catalog from unique executable names."""
        bindings: dict[str, ExecutableDependency] = {}
        for name in names:
            if name in bindings:
                raise ValueError(f"duplicate executable command: {name}")
            bindings[name] = ExecutableDependency(name)
        return cls(bindings)

    def binding_for(self, command_name: str) -> ExecutableDependency:
        """Return the declared binding for a resolved command."""
        try:
            return self.bindings[command_name]
        except KeyError as error:
            raise RuntimeError(
                f"resolved command has no implementation: {command_name}"
            ) from error

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Return all executable dependencies in catalog order."""
        return tuple(self.bindings.values())
