from collections.abc import Callable
from typing import assert_type

from roboshed.dependency_health import inspect_dependencies
from roboshed.deployments.robosprawl import robosprawl
from roboshed.sandbox import Sandbox
from roboz import DependencyRoute, LazyExternalDependency
from roboz.dependencies import BoundDependency
from roboz.deployment import Deployment
from roboz.llm import LLMEndpoint


def inspect(
    sandbox: Sandbox, getter: Callable[[], LazyExternalDependency[LLMEndpoint]]
) -> None:
    route = DependencyRoute(getter)
    assert_type(route.materialize(), LLMEndpoint)

    def configure(sandbox: Sandbox) -> Deployment:
        return robosprawl(
            sandbox,
            "project",
            orchestrator_endpoint=route,
            memory_endpoint=route,
            capabilities=(),
        )

    assert_type(configure(sandbox), Deployment)
    assert_type(
        inspect_dependencies(configure, sandbox=sandbox, registrations=None),
        tuple[BoundDependency, ...],
    )
