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
        "imapclient": False,
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
    from roboz.agent import PromptLLMContext, prompt_llm, run_nested_agent
    from roboz.agent.nested_agent import run_nested_agent as nested_factory
    from roboz.agent.prompt_llm_tool import PromptLLMContext as PromptContext
    from roboz.agent.prompt_llm_tool import prompt_llm as prompt_factory
    from roboz.dependencies import ExecutableDependency, dedupe_external_dependencies
    from roboz.llm import LLMEndpoint, LLMEndpointRoute, ModelSelector, MockLLMEndpoint
    from roboz.tools import stop

    assert PromptLLMContext is PromptContext
    assert prompt_llm is prompt_factory
    assert run_nested_agent is nested_factory
    assert prompt_llm.name == "prompt_llm"
    assert run_nested_agent.name == "run_nested_agent"
    for name in ("PromptAgentContext", "prompt_agent", "run_subagent"):
        assert not hasattr(rz.agent, name)

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
        "roboz.agent.prompt_agent_tool",
        "roboz.agent.subagent",
        "roboz.tooling.dependencies",
        "roboz.agents",
        "roboz.workspace",
        "roboz.tools.snapshot_conversations",
        "roboz.tools.compactification",
    ):
        assert find_spec(name) is None


def test_public_credential_constants():
    from roboz.endpoints import (
        DEFAULT_ENCRYPTED_ENV_PATH,
        ENCRYPTED_NAMESPACE,
        SECRET_SUFFIX,
    )
    from roboz.endpoints import env

    assert SECRET_SUFFIX == env.SECRET_SUFFIX == "_SECRET"
    assert ENCRYPTED_NAMESPACE == env.ENCRYPTED_NAMESPACE == "roboz:"
    assert DEFAULT_ENCRYPTED_ENV_PATH == env.DEFAULT_ENCRYPTED_ENV_PATH == Path(".env.encrypt")


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


def test_canonical_file_cli_imports_and_removed_names():
    from roboz.shed.tools import get_run_file_command
    from roboz.shed.tools.cli_commands import FileCommand
    from roboz.shed.tools.cli_commands import get_run_file_command as cli_factory
    from roboz.shed import models

    assert cli_factory is get_run_file_command
    assert FileCommand(value=[("pwd", "CMD")]).value == [("pwd", "CMD")]
    for name in (
        "roboz.shed.tools.cli_commands_v2",
        "roboz.shed.tools.cli_commands.tagged_transfer",
        "roboz.shed.tools.cli_commands.run_file_command",
        "roboz.shed.tools.cli_commands.utilities",
    ):
        assert find_spec(name) is None
    assert not hasattr(models, "RunFileCommand")
    assert not hasattr(models, "RunFileCommands")
