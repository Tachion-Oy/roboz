from collections.abc import Callable
from typing import assert_type

from roboshed.agents import orchestrator as orchestrator_definition
from roboshed.dependency_health import inspect_dependencies
from roboshed.sandbox import Sandbox
from roboz import Agent, DependencyRoute, LazyExternalDependency
from roboz.dependencies import BoundDependency
from roboz.llm import LLMEndpoint


def inspect(
    sandbox: Sandbox, getter: Callable[[], LazyExternalDependency[LLMEndpoint]]
) -> None:
    route = DependencyRoute(getter)
    assert_type(route.materialize(), LLMEndpoint)

    def configure(sandbox: Sandbox) -> tuple[Agent, tuple[Agent, ...]]:
        sandbox.configure_scope("project")
        definition = orchestrator_definition(sandbox, agent_endpoint=route)
        return definition.build(
            event_sinks=(lambda event: None,),
        )

    assert_type(configure(sandbox), tuple[Agent, tuple[Agent, ...]])
    assert_type(
        inspect_dependencies(configure, sandbox=sandbox, registrations=None),
        tuple[BoundDependency, ...],
    )
