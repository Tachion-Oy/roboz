from collections.abc import Callable
from typing import assert_type

from roboshed.deployments.robosprawl import RoboSprawl
from roboshed.sandbox import Sandbox
from roboz import Agent, LazyExternalDependency
from roboz.llm import EndpointLike, LLMEndpoint
from roboz.runtime import EventSink, Output


def configure(
    sandbox: Sandbox,
    getter: Callable[[], LazyExternalDependency[LLMEndpoint]],
    memory: EndpointLike,
    sink: EventSink,
) -> None:
    recipe = RoboSprawl()
    assert_type(recipe.set_sandbox(sandbox), None)
    assert_type(recipe.set_endpoint_getter(getter), None)
    assert_type(recipe.set_memory_endpoint(memory), None)
    assert_type(recipe.set_additional_capabilities(()), None)
    assert_type(recipe.set_specialists(()), None)
    assert_type(recipe.set_interaction_mode(Output.API), None)
    assert_type(recipe.set_event_sinks((sink,)), None)
    assert_type(recipe.build(), tuple[Agent, tuple[Agent, ...]])
