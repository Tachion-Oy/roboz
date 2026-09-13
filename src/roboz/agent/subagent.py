"""Tool adapter for invoking one agent as another agent's sub-agent."""

from logging import getLogger

from roboz.agent._notifications import SUBAGENT_NO_OUTCOME_PLACEHOLDER
from roboz.models import Empty, Message, Str
from roboz.agent.core import Agent
from roboz.tooling.decorators import factory

logger = getLogger(__name__)


@factory
def run_subagent(input: Empty, messages: list[Message], ctx: Agent) -> Str:
    """Delegate the current task to the configured sub-agent and return its result."""
    logger.debug("Delegating to sub-agent '%s'", ctx.name)
    stop_out, _ = ctx.invoke(input=input)
    logger.debug("Sub-agent '%s' returned", ctx.name)
    text = (
        stop_out.value
        if stop_out.value is not None
        else SUBAGENT_NO_OUTCOME_PLACEHOLDER
    )
    return Str(value=text)
