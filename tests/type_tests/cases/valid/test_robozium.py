from collections.abc import Callable
from pathlib import Path
from typing import assert_type

from roboz.shed.deployments.robozium import robozium
from roboz.shed.sandbox import Sandbox
from roboz.shed.tools.email.proton_bridge import ProtonBridgeEmailService
from roboz import Agent
from roboz.deployment import DeployableAgent
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
    definition = robozium(
        sandbox,
        endpoint_getter=getter,
        memory_endpoint=memory,
        email_service=email,
        scripts_dir=scripts_dir,
        specialists=(),
    )
    assert_type(definition, DeployableAgent)
    agents = definition.build(event_sinks=(sink,))
    assert_type(agents, tuple[Agent, tuple[Agent, ...]])
