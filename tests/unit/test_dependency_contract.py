"""Resources own checks; generic lazy loading and checker registries are removed."""

import pytest

from roboz import dependencies
from roboz.dependencies import (
    ExternalDependency,
    ExternalDependencyKind,
    dedupe_external_dependencies,
)


@pytest.mark.parametrize(
    "name",
    [
        "DependencyContractError",
        "DependencyRegistration",
        "bind_dependencies",
        "LazyExternalDependency",
        "DependencyRoute",
        "ExternalDependencySource",
        "ExternalDependencyReference",
    ],
)
def test_removed_dependency_apis_have_no_compatibility_alias(name):
    assert not hasattr(dependencies, name)


def test_resource_owns_its_check_and_discovery_never_runs_it():
    calls = []

    class Resource(ExternalDependency):
        @property
        def dependency_id(self):
            return "service:test"

        @property
        def kind(self):
            return ExternalDependencyKind.NETWORK_SERVICE

        def redacted_metadata(self):
            return {"service": "test"}

        def check(self):
            calls.append(self)
            return True

    resource = Resource()
    assert dedupe_external_dependencies((resource, resource)) == (resource,)
    assert resource.external_dependencies()[0] is resource
    assert calls == []
    assert resource.check() is True
    assert calls == [resource]


def test_incomplete_resource_cannot_instantiate():
    class Incomplete(ExternalDependency):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()
