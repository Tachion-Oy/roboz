"""Tool adapter for invoking one agent as another agent's sub-agent."""

from functools import partial
from logging import getLogger

from roboz.agent._notifications import SUBAGENT_NO_OUTCOME_PLACEHOLDER
from roboz.models import Empty, Message, Str
from roboz.tooling.context import Ctx, _prepare_context
from roboz.tooling.decorators import factory

logger = getLogger(__name__)


@factory
def run_subagent(input: Empty, messages: list[Message], ctx: Ctx) -> Str:
    """Delegate the current task to the configured sub-agent and return its result."""
    logger.debug("Delegating to sub-agent '%s'", ctx.agent.name)
    stop_out, _ = ctx.agent.invoke(input=input)
    logger.debug("Sub-agent '%s' returned", ctx.agent.name)
    text = (
        stop_out.value
        if stop_out.value is not None
        else SUBAGENT_NO_OUTCOME_PLACEHOLDER
    )
    return Str(value=text)


run_subagent._prepare_ctx = partial(_prepare_context, required=("agent",))
