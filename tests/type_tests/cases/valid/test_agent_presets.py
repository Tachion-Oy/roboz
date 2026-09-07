from typing import assert_type

from roboshed.agents import orchestrator
from roboshed.assistant import build_assistant
from roboshed.workspace import Project
from roboz import Agent
from roboz.deployment import AgentCapability, AgentDefinition
from roboz.llm import EndpointLike


def configure(
    project: Project, endpoint: EndpointLike, capability: AgentCapability
) -> None:
    assert_type(
        orchestrator(agent_endpoint=endpoint, capabilities=(capability,)),
        AgentDefinition,
    )
    assert_type(
        build_assistant(project=project, endpoint=endpoint, capabilities=(capability,)),
        Agent,
    )
