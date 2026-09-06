"""Dependency contracts exercised by deployment registration and health consumers.

The consumer below follows PeffaHub's ID/kind matching and explicit checker
dispatch. It uses Roboz resources and needs no application checkout or service.
"""

import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

import roboz as rz
from roboz.llm import LLMEndpoint, resolve_endpoint
from roboz.tooling.dependencies import dedupe_external_dependencies


@dataclass(frozen=True)
class Registration:
    dependency_id: str
    kind: rz.ExternalDependencyKind
    check: Callable[[rz.ExternalDependency], object]


def bind_checks(resources: Iterable[rz.ExternalDependency], registrations):
    registered = {item.dependency_id: item for item in registrations}
    if len(registered) != len(registrations):
        raise ValueError("duplicate registration")
    resources = dedupe_external_dependencies(resources)
    if {r.dependency_id: r.kind for r in resources} != {
        key: entry.kind for key, entry in registered.items()
    }:
        raise ValueError("dependency contract mismatch")
    return tuple(
        (resource, registered[resource.dependency_id].check) for resource in resources
    )


@rz.factory
def use_resources(input: rz.Empty, messages: list[rz.Message], ctx: rz.Ctx) -> rz.Empty:
    return input


def test_discovery_binds_exact_resource_to_checker_without_running_it() -> None:
    checks = []
    dependency = rz.ExecutableDependency(sys.executable)
    ctx = rz.Ctx(executable=dependency)
    bound = use_resources(ctx)
    registration = Registration(
        dependency.dependency_id, dependency.kind, checks.append
    )
    pairs = bind_checks(ctx.external_dependencies(), [registration])
    assert pairs == bind_checks(bound.copy().external_dependencies, [registration])
    assert checks == []
    resource, check = pairs[0]
    assert resource is dependency
    assert resource.redacted_metadata() == dependency.redacted_metadata()
    check(resource)
    assert checks == [dependency]


@pytest.mark.parametrize("mismatch", ["missing", "stale", "kind", "duplicate"])
def test_registry_mismatches_fail_before_any_checks(mismatch) -> None:
    checks = []
    dependency = rz.ExecutableDependency("python")
    registrations = [
        Registration(dependency.dependency_id, dependency.kind, checks.append)
    ]
    if mismatch == "missing":
        registrations = []
    elif mismatch == "stale":
        registrations.append(
            Registration("executable:stale", dependency.kind, checks.append)
        )
    elif mismatch == "kind":
        registrations = [
            Registration(
                dependency.dependency_id,
                rz.ExternalDependencyKind.NETWORK_SERVICE,
                checks.append,
            )
        ]
    else:
        registrations *= 2
    with pytest.raises(ValueError, match="mismatch|duplicate"):
        bind_checks(
            use_resources(rz.Ctx(executable=dependency)).external_dependencies,
            registrations,
        )
    assert checks == []


def test_endpoint_route_is_retained_and_materializes_the_current_selection() -> None:
    first = LLMEndpoint(client=object(), api_name="test", model_name="first")
    second = LLMEndpoint(client=object(), api_name="test", model_name="second")
    selected = first
    calls = []

    class EndpointRoute(rz.LazyExternalDependency[LLMEndpoint]):
        def materialize(self) -> LLMEndpoint:
            calls.append(selected.model_name)
            return selected

    route = EndpointRoute(
        dependency_id_value=first.dependency_id,
        dependency_kind=first.kind,
        metadata=first.redacted_metadata(),
        resolver=lambda: selected,
    )

    @rz.factory
    def inspect_selected(
        input: rz.Empty, messages: list[rz.Message], ctx: rz.Ctx
    ) -> rz.Str:
        return rz.Str(value=resolve_endpoint(ctx.endpoint).model_name)

    ctx = rz.Ctx(endpoint=route)
    assert ctx.external_dependencies()[0] is route
    assert calls == []
    bound = inspect_selected(ctx)
    copied = bound.copy()
    assert copied.external_dependencies[0] is route
    assert calls == []
    assert bound(rz.Empty(), []).value == "first"
    selected = second
    assert ctx.external_dependencies()[0] is route
    assert calls == ["first"]
    assert copied(rz.Empty(), []).value == "second"
    assert bound.external_dependencies[0].dependency_id == first.dependency_id
    assert calls == ["first", "second"]


def test_direct_executable_declaration_is_the_command_used(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs))
    )

    @rz.factory
    def convert(input: rz.Str, messages: list[rz.Message], ctx: rz.Ctx) -> rz.Str:
        subprocess.run([ctx.converter.require(), input.value], check=True)
        return input

    executable = rz.ExecutableDependency(sys.executable)
    bound = convert(rz.Ctx(converter=executable))
    assert bound.external_dependencies == (executable,)
    assert calls == []
    assert bound(rz.Str(value="payload"), []).value == "payload"
    assert calls == [([executable.require(), "payload"], {"check": True})]
    assert isinstance(calls[0][0][0], Path)
