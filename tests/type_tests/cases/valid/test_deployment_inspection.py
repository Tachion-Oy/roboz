from collections.abc import Callable
from typing import assert_type

from roboshed.agents import orchestrator as orchestrator_definition
from roboshed.dependency_health import inspect_dependencies
from roboshed.sandbox import Sandbox
from roboz import DependencyRoute, LazyExternalDependency
from roboz.dependencies import BoundDependency
from roboshed.deployments import Deployment
from roboz.llm import LLMEndpoint


def inspect(
    sandbox: Sandbox, getter: Callable[[], LazyExternalDependency[LLMEndpoint]]
) -> None:
    route = DependencyRoute(getter)
    assert_type(route.materialize(), LLMEndpoint)

    def configure(sandbox: Sandbox) -> Deployment:
        deployment = Deployment(
            sandbox=sandbox,
            agent=orchestrator_definition(sandbox, agent_endpoint=route),
        )
        deployment.sandbox.configure_scope("project")
        deployment.event_sinks.append(lambda event: None)
        return deployment

    assert_type(configure(sandbox), Deployment)
    assert_type(
        inspect_dependencies(configure, sandbox=sandbox, registrations=None),
        tuple[BoundDependency, ...],
    )
