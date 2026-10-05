"""Stable identifiers for the built-in agent tools.

Single source of truth for the built-in tool names that downstream packages
compare against (e.g. ``caller == PROMPT_USER_TOOL_NAME``). Change values here when
renaming the corresponding tool; never duplicate these strings as literals elsewhere.
Each value must match the actual ``tool.name`` (guarded by a test).
"""

from typing import Final

# --- Tools (``tool.name``) — imperative expressions `do` ---

PROMPT_LLM_TOOL_NAME: Final[str] = "prompt_llm"
RUN_BACKGROUND_AGENT_TOOL_NAME: Final[str] = "run_background_agent"
RUN_NESTED_AGENT_TOOL_NAME: Final[str] = "run_nested_agent"
