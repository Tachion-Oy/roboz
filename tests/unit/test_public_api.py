import importlib
import inspect
from pathlib import Path

import pytest

import roboz as rz
import roboz.agent
import roboz.llm
import roboz.models
import roboz.runtime
import roboz.runtime.persistence
import roboz.tooling
import roboz.tools
from roboz.agent.core import Agent as CoreAgent
from roboz.agent.prompt_agent_tool import prompt_agent as agent_prompt_agent
from roboz.tooling.decorators import factory as tooling_factory
from roboz.tooling.decorators import tool as tooling_tool
from roboz.tools import prompt_user as tools_prompt_user
from roboz.tools import stop as tools_stop


def test_golden_path_identities() -> None:
    assert rz.Agent is CoreAgent
    assert rz.tool is tooling_tool
    assert rz.factory is tooling_factory
    assert rz.stop is tools_stop
    assert rz.prompt_user is tools_prompt_user
    assert rz.prompt_agent is agent_prompt_agent


def test_domain_imports_do_not_replace_top_level_tool() -> None:
    original = rz.tool
    importlib.import_module("roboz.tools")
    importlib.import_module("roboz.tooling")
    assert rz.tool is original is tooling_tool


def test_domain_ownership_exports() -> None:
    assert roboz.agent.prompt_agent is agent_prompt_agent
    assert roboz.tools.stop is tools_stop
    assert roboz.tools.prompt_user is tools_prompt_user
    assert roboz.llm.call_llm_api is not None
    assert roboz.llm.get_completion is not None
    assert roboz.llm.get_basic_system_prompt is not None
    assert roboz.llm.estimate_conversation_tokens is not None
    assert roboz.llm.get_truncated_messages_for_context is not None
    assert roboz.llm.resolve_endpoint is not None
    assert roboz.agent.get_active_agent_stack is not None
    assert roboz.runtime.load_key is not None
    assert roboz.runtime.persistence.logged_row_to_message is not None
    assert roboz.tools.NO_REPLY == "The user did not respond"
    assert not hasattr(roboz.agent, "stop")
    assert not hasattr(roboz.tooling, "tool")
    assert not hasattr(roboz.tooling, "factory")


def test_all_exported_ctx_contracts_inherit_factory_ctx() -> None:
    for module in (rz, roboz.agent, roboz.tools, roboz.tooling):
        for name in module.__all__:
            value = getattr(module, name)
            if name.endswith("Ctx"):
                assert inspect.isclass(value)
                assert issubclass(value, rz.FactoryCtx)
            assert not name.endswith("Context")


def test_approved_interaction_rename() -> None:
    assert hasattr(roboz, "prompt_user_at_start")
    assert hasattr(roboz.tools, "prompt_user_at_start")
    assert not hasattr(roboz, "ask_user_at_start")
    assert not hasattr(roboz.tools, "ask_user_at_start")


def test_private_helpers_are_absent_from_public_facades() -> None:
    assert not hasattr(roboz.models, "LLMTelemetry")
    assert not hasattr(roboz.models, "schema_scrubber")
    assert not hasattr(roboz.llm, "classify_llm_provider_error")


def test_downstream_model_contracts() -> None:
    assert roboz.models.BOOTSTRAP_MESSAGE_KINDS == frozenset(
        {
            roboz.models.MessageKind.SYSTEM_MESSAGE,
            roboz.models.MessageKind.STARTUP_CONTEXT,
            roboz.models.MessageKind.AUTO_LOAD_BANNER,
            roboz.models.MessageKind.AUTO_LOADED_SKILL,
        }
    )
    assert roboz.models.BaseNames.VALUE_FIELD == "value"
    assert roboz.models.LIGHT_MAX_CHARS == 40_000


@pytest.mark.parametrize(
    "module_name",
    [
        "roboz.common",
        "roboz.create",
        "roboz.tool",
        "roboz.llm.context",
        "roboz.llm.models",
        "roboz.llm.utils",
        "roboz.llm.tokens",
        "roboz.llm._constants",
        "roboz.models._constants",
        "roboz.agent.prompt",
        "roboz.agent._messages",
    ],
)
def test_obsolete_modules_cannot_be_imported(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_llm_source_does_not_import_agent() -> None:
    source_root = Path(inspect.getfile(roboz.llm)).parent
    for source_path in source_root.glob("*.py"):
        assert "roboz.agent" not in source_path.read_text()
