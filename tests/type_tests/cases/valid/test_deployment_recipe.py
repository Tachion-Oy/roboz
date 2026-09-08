from collections.abc import Callable
from typing import assert_type

from roboshed.deployments.robosprawl import (
    AgenticFactory,
    DeploymentFactory,
    RoboSprawlBundle,
    RunFactory,
)
from roboshed.workspace import Project
from roboz import DependencyRoute, LazyExternalDependency
from roboz.deployment import AgentDefinition
from roboz.llm import EndpointLike, LLMEndpoint


def recipe(project: Project, *, orchestrator_endpoint: EndpointLike) -> AgenticFactory:
    return AgenticFactory(
        project=project,
        orchestrator=AgentDefinition(name="root", agent_endpoint=orchestrator_endpoint),
    )


def inspect(
    project: Project, getter: Callable[[], LazyExternalDependency[LLMEndpoint]]
) -> None:
    route = DependencyRoute(getter)
    assert_type(route.materialize(), LLMEndpoint)
    factory: RunFactory = DeploymentFactory(recipe)
    assert_type(
        factory(project, endpoint_getter=getter, event_sinks=()), RoboSprawlBundle
    )
