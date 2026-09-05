"""Tool adapter for invoking one agent as another agent's sub-agent."""

from dataclasses import dataclass
from logging import getLogger

from roboz.agent.core import Agent
from roboz.agent._notifications import SUBAGENT_NO_OUTCOME_PLACEHOLDER
from roboz.models import Empty, Message, Str
from roboz.tooling.decorators import factory
from roboz.tooling.dependencies import FactoryCtx

logger = getLogger(__name__)


@dataclass(frozen=True)
class SubagentCtx(FactoryCtx):
    """Sub-agent bound as a live dependency of its wrapper tool."""

    agent: Agent


@factory
def run_subagent(input: Empty, messages: list[Message], ctx: SubagentCtx) -> Str:
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
