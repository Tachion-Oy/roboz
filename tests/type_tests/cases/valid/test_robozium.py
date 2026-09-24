from collections.abc import Callable
from pathlib import Path
from typing import assert_type

from roboz.shed.capabilities import Email, SafeScripts
from roboz.shed.deployments.robozium import robozium
from roboz.shed.sandbox import Sandbox
from roboz.shed.tools.email.proton_bridge import ProtonBridgeEmailService
from roboz import Agent
from roboz.llm import EndpointLike, LLMEndpoint
from roboz.runtime import EventSink


def configure(
    sandbox: Sandbox,
    getter: Callable[[], LLMEndpoint],
    memory: EndpointLike,
    sink: EventSink,
    email: ProtonBridgeEmailService,
    scripts_dir: Path,
) -> None:
    agents = robozium(
        sandbox,
        endpoint_getter=getter,
        memory_endpoint=memory,
        additional_capabilities=(Email(email), SafeScripts(scripts_dir)),
        specialists=(),
        event_sinks=(sink,),
    )
    assert_type(agents, tuple[Agent, tuple[Agent, ...]])
