"""Concrete contexts own their field types, defaults, state and inspection."""

from dataclasses import dataclass, field, FrozenInstanceError
from types import SimpleNamespace

import pytest

from roboz.models import Empty, Message, Str
from roboz import factory
from roboz.dependencies import ExecutableDependency, ExternalDependency


@dataclass(frozen=True)
class PrefixContext:
    prefix: str
    state: list[str] = field(default_factory=list)


@factory
def add_prefix(input: Str, messages: list[Message], ctx: PrefixContext) -> Str:
    ctx.state.append(input.value)
    return Str(value=f"{ctx.prefix}{input.value}")


def test_concrete_context_binds_configuration_and_hides_it_from_schema():
    context = PrefixContext("[agent] ")
    bound = add_prefix(context)
    assert bound(Str(value="done"), []).value == "[agent] done"
    assert bound.external_dependencies() == ()
    assert bound.InputModel is Str
    assert "prefix" not in bound.InputModel.model_fields
    assert "[agent]" not in bound.description


def test_context_constructor_owns_fields_defaults_and_immutability():
    with pytest.raises(TypeError):
        PrefixContext()
    context = PrefixContext("x")
    with pytest.raises(FrozenInstanceError):
        context.prefix = "y"
    with pytest.raises(TypeError):
        PrefixContext(prefix="x", typo="y")


def test_copy_and_rebinding_share_supplied_state_but_new_contexts_are_independent():
    first, second = PrefixContext("one: "), PrefixContext("two: ")
    bound = add_prefix(first)
    for tool in (bound, bound.copy(), add_prefix(first)):
        assert tool(Str(value="text"), []).value == "one: text"
    assert first.state == ["text"] * 3
    assert second.state == []
    assert add_prefix(second)(Str(value="text"), []).value == "two: text"


class ResourceCatalog:
    def __init__(self, *resources: ExternalDependency):
        self.resources = resources
        self.inspections = 0

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        self.inspections += 1
        return self.resources


@factory
def inspect_catalog(
    input: Empty, messages: list[Message], ctx: ResourceCatalog
) -> Empty:
    return input


def test_catalog_inspection_is_live_and_binding_does_not_inspect():
    first, second = ExecutableDependency("first"), ExecutableDependency("second")
    catalog = ResourceCatalog(first)
    bound = inspect_catalog(catalog)
    copied = bound.copy()
    assert catalog.inspections == 0
    assert bound.external_dependencies()[0] is first
    catalog.resources = (second, second)
    assert copied.external_dependencies() == (second,)
    assert bound.external_dependencies()[0] is second


@pytest.mark.parametrize(
    "context", [[], {}, SimpleNamespace(resource=ExecutableDependency("hidden"))]
)
def test_plain_contexts_do_not_traverse_resources(context):
    @factory
    def plain(input: Empty, messages: list[Message], ctx: object) -> Empty:
        return input

    bound = plain(context)
    assert bound.external_dependencies() == ()
    assert bound(Empty(), []) == Empty()
