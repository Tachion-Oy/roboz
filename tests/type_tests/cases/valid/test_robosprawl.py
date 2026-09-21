from collections.abc import Callable
from typing import assert_type

from roboz.shed.deployments.robosprawl import robosprawl
from roboz.shed.sandbox import Sandbox
from roboz import Agent
from roboz.llm import EndpointLike, LLMEndpoint
from roboz.runtime import EventSink


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
        event_sinks=(sink,),
    )
    assert_type(agents, tuple[Agent, tuple[Agent, ...]])
