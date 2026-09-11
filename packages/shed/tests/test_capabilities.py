from dataclasses import replace

import pytest
from roboshed.capabilities import Compactification
from roboshed.tools.compactification import DEFAULT_THRESHOLD_PERCENT

from roboz import All
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
        agent_endpoint=endpoint,
        capabilities=(capability,),
    ).build()
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
        agent_endpoint=default,
        capabilities=(Compactification(endpoint=override),),
    ).build()
    (tool,) = agent.default_tools
    assert tool(input=All(), messages=[]).to_compaction == "1.6k"


def test_compaction_without_any_endpoint_fails_before_starting_work():
    with pytest.raises(ValueError, match="compaction requires an endpoint"):
        DeployableAgent(
            system_prompt="Compact the conversation.",
            name="test",
            agent_endpoint=None,
            is_agentic=False,
            capabilities=(Compactification(),),
        ).build()


def test_compaction_preserves_live_lazy_endpoint_selection():
    from roboz.llm import LLMEndpoint
    from roboz.dependencies import (
        ExternalDependencyReference,
        LazyExternalDependency,
    )

    constructed = []

    def lazy(name, budget):
        endpoint = LLMEndpoint(
            client=object(), api_name="test", model_name=name, max_context_tokens=budget
        )

        def construct():
            constructed.append(name)
            return endpoint

        return LazyExternalDependency(
            endpoint.dependency_id, endpoint.kind, {}, construct
        )

    selected = lazy("first", 2000)

    class Reference(ExternalDependencyReference[LLMEndpoint]):
        def external_dependencies(self):
            return (selected,)

        def materialize(self):
            return selected.materialize()

    default = LLMEndpoint(client=object(), api_name="test", model_name="default")
    agent = DeployableAgent(
        name="test",
        system_prompt="Compact the conversation.",
        agent_endpoint=default,
        capabilities=(Compactification(endpoint=Reference()),),
    ).build()
    (tool,) = agent.default_tools
    assert agent.external_dependencies() == (default, selected)
    assert constructed == []
    assert tool(input=All(), messages=[]).to_compaction == "1.6k"
    selected = lazy("second", 3000)
    assert agent.external_dependencies() == (default, selected)
    assert constructed == ["first"]
    assert tool(input=All(), messages=[]).to_compaction == "2.4k"
    assert constructed == ["first", "second"]
