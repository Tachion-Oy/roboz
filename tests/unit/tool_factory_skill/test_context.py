"""Public keyword context behavior and binding defaults."""

from types import SimpleNamespace

import pytest

import roboz as rz
from roboz.agent.background_agent import run_background_agent
from roboz.tools import sleep_between_runs


@rz.factory
def add_prefix(input: rz.Str, messages: list[rz.Message], ctx: rz.Ctx) -> rz.Str:
    """Prefix the supplied text with the configured label."""
    return rz.Str(value=f"{ctx.prefix}{input.value}")


def test_keyword_context_binds_configuration_without_a_subclass() -> None:
    bound = add_prefix(rz.Ctx(prefix="[agent] "))
    assert bound(rz.Str(value="done"), []).value == "[agent] done"
    assert bound.dependencies == ()
    assert bound.InputModel is rz.Str
    assert "prefix" not in bound.InputModel.model_fields
    assert "[agent]" not in bound.description


@pytest.mark.parametrize("field", ["bad-key", "_values", "__dict__", "class", ""])
def test_context_rejects_unusable_attribute_names(field: str) -> None:
    with pytest.raises(ValueError, match="context field name"):
        rz.Ctx(**{field: "value"})


def test_context_is_shallowly_immutable_and_retains_object_identity() -> None:
    state = []
    resource = object()
    ctx = rz.Ctx(state=state, resource=resource)
    assert ctx.resource is resource
    assert ctx.state is state
    ctx.state.append("called")
    assert state == ["called"]
    with pytest.raises(AttributeError, match="immutable"):
        ctx.resource = object()
    with pytest.raises(AttributeError, match="immutable"):
        ctx.extra = 1
    with pytest.raises(AttributeError, match="immutable"):
        del ctx.state
    with pytest.raises(AttributeError, match="missing"):
        _ = ctx.missing


def test_empty_context_and_fields_named_after_dependency_concepts() -> None:
    assert not hasattr(rz.Ctx(), "endpoint")
    ctx = rz.Ctx(dependencies="configuration", values=42)
    assert ctx.dependencies == "configuration"
    assert ctx.values == 42


def test_builtin_required_fields_fail_at_binding() -> None:
    with pytest.raises(TypeError, match="timeout_reply"):
        rz.prompt_user(rz.Ctx())


def test_builtin_default_configuration_is_preserved() -> None:
    result = sleep_between_runs(rz.Ctx(seconds=0))(rz.All(), [])
    assert isinstance(result, rz.Str)
    assert result.value == "sleep_between_runs: slept=0.0s"


def test_tool_copy_retains_explicitly_supplied_state() -> None:
    class Thread:
        def is_alive(self):
            return True

    # A supplied running-thread boundary avoids starting any worker here.
    state = SimpleNamespace(checks=0, thread=Thread(), started_monotonic=1.0)
    agent = SimpleNamespace(name="child")
    first = run_background_agent(rz.Ctx(agent=agent, state=state))
    copied = first.copy()
    assert first(rz.Empty(), []).checks == 1
    assert copied(rz.Empty(), []).checks == 2
    assert state.checks == 2


def test_factory_context_type_is_enforced_at_runtime() -> None:
    with pytest.raises(TypeError, match="Ctx"):
        add_prefix({"prefix": "x"})  # type: ignore[arg-type]


def test_separate_bindings_capture_their_own_configuration() -> None:
    first = add_prefix(rz.Ctx(prefix="one: "))
    second = add_prefix(rz.Ctx(prefix="two: "))
    assert first(rz.Str(value="text"), []).value == "one: text"
    assert second(rz.Str(value="text"), []).value == "two: text"
