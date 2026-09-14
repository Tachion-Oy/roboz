"""Optional context initialization happens at invocation, preserving typed objects."""

from dataclasses import dataclass, field
from typing import Self

import pytest

from roboz.tooling import Materializable
from roboz.models import Message, Str
from roboz import factory


@dataclass
class PreparedContext(Materializable):
    events: list[str] = field(default_factory=list)
    ready: bool = False
    fail: bool = False

    def materialize(self) -> Self:
        if self.fail:
            raise ValueError("context unavailable")
        if not self.ready:
            self.events.append("initialize")
            self.ready = True
        return self


@factory
def use_context(input: Str, messages: list[Message], ctx: PreparedContext) -> Str:
    assert ctx.ready
    ctx.events.append(input.value)
    return input


def test_binding_copying_and_inspection_defer_until_invocation():
    ctx = PreparedContext()
    bound = use_context(ctx)
    copied = bound.copy()
    assert bound.external_dependencies() == copied.external_dependencies() == ()
    assert ctx.events == []
    assert bound(Str(value="first"), []).value == "first"
    assert ctx.events == ["initialize", "first"]
    copied(Str(value="copied"), [])
    use_context(ctx)(Str(value="rebound"), [])
    assert ctx.events == ["initialize", "first", "copied", "rebound"]
    independent = PreparedContext()
    use_context(independent)(Str(value="separate"), [])
    assert independent.events == ["initialize", "separate"]
    assert ctx.events == ["initialize", "first", "copied", "rebound"]


def test_explicit_materialization_preserves_context_and_initializes_once():
    ctx = PreparedContext()
    assert ctx.materialize() is ctx
    assert ctx.materialize() is ctx
    use_context(ctx)(Str(value="run"), [])
    assert ctx.events == ["initialize", "run"]


def test_initialization_failure_prevents_callable_and_can_be_retried():
    ctx = PreparedContext(fail=True)
    bound = use_context(ctx)
    with pytest.raises(ValueError, match="context unavailable"):
        bound(Str(value="run"), [])
    assert ctx.events == []
    ctx.fail = False
    bound(Str(value="run"), [])
    assert ctx.events == ["initialize", "run"]


def test_plain_aggregate_fields_are_not_implicitly_materialized():
    @dataclass
    class Container:
        child: PreparedContext

    @factory
    def describe(input: Str, messages: list[Message], ctx: Container) -> Str:
        assert not ctx.child.ready
        return input

    ctx = Container(PreparedContext())
    bound = describe(ctx)
    assert bound(Str(value="describe"), []).value == "describe"
    assert ctx.child.events == []
