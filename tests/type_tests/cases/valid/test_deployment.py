from pathlib import Path
from typing import assert_type

from roboz import Agent
from roboshed.deployments import Deployment
from roboshed.sandbox import Sandbox
from roboz.deployment import AgentCapability, DeployableAgent


def inspect(
    deployment: Deployment,
    child: DeployableAgent,
    background: DeployableAgent,
    capability: AgentCapability,
) -> None:
    parent = DeployableAgent(
        name="parent",
        agent_endpoint=None,
        subagents=(child,),
        background_agents=(background,),
    )
    assert_type(parent.subagents, tuple[DeployableAgent, ...])
    assert_type(parent.background_agents, tuple[DeployableAgent, ...])
    assert_type(deployment.sandbox.configure_scope("job"), None)
    deployment.additional_capabilities = (capability,)
    deployment.event_sinks.append(lambda event: None)
    result = deployment.build()
    assert_type(result, tuple[Agent, tuple[Agent, ...]])
    agent, background_agents = result
    assert_type(agent, Agent)
    assert_type(background_agents, tuple[Agent, ...])


def construct_sandbox(root: Path) -> None:
    assert_type(Sandbox(root=root, shared="workspace"), Sandbox)
