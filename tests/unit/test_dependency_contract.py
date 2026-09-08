import pytest

from roboz.dependencies import (
    DependencyContractError,
    DependencyRegistration,
    ExecutableDependency,
    ExternalDependencyKind,
    LazyExternalDependency,
    bind_dependencies,
)


def _registration(
    name: str,
    *,
    kind: ExternalDependencyKind = ExternalDependencyKind.EXECUTABLE,
    check=lambda dependency: True,
) -> DependencyRegistration:
    return DependencyRegistration(f"executable:{name}", kind, check)


def test_registry_requires_exact_discovery_equality() -> None:
    discovered = [ExecutableDependency("bash")]
    bound = bind_dependencies(discovered, [_registration("bash")])
    assert tuple(item.dependency for item in bound) == tuple(discovered)

    with pytest.raises(DependencyContractError, match="contract mismatch.*missing"):
        bind_dependencies(
            [*discovered, ExecutableDependency("missing")], [_registration("bash")]
        )
    with pytest.raises(DependencyContractError, match="contract mismatch.*stale"):
        bind_dependencies(discovered, [_registration("bash"), _registration("stale")])


def test_registry_rejects_kind_and_duplicate_errors() -> None:
    discovered = [ExecutableDependency("bash")]
    with pytest.raises(DependencyContractError, match="contract mismatch"):
        bind_dependencies(
            discovered,
            [_registration("bash", kind=ExternalDependencyKind.NETWORK_SERVICE)],
        )
    with pytest.raises(DependencyContractError, match="duplicate dependency"):
        bind_dependencies(discovered, [_registration("bash"), _registration("bash")])


def test_registry_validation_happens_before_any_checker_runs() -> None:
    calls: list[str] = []

    def checker(dependency):
        calls.append(dependency.dependency_id)
        return True

    with pytest.raises(DependencyContractError):
        bind_dependencies(
            [ExecutableDependency("bash"), ExecutableDependency("extra")],
            [_registration("bash", check=checker)],
        )
    assert calls == []


def test_binding_preserves_first_resource_and_checker_without_resolving() -> None:
    def unexpected_call(*args):
        raise AssertionError("binding must not resolve dependencies or run checks")

    dependency = LazyExternalDependency(
        dependency_id_value="executable:bash",
        dependency_kind=ExternalDependencyKind.EXECUTABLE,
        metadata={},
        resolver=unexpected_call,
    )
    registration = _registration("bash", check=unexpected_call)
    bound = bind_dependencies(
        [dependency, ExecutableDependency("bash")], [registration]
    )
    assert len(bound) == 1
    assert bound[0].dependency is dependency
    assert bound[0].check is registration.check
