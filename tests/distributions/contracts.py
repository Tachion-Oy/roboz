"""Consumer contracts, copied into and collected within an isolated installation."""

import importlib
from importlib.util import find_spec
import os
from pathlib import Path
import pkgutil
import sys

CASE = os.environ["ROBOZ_INSTALL_CASE"]
PACKAGES = {
    "roboz": ("roboz",),
}[CASE]


def test_all_modules_and_required_dependencies_are_installed():
    for name in PACKAGES:
        module = importlib.import_module(name)
        assert (
            Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        )
        for entry in pkgutil.walk_packages(module.__path__, name + "."):
            importlib.import_module(entry.name)
    assert find_spec("roboz_openai") is None
    dependencies = {
        "openai": True,
        "pydantic_settings": False,
        "fastapi": False,
    }
    for name, present in dependencies.items():
        assert (find_spec(name) is not None) == present, name
    assert "openai" not in sys.modules
    for removed in ("roboshed", "roboz_endpoints", "roboz_proton_bridge"):
        assert find_spec(removed) is None


def test_core_dependency_and_agent_contracts():
    import roboz as rz
    from types import SimpleNamespace
    from roboz.dependencies import ExecutableDependency, dedupe_external_dependencies
    from roboz.llm import LLMEndpoint, LLMEndpointRoute, ModelSelector, MockLLMEndpoint
    from roboz.tools import stop

    resource = ExecutableDependency("python")
    assert resource.external_dependencies() == (resource,)
    assert dedupe_external_dependencies((resource, resource)) == (resource,)

    def unexpected_resolution():
        raise AssertionError("Selection must not construct clients")

    model = LLMEndpoint(
        client=SimpleNamespace(
            chat=object(),
            models=object(),
            close=unexpected_resolution,
            materialize=unexpected_resolution,
        ),
        api_name="test",
        model_name="one",
    )
    selector = ModelSelector({"One": model}, default=model)
    route = LLMEndpointRoute(lambda: selector.selected_endpoint)
    assert route.resolve() is model
    assert route.external_dependencies()[0] is model
    agent = rz.Agent(
        name="test",
        tools=[stop],
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


def test_endpoint_inventory_is_lazy():
    from roboz.llm import LLMEndpoint, TranscriptionEndpoint
    from roboz.endpoints.inventory import CATALOGS

    endpoints = [
        getattr(provider, name)
        for provider in CATALOGS.values()
        for name in provider.models_by_attribute
    ]
    assert endpoints
    for endpoint in endpoints:
        assert isinstance(endpoint, (LLMEndpoint, TranscriptionEndpoint))
        assert "materialized" not in endpoint.__dict__
        assert endpoint.external_dependencies() == (endpoint,)
        metadata = endpoint.redacted_metadata()
        assert (
            endpoint.dependency_id
            == f"model:{metadata['api_name']}:{metadata['model_name']}"
        )
