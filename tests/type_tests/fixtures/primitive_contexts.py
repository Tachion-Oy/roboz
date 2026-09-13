"""Concrete contexts used by the independent factory typing contracts."""

from dataclasses import dataclass

from roboz.dependencies import ExecutableDependency, ExternalDependency


@dataclass(frozen=True, kw_only=True)
class ProgramContext:
    executable: ExecutableDependency
    prefix: str = ""

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        return (self.executable,)
