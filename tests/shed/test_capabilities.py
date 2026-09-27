from dataclasses import replace

import pytest
from roboz.shed.capabilities import Compactification
from roboz.shed.tools.compactification import DEFAULT_THRESHOLD_PERCENT

from roboz.agent import AgentMode
from roboz.models import All
from roboz.deployment import DeployableAgent
from roboz.llm import MockLLMEndpoint


@pytest.mark.parametrize("threshold", [None, 60.0])
def test_compaction_capability_preserves_tool_default_and_explicit_policy(threshold):
    capability = Compactification()
    if threshold is not None:
        capability = replace(capability, threshold_percent=threshold)
    endpoint = MockLLMEndpoint([], max_context_tokens=1000)
    agent = DeployableAgent(
        system_prompt="Compact the conversation.",
        name="test",
        default_capabilities=(capability,),
    )
    agent.set_agent_endpoint(endpoint)
    agent, _ = agent.build()
    (tool,) = agent.default_tools
    result = tool(input=All(), messages=[])
    expected = DEFAULT_THRESHOLD_PERCENT if threshold is None else threshold
    assert result.to_compaction == str(int(1000 * expected / 100))
    assert result.compactions == 0


def test_compaction_override_uses_its_model_context_budget():
    default = MockLLMEndpoint([], max_context_tokens=1000)
    override = MockLLMEndpoint([], max_context_tokens=2000)
    agent = DeployableAgent(
        system_prompt="Compact the conversation.",
        name="test",
        default_capabilities=(Compactification(endpoint=override),),
    )
    agent.set_agent_endpoint(default)
    agent, _ = agent.build()
    (tool,) = agent.default_tools
    assert tool(input=All(), messages=[]).to_compaction == "1.6k"


def test_compaction_without_any_endpoint_fails_before_starting_work():
    definition = DeployableAgent(
        system_prompt="Compact the conversation.",
        name="test",
        mode=AgentMode.DETERMINISTIC,
        default_capabilities=(Compactification(),),
    )
    with pytest.raises(ValueError, match="agent_endpoint.*None"):
        definition.build()


def test_compaction_follows_selection_without_initializing_idle_models():
    from types import SimpleNamespace
    from roboz.llm import LLMEndpoint, LLMEndpointRoute

    def forbidden():
        pytest.fail("Idle compaction must not initialize a client")

    client = SimpleNamespace(
        chat=object(), models=object(), close=forbidden, materialize=forbidden
    )
    default = LLMEndpoint(client=client, api_name="test", model_name="default")
    first = LLMEndpoint(
        client=client, api_name="test", model_name="first", max_context_tokens=2000
    )
    second = LLMEndpoint(
        client=client, api_name="test", model_name="second", max_context_tokens=3000
    )
    selected = first
    definition = DeployableAgent(
        name="test",
        system_prompt="Compact the conversation.",
        default_capabilities=(
            Compactification(endpoint=LLMEndpointRoute(lambda: selected)),
        ),
    )
    definition.set_agent_endpoint(default)
    agent, _ = definition.build()
    (tool,) = agent.default_tools
    assert agent.external_dependencies() == (default, first)
    assert tool(input=All(), messages=[]).to_compaction == "1.6k"
    selected = second
    assert agent.external_dependencies() == (default, second)
    assert tool(input=All(), messages=[]).to_compaction == "2.4k"


@pytest.mark.parametrize("capability", ["commands", "editing"])
def test_guarded_capabilities_require_a_configured_sandbox(tmp_path, capability):
    from roboz.shed.capabilities import FileCommands, FileEditing
    from roboz.shed.sandbox import Sandbox

    selected = FileCommands() if capability == "commands" else FileEditing()
    definition = DeployableAgent(
        name="files", system_prompt="Work on files.", default_capabilities=(selected,)
    )
    definition.set_agent_endpoint(MockLLMEndpoint([]))
    with pytest.raises(ValueError, match="sandbox"):
        definition.build()
    definition.set_attributes(sandbox=Sandbox(tmp_path))
    with pytest.raises(ValueError, match="scope is not configured"):
        definition.build()


def test_rebuilding_file_editing_preserves_each_project(tmp_path):
    from roboz.shed.agents import orchestrator
    from roboz.shed.models import ApplyPatch, GuardStatus
    from roboz.shed.sandbox import Sandbox

    sandbox = Sandbox(tmp_path, scope="one")
    definition = orchestrator(sandbox, agent_endpoint=MockLLMEndpoint([]))
    first, _ = definition.build()
    sandbox.configure_scope("two")
    second, _ = definition.build()
    for agent, own, other in ((first, "one", "two"), (second, "two", "one")):
        entry = next(tool for tool in agent.tools if tool.name == "apply_patch")
        guard = next(tool for tool in agent.tools if entry in (tool.chained_to or ()))
        execute = next(tool for tool in agent.tools if guard in (tool.chained_to or ()))
        allowed = guard(
            entry(
                ApplyPatch(
                    path=f"projects/{own}/note.txt", old_string="", new_string=own
                ),
                [],
            ),
            [],
        )
        assert allowed.status == GuardStatus.ALLOWED
        execute(allowed, [])
        assert (sandbox.projects_dir / own / "note.txt").read_text() == own
        denied = guard(
            entry(
                ApplyPatch(
                    path=f"projects/{other}/note.txt", old_string="", new_string="wrong"
                ),
                [],
            ),
            [],
        )
        assert denied.status == GuardStatus.DENIED


@pytest.mark.parametrize("reply,allowed", [("yes", True), ("no", False)])
def test_file_editing_keeps_shared_write_confirmation(tmp_path, reply, allowed):
    from roboz.shed.capabilities import FileEditing
    from roboz.shed.models import ApplyPatch, GuardStatus
    from roboz.shed.sandbox import Sandbox
    from roboz.runtime import EventPipe, bind_api_user_io, reset_api_user_io

    class Replies:
        def __init__(self):
            self.prompts = []

        def request_input(self, message, timeout=None):
            self.prompts.append(message)
            return reply

        def notify(self, message):
            pytest.fail("Expected a confirmation request")

    sandbox = Sandbox(tmp_path, scope="project")
    definition = DeployableAgent(name="files", system_prompt="Edit files.")
    definition.set_attributes(sandbox=sandbox)
    ((entry, guard, execute),) = FileEditing().build(definition, EventPipe()).tools
    replies = Replies()
    token = bind_api_user_io(replies)
    try:
        result = guard(
            entry(
                ApplyPatch(path="shared/note.txt", old_string="", new_string="shared"),
                [],
            ),
            [],
        )
    finally:
        reset_api_user_io(token)
    assert (result.status == GuardStatus.ALLOWED) is allowed
    assert len(replies.prompts) == 1
    if allowed:
        execute(result, [])
        assert (sandbox.shared_dir / "note.txt").read_text() == "shared"
    else:
        assert not (sandbox.shared_dir / "note.txt").exists()
