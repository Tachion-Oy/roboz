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


@pytest.mark.parametrize(
    "field", ["bad-key", "_values", "__dict__", "class", "", "external_dependencies"]
)
def test_context_rejects_unusable_attribute_names(field: str) -> None:
    with pytest.raises(ValueError, match="context field name"):
        rz.Ctx(**{field: "value"})


def test_context_is_shallowly_immutable_and_retains_object_identity() -> None:
    state = []
    resource = object()
    ctx = rz.Ctx(state=state, resource=resource)
    assert not hasattr(ctx, "__dict__")
    with pytest.raises(AttributeError):
        object.__setattr__(ctx, "extra", 1)
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
    assert rz.Ctx().external_dependencies() == ()
    assert not hasattr(rz.Ctx(), "endpoint")
    ctx = rz.Ctx(dependencies="configuration", values=42)
    assert ctx.dependencies == "configuration"
    assert ctx.values == 42
    assert ctx.external_dependencies() == ()


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


class ResourceCatalog(rz.ExternalDependencySource):
    def __init__(self, *resources: rz.ExternalDependency) -> None:
        self.resources = resources
        self.inspections = 0

    def external_dependencies(self) -> tuple[rz.ExternalDependency, ...]:
        self.inspections += 1
        return self.resources


def test_context_discovers_direct_resources_before_live_sources() -> None:
    first = rz.ExecutableDependency("first")
    duplicate = rz.ExecutableDependency("first", display_name="duplicate")
    second = rz.ExecutableDependency("second")
    third = rz.ExecutableDependency("third")
    fourth = rz.ExecutableDependency("fourth")
    catalog = ResourceCatalog(duplicate, third)
    ctx = rz.Ctx(
        catalog=catalog,
        nested=rz.Ctx(resource=fourth),
        first=first,
        entries=(second, duplicate, catalog, "config", (fourth,)),
        ignored_list=[fourth],
        ignored_mapping={"resource": fourth},
        ignored_object=SimpleNamespace(resource=fourth),
    )
    source: rz.ExternalDependencySource = ctx
    assert isinstance(ctx, rz.ExternalDependencySource)
    resources = source.external_dependencies()
    assert resources == (first, second, third, fourth)
    assert resources[0] is first
    assert resources[1] is second
    assert resources[2] is third
    assert resources[3] is fourth
    bound = add_prefix(ctx)
    assert bound.dependencies == (first, second, duplicate)
    assert bound.external_dependencies == resources


def test_context_does_not_walk_arbitrary_containers_or_objects() -> None:
    resource = rz.ExecutableDependency("hidden")
    ctx = rz.Ctx(
        sequence=[resource],
        mapping={"resource": resource},
        object=SimpleNamespace(resource=resource),
        nested_tuple=((resource,),),
    )
    assert ctx.external_dependencies() == ()


def test_catalog_is_inspected_only_on_discovery_and_stays_live() -> None:
    first = rz.ExecutableDependency("first")
    second = rz.ExecutableDependency("second")
    catalog = ResourceCatalog(first)
    ctx = rz.Ctx(catalog=catalog)
    bound = add_prefix(ctx)
    copied = bound.copy()
    assert catalog.inspections == 0
    assert bound.dependencies == ()
    assert ctx.external_dependencies() == (first,)
    catalog.resources = (second,)
    assert ctx.external_dependencies() == (second,)
    assert bound.external_dependencies[0] is second
    assert copied.external_dependencies[0] is second


def test_discovery_preserves_immutable_method_binding() -> None:
    ctx = rz.Ctx()
    with pytest.raises(AttributeError, match="immutable"):
        ctx.external_dependencies = lambda: ()
    with pytest.raises(AttributeError, match="immutable"):
        del ctx.external_dependencies
    assert ctx.external_dependencies() == ()
