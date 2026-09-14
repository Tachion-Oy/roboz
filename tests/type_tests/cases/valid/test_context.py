from dataclasses import dataclass
from typing import assert_type

from roboz import Factory, Tool, factory
from roboz.tooling import HasExternalDependencies
from roboz.models import Message, Str
from roboz.dependencies import ExecutableDependency, ExternalDependency


@dataclass(frozen=True)
class PrefixContext:
    prefix: str


@factory
def add_prefix(input: Str, messages: list[Message], ctx: PrefixContext) -> Str:
    return Str(value=f"{ctx.prefix}{input.value}")


assert_type(add_prefix, Factory[Str, Str, PrefixContext])
assert_type(add_prefix(PrefixContext("hello")), Tool[Str, Str])


def inspect_source(source: HasExternalDependencies) -> tuple[ExternalDependency, ...]:
    return source.external_dependencies()


assert_type(
    inspect_source(ExecutableDependency("python")), tuple[ExternalDependency, ...]
)
