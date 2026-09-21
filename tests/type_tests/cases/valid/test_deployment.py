from pathlib import Path
from typing import assert_type

from roboz import Agent
from roboz.shed.sandbox import Sandbox
from roboz.deployment import AgentCapability, DeployableAgent


def inspect(
    child: DeployableAgent,
    background: DeployableAgent,
    capability: AgentCapability,
) -> None:
    parent = DeployableAgent(
        name="parent",
        subagents=(child,),
        background_agents=(background,),
    )
    assert_type(parent.subagents, tuple[DeployableAgent, ...])
    assert_type(parent.background_agents, tuple[DeployableAgent, ...])
    assert_type(parent.add_capabilities(capability), None)
    result = parent.build(event_sinks=(lambda event: None,))
    assert_type(result, tuple[Agent, tuple[Agent, ...]])
    agent, background_agents = result
    assert_type(agent, Agent)
    assert_type(background_agents, tuple[Agent, ...])


def construct_sandbox(root: Path) -> None:
    assert_type(Sandbox(root=root, shared="workspace"), Sandbox)
