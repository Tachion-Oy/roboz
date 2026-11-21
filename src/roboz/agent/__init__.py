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
from roboz.agent.prompt_llm_tool import PromptLLMContext, prompt_llm
from roboz.agent.nested_agent import run_nested_agent
from roboz.agent.subagents import (
    SubagentsContext,
    read_shared_notes,
    start_subagents,
)
from roboz.models import AgentMode

__all__ = [
    "Agent",
    "AgentMode",
    "BackgroundAgentContext",
    "BackgroundAgentPhase",
    "BackgroundAgentState",
    "BackgroundAgentStatus",
    "SubagentsContext",
    "get_active_agent_stack",
    "PromptLLMContext",
    "prompt_llm",
    "read_shared_notes",
    "run_background_agent",
    "run_nested_agent",
    "start_subagents",
]
