"""Independent resource and binding contracts, without agent/runtime fixtures."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from inspect import signature
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from pydantic import BaseModel, Field, SerializeAsAny, ValidationError

import roboz
from roboz import Context, Empty, Invoke, Message, Str, factory, tool
from roboz.dependencies import (
    ExecutableDependency,
    ExternalDependency,
    ExternalDependencyKind,
    dedupe_external_dependencies,
)


class IdentityOnly(ExternalDependency):
    @property
    def dependency_id(self) -> str:
        return "test:incomplete"


class MissingMetadata(IdentityOnly):
    @property
    def kind(self) -> ExternalDependencyKind:
        return ExternalDependencyKind.NETWORK_SERVICE


class MissingKind(IdentityOnly):
    def redacted_metadata(self) -> Mapping[str, str]:
        return {}


@dataclass
class SearchIndex(ExternalDependency):
    name: str

    @property
    def dependency_id(self) -> str:
        return f"search:{self.name}"

    @property
    def kind(self) -> ExternalDependencyKind:
        return ExternalDependencyKind.NETWORK_SERVICE

    def redacted_metadata(self) -> Mapping[str, str]:
        return {"name": self.name}


@factory
def describe_program(
    input: Str, messages: list[Message], ctx: ExecutableDependency
) -> Str:
    """Describe the supplied value using the configured program name."""
    return Str(value=f"{ctx.executable}: {input.value}")


@dataclass(frozen=True, kw_only=True)
class ProgramContext:
    executable: ExecutableDependency
    prefix: str = ""

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        return (self.executable,)


@dataclass
class MutableContext:
    resources: tuple[ExternalDependency, ...] = ()
    history: list[str] = field(default_factory=list)
    inspections: int = 0

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        self.inspections += 1
        return self.resources


@factory
def remember_value(input: Str, messages: list[Message], ctx: MutableContext) -> Str:
    """Append the supplied value and return the recorded values."""
    ctx.history.append(input.value)
    return Str(value=", ".join(ctx.history))


@tool
def echo(input: Str, messages: list[Message]) -> Str:
    """Repeat the supplied value.

    Preserve its text exactly.
    """
    return input


@pytest.mark.parametrize(
    "resource_class", [ExternalDependency, IdentityOnly, MissingMetadata, MissingKind]
)
def test_incomplete_resources_cannot_instantiate(resource_class):
    with pytest.raises(TypeError, match="abstract"):
        resource_class()


def test_complete_resource_reports_itself_without_extra_base_classes():
    resource = SearchIndex("docs")
    assert resource.external_dependencies() == (resource,)
    assert resource.external_dependencies()[0] is resource
    assert resource.dependency_id == "search:docs"
    assert resource.redacted_metadata() == {"name": "docs"}


def test_direct_binding_uses_exact_resource_for_execution_and_inspection(monkeypatch):
    def unexpected_resolution(*args, **kwargs):
        pytest.fail("binding, copying, and inspection must not resolve executables")

    monkeypatch.setattr("shutil.which", unexpected_resolution)
    program = ExecutableDependency("python")
    captured = []

    @factory
    def record_program(input: Str, messages: list[Message], ctx: ExecutableDependency) -> Str:
        captured.append(ctx)
        return Str(value=f"{ctx.executable}: {input.value}")

    bound = record_program(program)
    copied = bound.copy()
    assert bound(Str(value="hello"), []).value == "python: hello"
    assert captured[0] is program
    assert bound.external_dependencies()[0] is program
    assert copied.external_dependencies()[0] is program
    assert describe_program(program)(Str(value="hello"), []).value == "python: hello"


def test_executable_identity_metadata_and_explicit_resolution(monkeypatch):
    program = ExecutableDependency("python", display_name="Python")
    assert program.dependency_id == "executable:python"
    assert program.kind is ExternalDependencyKind.EXECUTABLE
    assert program.redacted_metadata() == {"executable": "python", "display_name": "Python"}
    resolved_names = []

    def resolve(name):
        resolved_names.append(name)
        return "/configured/python"

    monkeypatch.setattr("shutil.which", resolve)
    assert program.resolve() == Path("/configured/python")
    assert program.require() == Path("/configured/python")
    assert resolved_names == ["python", "python"]
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert program.resolve() is None
    with pytest.raises(FileNotFoundError, match="required executable is unavailable"):
        program.require()
    with pytest.raises(ValueError, match="non-empty"):
        ExecutableDependency(" ")


def test_aggregate_context_uses_concrete_fields_and_declared_resources():
    @factory
    def prefix_value(input: Str, messages: list[Message], ctx: ProgramContext) -> Str:
        return Str(value=f"{ctx.prefix}{ctx.executable.executable}: {input.value}")

    program = ExecutableDependency("python")
    bound = prefix_value(ProgramContext(executable=program, prefix="> "))
    assert bound(Str(value="hello"), []).value == "> python: hello"
    assert bound.external_dependencies()[0] is program


def test_live_inspection_uses_current_declarations_and_first_resource_per_id():
    first = SearchIndex("first")
    second = SearchIndex("second")
    ctx = MutableContext(resources=(first, second, SearchIndex("first")))
    bound = remember_value(ctx)
    copied = bound.copy()
    assert ctx.inspections == 0
    assert bound.external_dependencies() == (first, second)
    assert ctx.inspections == 1
    ctx.resources = (second, first, second)
    for current in (bound, copied):
        resources = current.external_dependencies()
        assert len(resources) == 2
        assert resources[0] is second
        assert resources[1] is first
    assert ctx.inspections == 3


def test_context_constructor_owns_state_and_copies_and_rebinding_share_it():
    shared = MutableContext()
    independent = MutableContext()
    bound = remember_value(shared)
    copied = bound.copy()
    rebound = remember_value(shared)
    other = remember_value(independent)
    assert bound(Str(value="one"), []).value == "one"
    assert copied(Str(value="two"), []).value == "one, two"
    assert rebound(Str(value="three"), []).value == "one, two, three"
    assert other(Str(value="other"), []).value == "other"
    assert shared.inspections == independent.inspections == 0
    assert bound.id == rebound.id == other.id == remember_value.id
    assert copied.id != bound.id
    assert copied.caller is bound.caller
    assert other.external_dependencies() == ()


def test_unreported_fields_are_not_traversed():
    @dataclass
    class Configuration:
        unused_resource: ExternalDependency

        def external_dependencies(self) -> tuple[ExternalDependency, ...]:
            return ()

    @factory
    def use_configuration(input: Str, messages: list[Message], ctx: Context) -> Str:
        return input

    assert use_configuration(Configuration(SearchIndex("unused"))).external_dependencies() == ()


class NonCallableInspection:
    external_dependencies = ()


@pytest.mark.parametrize("inspection", [None, (), 123])
def test_present_but_non_callable_inspection_fails_at_binding(inspection):
    ctx = NonCallableInspection()
    ctx.external_dependencies = inspection
    with pytest.raises(TypeError, match="external_dependencies must be callable"):
        describe_program(ctx)


@dataclass
class InvalidInspection:
    result: Any

    def external_dependencies(self) -> Any:
        return self.result


@pytest.mark.parametrize("result", [None, [], {}, iter(()), (object(),), (IdentityOnly,)])
def test_invalid_inspection_fails_only_when_inspected(result):
    bound = describe_program(InvalidInspection(result))  # Runtime checks only the interface.
    copied = bound.copy()
    for current in (bound, copied):
        with pytest.raises(TypeError):
            current.external_dependencies()


def test_inspection_errors_propagate_without_binding_or_copying_invoking_it():
    failure = RuntimeError("inspection failed")

    class FailingContext:
        def external_dependencies(self) -> tuple[ExternalDependency, ...]:
            raise failure

    bound = describe_program(FailingContext())
    copied = bound.copy()
    for current in (bound, copied):
        with pytest.raises(RuntimeError) as error:
            current.external_dependencies()
        assert error.value is failure


def test_dedupe_preserves_first_object_and_order_and_rejects_non_resources():
    first, second = SearchIndex("one"), SearchIndex("two")
    result = dedupe_external_dependencies(iter([first, second, SearchIndex("one"), second]))
    assert len(result) == 2
    assert result[0] is first and result[1] is second
    assert dedupe_external_dependencies(()) == ()
    with pytest.raises(TypeError, match="ExternalDependency instances"):
        dedupe_external_dependencies([first, object()])

    class PretendResource:
        dependency_id = first.dependency_id

    with pytest.raises(TypeError, match="ExternalDependency instances"):
        dedupe_external_dependencies([first, PretendResource()])


def test_plain_tool_and_generated_signature_exclude_context():
    bound = describe_program(ExecutableDependency("python"))
    assert echo.external_dependencies() == ()
    assert set(signature(bound.caller).parameters) == {"input", "messages"}
    assert get_type_hints(bound.caller) == {
        "input": Str, "messages": list[Message], "return": Str,
    }
    assert bound.InputModel is Str
    assert "ctx" not in bound.InputModel.model_json_schema()["properties"]
    assert not hasattr(bound, "dependencies")


class Payload(Empty):
    values: list[int]


class Envelope(Empty):
    payload: SerializeAsAny[Empty]
    hidden: str = Field(default="default", exclude=True)


def test_factory_invocation_preserves_nested_types_and_detaches_projected_input():
    @factory
    def inspect_payload(input: Envelope, messages: list[Message], ctx: MutableContext) -> Str:
        assert isinstance(input.payload, Payload)
        input.payload.values.append(2)
        return Str(value=input.hidden)

    original = Envelope(payload=Payload(values=[1]), hidden="excluded")
    result = inspect_payload(MutableContext())(original, [])
    assert result.value == "default"
    assert isinstance(original.payload, Payload)
    assert original.payload.values == [1]


def test_wire_input_projection_and_validation_are_preserved():
    bound = describe_program(ExecutableDependency("python"))
    assert bound(Invoke(action="describe_program", rationale="test", value="ok"), []).value == "python: ok"
    with pytest.raises(ValidationError):
        bound(Invoke(action="describe_program", rationale="test", value=[]), [])


class InvalidOutput(BaseModel):
    pass


def test_invalid_output_model_is_rejected_at_tool_construction():
    @factory
    def invalid_output(input: Str, messages: list[Message], ctx: MutableContext) -> InvalidOutput:
        return InvalidOutput()

    with pytest.raises(ValueError, match="output must be subclass"):
        invalid_output(MutableContext())


def test_description_normalization_is_preserved():
    @factory
    def describe_value(input: Str, messages: list[Message], ctx: MutableContext) -> Str:
        """Describe the supplied value.

        Preserve its text exactly.
        """
        return input

    expected = "Describe the supplied value.\n\nPreserve its text exactly."
    assert describe_value.description == expected
    assert describe_value(MutableContext()).copy().description == expected
    assert echo.description == "Repeat the supplied value.\n\nPreserve its text exactly."


def test_top_level_authoring_api_keeps_resource_implementation_in_its_module():
    assert roboz.factory is factory
    assert not hasattr(roboz, "ExecutableDependency")
    assert not hasattr(roboz, "ExternalDependency")


@pytest.mark.parametrize("parent", [echo, describe_program])
def test_chained_factory_binding_preserves_context_parent_and_predicate(parent):
    def should_chain(output: Str) -> bool:
        return output.value == "ready"

    @factory(chained_to=parent, chain_condition=should_chain)
    def describe_ready_value(
        input: Str, messages: list[Message], ctx: ExecutableDependency
    ) -> Str:
        return Str(value=f"{ctx.executable}: {input.value}")

    program = ExecutableDependency("python")
    bound = describe_ready_value(program)
    copied = bound.copy()
    for current in (bound, copied):
        assert current.chained_to == [parent]
        assert current.chained_to[0] is parent
        assert current.chain_condition is should_chain
        assert current.chain_condition(Str(value="ready")) is True
        assert current.chain_condition(Str(value="waiting")) is False
        assert current.external_dependencies()[0] is program
        assert current(Str(value="ready"), []).value == "python: ready"


def test_parenthesized_factory_binds_an_ordinary_typed_context():
    @factory()
    def prefix_program(
        input: Str, messages: list[Message], ctx: ProgramContext
    ) -> Str:
        return Str(value=ctx.prefix + input.value)

    bound = prefix_program(
        ProgramContext(executable=ExecutableDependency("python"), prefix="> ")
    )
    assert bound(Str(value="hello"), []).value == "> hello"


@dataclass(frozen=True)
class PlainContext:
    prefix: str = ""


def test_plain_context_is_injected_unchanged_and_reports_no_resources():
    captured = []

    @factory
    def prefix_value(input: Str, messages: list[Message], ctx: PlainContext) -> Str:
        captured.append(ctx)
        return Str(value=ctx.prefix + input.value)

    ctx = PlainContext(prefix="> ")
    bound = prefix_value(ctx)
    copied = bound.copy()
    assert bound.external_dependencies() == copied.external_dependencies() == ()
    assert bound(Str(value="one"), []).value == "> one"
    assert copied(Str(value="two"), []).value == "> two"
    assert captured[0] is ctx
    assert captured[1] is ctx


def test_list_context_retains_identity_and_mutation_through_binding_and_copying():
    @factory()
    def remember(input: Str, messages: list[Message], ctx: list[str]) -> Str:
        ctx.append(input.value)
        return Str(value=", ".join(ctx))

    history: list[str] = []
    bound = remember(history)
    copied = bound.copy()
    independent = remember([])
    assert bound.external_dependencies() == copied.external_dependencies() == ()
    assert bound(Str(value="one"), []).value == "one"
    assert copied(Str(value="two"), []).value == "one, two"
    assert history == ["one", "two"]
    assert independent(Str(value="other"), []).value == "other"


def test_plain_context_contents_are_not_automatically_resource_dependencies():
    @factory
    def count_resources(
        input: Str, messages: list[Message], ctx: list[ExternalDependency]
    ) -> Str:
        return Str(value=str(len(ctx)))

    bound = count_resources([ExecutableDependency("python")])
    assert bound.external_dependencies() == ()
    assert bound(Str(value="count"), []).value == "1"
