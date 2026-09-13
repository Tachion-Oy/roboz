"""Agent construction, execution, and delegation interfaces."""

from roboz.agent._execution_context import get_active_agent_stack
from roboz.agent.background_agent import (
    BackgroundAgentContext,
    BackgroundAgentPhase,
    BackgroundAgentState,
    BackgroundAgentStatus,
    run_background_agent,
)
from roboz.agent.core import Agent
from roboz.agent.prompt_agent_tool import PromptAgentContext, prompt_agent
from roboz.agent.subagent import run_subagent

__all__ = [
    "Agent",
    "BackgroundAgentContext",
    "BackgroundAgentPhase",
    "BackgroundAgentState",
    "BackgroundAgentStatus",
    "get_active_agent_stack",
    "PromptAgentContext",
    "prompt_agent",
    "run_background_agent",
    "run_subagent",
]
