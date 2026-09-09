"""Consumer contracts, copied into and collected within an isolated installation."""

import importlib
from importlib.util import find_spec
import os
from pathlib import Path
import pkgutil
import sys

import pytest

CASE = os.environ["ROBOZ_INSTALL_CASE"]
PACKAGES = {
    "core": ("roboz",),
    "shed": ("roboz", "roboshed"),
    "endpoints": ("roboz", "roboz_endpoints"),
    "endpoints-openai": ("roboz", "roboz_endpoints"),
    "proton": ("roboz", "roboshed", "roboz_proton_bridge"),
    "extras": ("roboz", "roboshed", "roboz_endpoints", "roboz_proton_bridge"),
}[CASE]


def test_imports_are_installed_and_optional_dependencies_stay_optional():
    for name in PACKAGES:
        module = importlib.import_module(name)
        assert (
            Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        )
        for entry in pkgutil.walk_packages(module.__path__, name + "."):
            importlib.import_module(entry.name)
    assert find_spec("roboz_openai") is None
    optional = {
        "roboshed": CASE in {"shed", "proton", "extras"},
        "roboz_endpoints": CASE in {"endpoints", "endpoints-openai", "extras"},
        "roboz_proton_bridge": CASE in {"proton", "extras"},
        "openai": CASE in {"endpoints-openai", "extras"},
        "pydantic_settings": CASE in {"proton", "extras"},
        "fastapi": False,
    }
    for name, present in optional.items():
        assert (find_spec(name) is not None) == present, name
    if "roboz_endpoints" in PACKAGES:
        assert "openai" not in sys.modules


@pytest.mark.skipif(CASE != "core", reason="Core-only public contract")
def test_core_dependency_and_agent_contracts():
    import roboz as rz
    from roboz.dependencies import (
        DependencyRegistration,
        DependencyRoute,
        ExecutableDependency,
        ExternalDependencyKind,
        LazyExternalDependency,
        bind_dependencies,
    )
    from roboz.llm import LLMEndpoint, ModelSelector, MockLLMEndpoint

    resource = ExecutableDependency("python")
    route = DependencyRoute(lambda: resource)
    assert route.materialize() is resource
    assert route.external_dependencies() == (resource,)
    registration = DependencyRegistration(
        resource.dependency_id, resource.kind, lambda dependency: True
    )
    (bound,) = bind_dependencies([resource], [registration])
    assert bound.dependency is resource and bound.check is registration.check

    def unexpected_resolution():
        raise AssertionError("Selection must not construct clients")

    model = LazyExternalDependency[LLMEndpoint](
        "model:test:one",
        ExternalDependencyKind.MODEL_ENDPOINT,
        {},
        unexpected_resolution,
    )
    assert ModelSelector({"One": model}, default=model).selected_endpoint is model
    agent = rz.Agent(
        name="test",
        tools=[rz.stop],
        system_prompt="Stop.",
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "test", "value": "ok"}]
        ),
    )
    assert agent.invoke()[0].value == "ok"
    for name in (
        "roboz.tooling.dependencies",
        "roboz.agents",
        "roboz.workspace",
        "roboz.tools.snapshot_conversations",
        "roboz.tools.compactification",
    ):
        assert find_spec(name) is None


@pytest.mark.skipif(
    "roboz_endpoints" not in PACKAGES, reason="Endpoint package contract"
)
def test_endpoint_inventory_is_lazy_and_missing_extra_is_explained():
    from roboz import LazyExternalDependency
    from roboz_endpoints.inventory import CATALOGS

    endpoints = [
        getattr(provider, name)
        for provider in CATALOGS.values()
        for name in provider.models_by_attribute
    ]
    assert endpoints
    for endpoint in endpoints:
        assert isinstance(endpoint, LazyExternalDependency)
        assert "materialized" not in endpoint.__dict__
        assert endpoint.external_dependencies() == (endpoint,)
        metadata = endpoint.redacted_metadata()
        assert (
            endpoint.dependency_id
            == f"model:{metadata['api_name']}:{metadata['model_name']}"
        )
        if CASE == "endpoints":
            with pytest.raises(ModuleNotFoundError, match="roboz-endpoints") as error:
                endpoint.materialize()
            assert error.value.name == "openai"


@pytest.mark.skipif(
    "roboz_proton_bridge" not in PACKAGES, reason="Proton package contract"
)
def test_proton_service_constructs_without_connecting():
    from roboz_proton_bridge import ProtonBridgeEmailService

    assert ProtonBridgeEmailService().dependency_id
