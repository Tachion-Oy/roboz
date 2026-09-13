from collections.abc import Callable
from typing import assert_type

from roboshed.deployments.robosprawl import robosprawl
from roboshed.sandbox import Sandbox
from roboz import Agent
from roboz.llm import EndpointLike, LLMEndpoint
from roboz.runtime import EventSink, Output


def configure(
    sandbox: Sandbox,
    getter: Callable[[], LLMEndpoint],
    memory: EndpointLike,
    sink: EventSink,
) -> None:
    agents = robosprawl(
        sandbox,
        endpoint_getter=getter,
        memory_endpoint=memory,
        additional_capabilities=(),
        specialists=(),
        interaction_mode=Output.API,
        event_sinks=(sink,),
    )
    assert_type(agents, tuple[Agent, tuple[Agent, ...]])
