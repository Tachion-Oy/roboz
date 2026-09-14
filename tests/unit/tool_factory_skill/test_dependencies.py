"""Tool inspection reports explicitly declared resources without external work."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from roboz.models import Message, Str
from roboz import factory, tool
from roboz.dependencies import ExecutableDependency, ExternalDependencyKind


@factory
def dependency_factory(
    input: Str, messages: list[Message], ctx: ExecutableDependency
) -> Str:
    return input


def test_plain_tool_has_no_dependencies():
    @tool
    def plain(input: Str, messages: list[Message]) -> Str:
        return input

    assert plain.external_dependencies() == ()
    assert not hasattr(plain, "dependencies")


def test_factory_binding_copy_and_rebinding_preserve_resources():
    python, shell = ExecutableDependency("python"), ExecutableDependency("sh")
    first = dependency_factory(python)
    for bound in (first, first.copy(), dependency_factory(python)):
        assert bound.external_dependencies()[0] is python
    assert dependency_factory(shell).external_dependencies()[0] is shell


def test_tool_view_deduplicates_by_id_preserving_first_seen_order():
    @factory
    def inspect(input: Str, messages: list[Message], ctx: object) -> Str:
        return input

    first, second = ExecutableDependency("python"), ExecutableDependency("sh")
    context = SimpleNamespace(
        external_dependencies=lambda: (first, second, ExecutableDependency("python"))
    )
    resources = inspect(context).external_dependencies()
    assert resources == (first, second)
    assert resources[0] is first


@pytest.mark.parametrize(
    "result", [[], [ExecutableDependency("python")], (object(),), None]
)
def test_invalid_inspection_is_rejected(result):
    @factory
    def inspect(input: Str, messages: list[Message], ctx: object) -> Str:
        return input

    with pytest.raises(TypeError):
        inspect(
            SimpleNamespace(external_dependencies=lambda: result)
        ).external_dependencies()


def test_binding_does_not_resolve_executable(monkeypatch):
    def forbidden(*args):
        pytest.fail("Binding and inspection must not resolve executables")

    monkeypatch.setattr(ExecutableDependency, "resolve", forbidden)
    resource = ExecutableDependency("python")
    assert dependency_factory(resource).copy().external_dependencies() == (resource,)


def test_executable_dependency_resolves_current_python():
    dependency = ExecutableDependency(sys.executable, display_name="test Python")
    assert dependency.dependency_id == f"executable:{sys.executable}"
    assert dependency.kind is ExternalDependencyKind.EXECUTABLE
    assert dependency.redacted_metadata() == {
        "executable": sys.executable,
        "display_name": "test Python",
    }
    assert isinstance(dependency.resolve(), Path)
    assert dependency.require().exists()
    assert dependency.check()
    with pytest.raises(ValueError, match="executable must be non-empty"):
        ExecutableDependency(" ")
