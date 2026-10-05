"""Tool adapter for synchronously invoking a nested agent."""

from logging import getLogger

from roboz.agent._notifications import NESTED_AGENT_NO_OUTCOME_PLACEHOLDER
from roboz.models import Empty, Message, Str
from roboz.agent.core import Agent
from roboz.tooling.decorators import factory

logger = getLogger(__name__)


@factory
def run_nested_agent(input: Empty, messages: list[Message], ctx: Agent) -> Str:
    """Run the configured nested agent, wait for completion, and return its result."""
    logger.debug("Delegating to nested agent '%s'", ctx.name)
    stop_out, _ = ctx.invoke(input=input)
    logger.debug("Nested agent '%s' returned", ctx.name)
    text = (
        stop_out.value
        if stop_out.value is not None
        else NESTED_AGENT_NO_OUTCOME_PLACEHOLDER
    )
    return Str(value=text)
