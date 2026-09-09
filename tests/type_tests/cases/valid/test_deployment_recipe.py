from collections.abc import Callable
from typing import assert_type

from roboshed.deployments.robosprawl import (
    AgenticFactory,
    DeploymentFactory,
    RoboSprawlBundle,
    RunFactory,
)
from roboshed.sandbox import Sandbox
from roboz import DependencyRoute, LazyExternalDependency
from roboz.deployment import AgentDefinition
from roboz.llm import EndpointLike, LLMEndpoint


def recipe(
    sandbox: Sandbox, project_slug: str, *, orchestrator_endpoint: EndpointLike
) -> AgenticFactory:
    return AgenticFactory(
        sandbox=sandbox,
        project_slug=project_slug,
        orchestrator=AgentDefinition(name="root", agent_endpoint=orchestrator_endpoint),
    )


def inspect(
    sandbox: Sandbox, getter: Callable[[], LazyExternalDependency[LLMEndpoint]]
) -> None:
    route = DependencyRoute(getter)
    assert_type(route.materialize(), LLMEndpoint)
    factory: RunFactory = DeploymentFactory(recipe)
    assert_type(
        factory(sandbox, "project", endpoint_getter=getter, event_sinks=()),
        RoboSprawlBundle,
    )
