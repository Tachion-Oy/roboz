from dataclasses import replace

import pytest
from roboshed.capabilities import Compactification
from roboshed.tools.compactification import DEFAULT_THRESHOLD_PERCENT

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
        is_agentic=False,
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
