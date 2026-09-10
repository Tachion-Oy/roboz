from typing import assert_type

from roboz import Agent
from roboz.deployment import DeployableAgent, Deployment


def inspect(
    deployment: Deployment, child: DeployableAgent, background: DeployableAgent
) -> None:
    parent = DeployableAgent(
        name="parent",
        agent_endpoint=None,
        subagents=(child,),
        background_agents=(background,),
    )
    assert_type(parent.subagents, tuple[DeployableAgent, ...])
    assert_type(parent.background_agents, tuple[DeployableAgent, ...])
    result = deployment.build()
    assert_type(result, tuple[Agent, tuple[Agent, ...]])
    agent, background_agents = result
    assert_type(agent, Agent)
    assert_type(background_agents, tuple[Agent, ...])
