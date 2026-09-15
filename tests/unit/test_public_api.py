import ast
import importlib
import inspect
from pathlib import Path
import subprocess
import sys
import textwrap

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
    assert rz.Tool is roboz.tooling.Tool
    assert rz.Factory is roboz.tooling.Factory
    assert rz.Skill is roboz.skill.Skill


def test_root_exports_only_authoring_primitives_and_domains() -> None:
    assert set(rz.__all__) == {
        "Agent",
        "Factory",
        "Skill",
        "Tool",
        "agent",
        "dependencies",
        "deployment",
        "exceptions",
        "factory",
        "llm",
        "models",
        "runtime",
        "skill",
        "tool",
        "tooling",
        "tools",
    }
    assert set(rz.__all__) <= set(dir(rz))
    for name in (
        "Empty",
        "HasExternalDependencies",
        "Materializable",
        "Message",
        "PromptUser",
        "Str",
        "prompt_agent",
        "prompt_user",
        "run_subagent",
        "stop",
    ):
        assert not hasattr(rz, name)
    with pytest.raises(AttributeError, match="unknown"):
        getattr(rz, "unknown")


def test_root_stub_declares_every_runtime_export() -> None:
    stub = Path(inspect.getfile(rz)).with_suffix(".pyi")
    tree = ast.parse(stub.read_text(encoding="utf-8"), stub)
    declared = {
        alias.asname
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname is not None
    }
    assert declared == set(rz.__all__)


def test_root_domains_resolve_and_cache_canonical_modules() -> None:
    for name in (
        "agent",
        "dependencies",
        "deployment",
        "exceptions",
        "llm",
        "models",
        "runtime",
        "skill",
        "tooling",
        "tools",
    ):
        domain = getattr(rz, name)
        assert domain is importlib.import_module(f"roboz.{name}")
        assert getattr(rz, name) is domain


def test_plain_import_and_discovery_do_not_load_domains() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """\
                import sys
                import roboz

                assert not any(name.startswith("roboz.") for name in sys.modules)
                assert {"Agent", "llm", "models", "tool", "tools"} <= set(dir(roboz))
                assert not any(name.startswith("roboz.") for name in sys.modules)
                """
            ),
        ],
        check=True,
    )


def test_basic_tooling_does_not_load_agent_llm_or_runtime() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """\
                import sys
                import roboz

                assert roboz.tool is roboz.tooling.tool
                assert roboz.models.Message
                for name in ("roboz.agent", "roboz.llm", "roboz.runtime"):
                    assert name not in sys.modules
                """
            ),
        ],
        check=True,
    )


def test_domain_imports_do_not_replace_top_level_tool() -> None:
    original = rz.tool
    importlib.import_module("roboz.tools")
    importlib.import_module("roboz.tooling")
    assert rz.tool is original is tooling_tool


def test_domain_ownership_exports() -> None:
    from roboz.models.messages import filter_messages

    assert roboz.models.filter_messages is filter_messages
    assert roboz.agent.prompt_agent is agent_prompt_agent
    assert roboz.tools.stop is tools_stop
    assert roboz.tools.prompt_user is tools_prompt_user
    assert roboz.llm.call_llm_api is not None
    assert roboz.llm.get_completion is not None
    assert roboz.llm.get_basic_system_prompt is not None
    assert roboz.llm.estimate_conversation_tokens is not None
    assert roboz.llm.get_truncated_messages_for_context is not None
    assert roboz.llm.resolve_endpoint is not None
    assert roboz.llm.with_request_options is not None
    assert roboz.llm.JSONValue is not None
    assert roboz.llm.RequestOptions is not None
    assert roboz.agent.get_active_agent_stack is not None
    assert roboz.runtime.load_key is not None
    assert roboz.runtime.LOG_DATA_ATTRIBUTE == "roboz_data"
    assert roboz.runtime.LOG_FORMAT is not None
    assert roboz.runtime.LOG_DATE_FORMAT is not None
    assert roboz.runtime.LogScalar is not None
    assert roboz.runtime.log_with_data is not None
    assert roboz.runtime.reset_logging_config is not None
    assert roboz.runtime.persistence.logged_row_to_message is not None
    assert roboz.tools.NO_REPLY == "The user did not respond"
    assert not hasattr(roboz.agent, "stop")
    assert roboz.tooling.tool is tooling_tool
    assert roboz.tooling.factory is tooling_factory


def test_optional_context_capabilities_are_public() -> None:
    for module in (rz, roboz.agent, roboz.tools, roboz.tooling):
        assert not hasattr(module, "Ctx")
    assert not hasattr(rz, "HasExternalDependencies")
    assert not hasattr(rz, "Materializable")
    assert not hasattr(rz, "FactoryCtx")
    assert not hasattr(rz, "ToolDependency")


def test_approved_interaction_rename() -> None:
    assert hasattr(roboz.tools, "prompt_user_at_start")
    assert not hasattr(roboz, "prompt_user_at_start")
    assert not hasattr(roboz, "ask_user_at_start")
    assert not hasattr(roboz.tools, "ask_user_at_start")


def test_private_helpers_are_absent_from_public_facades() -> None:
    assert not hasattr(roboz.models, "LLMTelemetry")
    assert not hasattr(roboz.models, "schema_scrubber")
    assert not hasattr(roboz.llm, "classify_llm_provider_error")
    assert not hasattr(roboz.llm, "copy_request_options")


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
