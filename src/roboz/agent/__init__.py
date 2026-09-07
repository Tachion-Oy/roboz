"""Agent construction, execution, and delegation interfaces."""

from roboz.agent._execution_context import get_active_agent_stack
from roboz.agent.background_agent import (
    BackgroundAgentPhase,
    BackgroundAgentStatus,
    run_background_agent,
)
from roboz.agent.core import Agent
from roboz.agent.prompt_agent_tool import prompt_agent
from roboz.agent.subagent import run_subagent

__all__ = [
    "Agent",
    "BackgroundAgentPhase",
    "BackgroundAgentStatus",
    "get_active_agent_stack",
    "prompt_agent",
    "run_background_agent",
    "run_subagent",
]
