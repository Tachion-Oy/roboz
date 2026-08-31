from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, cast

import pytest

from roboz.models import Message, Str
from roboz.tooling.decorators import factory, tool
from roboz.tooling.dependencies import (
    ExecutableDependency,
    ExternalDependencyKind,
    FactoryCtx,
    LazyExternalDependency,
    ToolDependency,
)


@dataclass(frozen=True)
class DependencyContext(FactoryCtx):
    executable: ToolDependency[ExecutableDependency]
    label: str = "ordinary field"


@factory
def dependency_factory(
    input: Str, messages: list[Message], ctx: DependencyContext
) -> Str:
    return input


def test_plain_tool_has_no_dependencies() -> None:
    @tool
    def plain(input: Str, messages: list[Message]) -> Str:
        return input

    assert plain.dependencies == ()
    assert plain.external_dependencies == ()


def test_factory_rejects_arbitrary_context() -> None:
    with pytest.raises(TypeError, match="FactoryCtx"):
        dependency_factory(cast(Any, {"executable": "python"}))


def test_context_ignores_ordinary_fields() -> None:
    executable = ExecutableDependency("python")
    context = DependencyContext(
        executable=ToolDependency(executable),
        label="not a dependency",
    )

    assert dependency_factory(context).dependencies == (ToolDependency(executable),)


def test_tool_view_deduplicates_by_id_preserving_first_seen_order() -> None:
    @dataclass(frozen=True)
    class MultiDependencyContext(FactoryCtx):
        first: ToolDependency[ExecutableDependency]
        rest: tuple[
            ToolDependency[ExecutableDependency]
            | ToolDependency[LazyExternalDependency[ExecutableDependency]],
            ...,
        ]

    @factory
    def multi_dependency_factory(
        input: Str, messages: list[Message], ctx: MultiDependencyContext
    ) -> Str:
        return input

    executable = ExecutableDependency("python", display_name="Python")
    compatible = MultiDependencyContext(
        first=ToolDependency(executable),
        rest=(ToolDependency(executable),),
    )
    compatible_tool = multi_dependency_factory(compatible)
    assert compatible_tool.dependencies == (
        ToolDependency(executable),
        ToolDependency(executable),
    )
    assert compatible_tool.external_dependencies == (executable,)

    shell = ExecutableDependency("sh")
    renamed = MultiDependencyContext(
        first=ToolDependency(executable),
        rest=(
            ToolDependency(shell),
            ToolDependency(
                ExecutableDependency("python", display_name="Different Python")
            ),
        ),
    )
    assert multi_dependency_factory(renamed).external_dependencies == (
        executable,
        shell,
    )


def test_dependency_primitives_validate_empty_and_invalid_values() -> None:
    with pytest.raises(ValueError, match="executable must be non-empty"):
        ExecutableDependency(" ")
    with pytest.raises(TypeError, match="must be an ExternalDependency"):
        ToolDependency(cast(Any, object()))
    with pytest.raises(ValueError, match="dependency_id must be non-empty"):
        LazyExternalDependency(
            dependency_id_value=" ",
            dependency_kind=ExternalDependencyKind.EXECUTABLE,
            metadata={},
            resolver=lambda: ExecutableDependency("python"),
        )


def test_factory_binding_copy_and_rebinding_are_independent() -> None:
    python = ExecutableDependency("python")
    shell = ExecutableDependency("sh")
    python_binding = ToolDependency(python)
    shell_binding = ToolDependency(shell)

    python_tool = dependency_factory(DependencyContext(python_binding))
    python_copy = python_tool.copy(name="python_copy")
    shell_tool = dependency_factory(DependencyContext(shell_binding))

    assert python_tool.dependencies == (python_binding,)
    assert python_copy.dependencies == (python_binding,)
    assert python_copy.external_dependencies == (python,)
    assert shell_tool.dependencies == (shell_binding,)
    assert shell_tool.external_dependencies == (shell,)
    assert python_tool.dependencies == (python_binding,)


def test_lazy_dependency_is_not_resolved_during_binding_and_resolves_once() -> None:
    calls = 0

    def resolve() -> ExecutableDependency:
        nonlocal calls
        calls += 1
        return ExecutableDependency("python")

    lazy = LazyExternalDependency(
        dependency_id_value="executable:python",
        dependency_kind=ExternalDependencyKind.EXECUTABLE,
        metadata={"executable": "python"},
        resolver=resolve,
    )

    @dataclass(frozen=True)
    class LazyContext(FactoryCtx):
        dependency: ToolDependency[LazyExternalDependency[ExecutableDependency]]

    @factory
    def lazy_factory(input: Str, messages: list[Message], ctx: LazyContext) -> Str:
        return input

    bound = lazy_factory(LazyContext(ToolDependency(lazy)))
    assert bound.external_dependencies == (lazy,)
    assert calls == 0

    assert lazy.materialize() == ExecutableDependency("python")
    assert lazy.materialize() == ExecutableDependency("python")
    assert calls == 1


def test_lazy_dependency_rejects_resolved_id_mismatch() -> None:
    lazy = LazyExternalDependency(
        dependency_id_value="executable:python",
        dependency_kind=ExternalDependencyKind.EXECUTABLE,
        metadata={},
        resolver=lambda: ExecutableDependency("sh"),
    )

    with pytest.raises(ValueError, match="different dependency id"):
        lazy.materialize()


def test_lazy_dependency_rejects_resolved_kind_mismatch() -> None:
    resolved = LazyExternalDependency(
        dependency_id_value="executable:python",
        dependency_kind=ExternalDependencyKind.MODEL_ENDPOINT,
        metadata={},
        resolver=lambda: ExecutableDependency("python"),
    )
    lazy = LazyExternalDependency(
        dependency_id_value="executable:python",
        dependency_kind=ExternalDependencyKind.EXECUTABLE,
        metadata={},
        resolver=lambda: resolved,
    )

    with pytest.raises(ValueError, match="different dependency kind"):
        lazy.materialize()


def test_executable_dependency_resolves_current_python() -> None:
    dependency = ExecutableDependency(sys.executable, display_name="test Python")

    assert dependency.materialize() is dependency
    resolved = dependency.resolve()

    assert dependency.dependency_id == f"executable:{sys.executable}"
    assert dependency.kind is ExternalDependencyKind.EXECUTABLE
    assert dependency.redacted_metadata() == {
        "executable": sys.executable,
        "display_name": "test Python",
    }
    assert isinstance(resolved, Path)
    assert resolved.exists()
